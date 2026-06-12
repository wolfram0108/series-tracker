"""Gateway — единственная HTTP/SSE-точка входа.

Транслирует HTTP-запросы фронта в query/command шины и события шины —
в SSE. Сам не содержит бизнес-логики (она в модулях); его логика —
только адаптация протоколов и таблица соответствия топиков SSE-именам.

SSE_MAP: ключ — топик шины, значение — (имя SSE-события, трансформация
payload). Контракт — contracts/sse_contract.md (Р-18); series_added и
series_deleted добавятся при ревизии CRUD сериалов.
"""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import FastAPI, Request  # Request нужен diag-роуту
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core import BaseModule, BusRequestError


def _series_delta(p: dict) -> dict:
    """series_updated — дельта вместо полного объекта серии (Р-18).

    Object.assign на фронте сливает только присланные поля; is_busy
    обязан присутствовать в каждом событии (находка 38 — по falsy
    is_busy фронт снимает спиннер сохранения)."""
    return {"id": p["series_id"], "statuses": p["statuses"],
            "is_busy": p["is_busy"]}


def _bare_tasks(p: dict) -> list:
    """Старый контракт очередей — голый массив задач."""
    return p["tasks"]


def _agent_tasks(p: dict) -> list:
    """Очередь торрент-конвейера: у задач старого контракта поле hash
    (находка 39 — HTTP и SSE выборки выровнены)."""
    return [{"hash": t.get("torrent_hash"), **t} for t in p["tasks"]]


def _series_update_fields(p: dict) -> dict:
    """series.updated (catalog, Р-19): применённые поля + обязательные
    statuses/is_busy — дельта для Object.assign фронта."""
    return {"id": p["series_id"],
            **{k: v for k, v in p.items() if k != "series_id"}}


def _deleted_id(p: dict) -> dict:
    return {"id": p["series_id"]}


def _series_added(p: dict) -> dict:
    """Полный объект серии (контракт series_added); дата — в ISO."""
    out = dict(p)
    if isinstance(out.get("last_scan_time"), str):
        out["last_scan_time"] = out["last_scan_time"].replace(" ", "T", 1)
    return out


# Топик шины -> (имя SSE-события, трансформация payload | None=как есть).
# Контракт и решения по каждому событию — contracts/sse_contract.md (Р-18).
# agent_heartbeat удалён по согласованию (Р-18).
SSE_MAP: dict[str, tuple[str, object]] = {
    "series.status.changed": ("series_updated", _series_delta),
    "series.busy.changed": ("series_updated", _series_delta),
    "series.updated": ("series_updated", _series_update_fields),
    "series.added": ("series_added", _series_added),
    "series.deleted": ("series_deleted", _deleted_id),
    "torrents.queue.changed": ("agent_queue_update", _agent_tasks),
    "downloads.queue.changed": ("download_queue_update", _bare_tasks),
    "slicing.queue.changed": ("slicing_queue_update", _bare_tasks),
    "scan.status.changed": ("scanner_status_update", None),
    "renaming.finished": ("renaming_complete", None),
    "library.relocation.started": ("relocation_started", None),
    "library.relocation.finished": ("relocation_finished", None),
}

_KEEPALIVE_SECONDS = 15.0


class GatewayModule(BaseModule):
    name = "gateway"

    def __init__(self, bus, *, static_dir: str = "static",
                 templates_dir: str = "templates", diag: bool = False,
                 db_path: str = "app.db") -> None:
        self._static_dir = static_dir
        self._templates_dir = templates_dir
        self._diag = diag
        self._sse_clients = 0
        # путь к SQLite — только для админ-вкладки БД (Р-22: сознательный
        # обход Р-7, отладочный инструмент пользователя)
        self.db_path = db_path
        super().__init__(bus)
        self.app = self._create_app()

    # Gateway ничего не слушает декларативно: SSE-подписки создаются
    # на каждое клиентское соединение в _sse_stream().
    def register(self) -> None:
        pass

    async def on_start(self) -> None:
        # Начальная загрузка debug-групп логирования (Р-22): settings
        # может стартовать позже gateway — добираем с повторами.
        self._tasks.append(asyncio.create_task(self._load_debug_groups()))

    async def _load_debug_groups(self) -> None:
        from core import logging as core_logging
        for _ in range(20):
            try:
                reply = await self.request("settings.values.by_prefix",
                                           {"prefix": "debug_enabled_"},
                                           timeout=5)
            except BusRequestError:
                await asyncio.sleep(0.5)
                continue
            groups = {k.removeprefix("debug_enabled_")
                      for k, v in reply["values"].items() if v == "true"}
            core_logging.set_debug_groups(groups)
            if groups:
                self.log.info("DEBUG-логирование включено для групп: %s",
                              ", ".join(sorted(groups)))
            return
        self.log.warning("debug-группы логирования не загружены — "
                         "settings так и не ответил")

    # --- FastAPI-приложение -------------------------------------------------

    def _create_app(self) -> FastAPI:
        app = FastAPI(title="Series Tracker", docs_url=None, redoc_url=None)

        @app.get("/api/stream")
        async def stream() -> StreamingResponse:
            return StreamingResponse(self._sse_stream(),
                                     media_type="text/event-stream")

        if self._diag:
            @app.post("/api/_diag/echo")
            async def diag_echo(request: Request) -> JSONResponse:
                body = await request.json()
                try:
                    result = await self.request(
                        body["topic"], body.get("payload"),
                        timeout=float(body.get("timeout", 5)))
                except BusRequestError as exc:
                    return JSONResponse({"error": str(exc)}, status_code=502)
                return JSONResponse({"reply": result})

        from .api_media import build_router as media_router
        from .api_series import build_router as series_router
        from .api_settings import build_router as settings_router
        from .api_system import build_router as system_router
        app.include_router(series_router(self))
        app.include_router(system_router(self))
        app.include_router(media_router(self))
        app.include_router(settings_router(self))

        index_path = os.path.join(self._templates_dir, "index.html")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(index_path)

        if os.path.isdir(self._static_dir):
            app.mount("/static", StaticFiles(directory=self._static_dir),
                      name="static")
        return app

    # --- SSE: шина -> браузер -------------------------------------------------

    async def _sse_stream(self):
        # Разрыв соединения обнаруживает Starlette: он закрывает генератор
        # (GeneratorExit), и finally снимает подписку.
        sub = self.bus.subscribe("#")
        self._sse_clients += 1
        # Счётчик подключений — страховка эфемерных состояний (Р-11):
        # при count=0 catalog сбрасывает все viewing.
        self.publish_event("gateway.sse.clients", {"count": self._sse_clients})
        self.log.info("SSE-клиент подключился (всего: %d)", self._sse_clients)
        try:
            while True:
                try:
                    env = await asyncio.wait_for(sub.queue.get(),
                                                 timeout=_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    # Комментарий держит соединение живым через прокси.
                    yield ": keepalive\n\n"
                    continue
                if env.kind != "event":
                    continue
                mapping = SSE_MAP.get(env.topic)
                if mapping is None:
                    continue
                sse_name, transform = mapping
                payload = transform(env.payload) if transform else env.payload
                data = json.dumps(payload, ensure_ascii=False)
                yield f"event: {sse_name}\ndata: {data}\n\n"
        finally:
            self.bus.unsubscribe(sub)
            self._sse_clients -= 1
            self.publish_event("gateway.sse.clients",
                               {"count": self._sse_clients})
            self.log.info("SSE-клиент отключился (осталось: %d)",
                          self._sse_clients)
