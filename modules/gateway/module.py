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
import secrets

from fastapi import FastAPI, Request  # Request нужен diag-роуту
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from core import BaseModule, BusRequestError


class AuthGateMiddleware:
    """ASGI-«замок»: всё, кроме страницы входа и статики фронта, требует
    валидную сессию (иначе 401). Чистый ASGI (не BaseHTTPMiddleware) —
    чтобы не сломать потоковый SSE (/api/stream): BaseHTTPMiddleware
    буферизует StreamingResponse. Ставится ВНУТРИ SessionMiddleware,
    поэтому scope['session'] уже распарсен."""

    _PUBLIC_EXACT = {"/", "/legacy", "/v2", "/v2/", "/favicon.svg",
                     "/api/login"}
    _PUBLIC_PREFIX = ("/assets/", "/static/")

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        session = scope.get("session") or {}
        if (path in self._PUBLIC_EXACT or path.startswith(self._PUBLIC_PREFIX)
                or session.get("user")):
            await self.app(scope, receive, send)
            return
        response = JSONResponse({"authenticated": False,
                                 "error": "Требуется вход"}, status_code=401)
        await response(scope, receive, send)


class SecurityHeadersMiddleware:
    """Защитные HTTP-заголовки на все ответы (Этап 4). Чистый ASGI —
    дописывает заголовки в http.response.start, не буферизуя тело (SSE
    остаётся потоковым). HSTS не ставим здесь — он для https и задаётся на
    nginx (Этап 2)."""

    _HEADERS = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"same-origin"),
    ]

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {k.lower() for k, _ in headers}
                for key, value in self._HEADERS:
                    if key not in present:
                        headers.append((key, value))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _series_delta(p: dict) -> dict:
    """series_updated — дельта вместо полного объекта серии (Р-18).

    Object.assign на фронте сливает только присланные поля; is_busy
    обязан присутствовать в каждом событии (находка 38 — по falsy
    is_busy фронт снимает спиннер сохранения)."""
    return {"id": p["series_id"], "statuses": p["statuses"],
            "is_busy": p["is_busy"]}


def _series_downloaded(p: dict) -> dict:
    """series_updated — дельта только со счётчиком скачанного (Д1): фронт
    сливает в карточку, число обновляется без перезагрузки."""
    return {"id": p["series_id"],
            "downloaded_episodes_count": p["count"]}


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
    "series.downloaded.changed": ("series_updated", _series_downloaded),
    "series.updated": ("series_updated", _series_update_fields),
    "series.added": ("series_added", _series_added),
    "series.deleted": ("series_deleted", _deleted_id),
    "torrents.queue.changed": ("agent_queue_update", _agent_tasks),
    "torrents.progress.changed": ("torrent_progress_update", _bare_tasks),
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

    def __init__(self, bus, *, static_dir: str = "legacy_frontend/static",
                 templates_dir: str = "legacy_frontend/templates",
                 diag: bool = False, db_path: str = "app.db",
                 web_dist_dir: str = "web/dist",
                 secret_key: str = "", cookie_secure: bool = True,
                 auth_required: bool = True, docs_enabled: bool = False,
                 allowed_hosts: list[str] | None = None) -> None:
        self._static_dir = static_dir
        self._templates_dir = templates_dir
        self._web_dist_dir = web_dist_dir
        self._diag = diag
        self._sse_clients = 0
        # путь к SQLite — только для админ-вкладки БД (Р-22: сознательный
        # обход Р-7, отладочный инструмент пользователя)
        self.db_path = db_path
        # ключ подписи сессионной куки; пустой → эфемерный (сессии слетят
        # при рестарте — для прода задаётся ST_SECRET_KEY в run.py).
        self._secret_key = secret_key or secrets.token_hex(32)
        # Secure-кука (только по https); за nginx с TLS — True (Этап 2).
        self._cookie_secure = cookie_secure
        # «Замок» включён в проде; тесты бизнес-роутов поднимают gateway с
        # auth_required=False (вход проверяется отдельно — test_auth.py).
        self._auth_required = auth_required
        # /docs и /openapi.json — выключены в проде (Этап 4); включаются
        # флагом ST_DOCS_ENABLED для разработки/генерации типов (gen:api).
        self._docs_enabled = docs_enabled
        # Разрешённые Host (Этап 2): None → проверки нет (dev); в проде —
        # домен + loopback (ST_ALLOWED_HOSTS), защита от host-header атак.
        self._allowed_hosts = allowed_hosts
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
        # /docs и /openapi.json — выключены в проде (Этап 4: не раскрывать
        # карту API наружу). Включаются флагом docs_enabled (ST_DOCS_ENABLED)
        # для разработки и генерации TS-типов (gen:api → openapi-typescript).
        app = FastAPI(
            title="Series Tracker", redoc_url=None,
            docs_url="/docs" if self._docs_enabled else None,
            openapi_url="/openapi.json" if self._docs_enabled else None)

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

        from .api_auth import build_router as auth_router
        from .api_media import build_router as media_router
        from .api_series import build_router as series_router
        from .api_settings import build_router as settings_router
        from .api_system import build_router as system_router
        app.include_router(auth_router(self))
        app.include_router(series_router(self))
        app.include_router(system_router(self))
        app.include_router(media_router(self))
        app.include_router(settings_router(self))

        legacy_index = os.path.join(self._templates_dir, "index.html")
        dist_index = os.path.join(self._web_dist_dir, "index.html")
        dist_favicon = os.path.join(self._web_dist_dir, "favicon.svg")
        has_dist = os.path.isdir(self._web_dist_dir)

        # Ф6 cutover (Р-Ф7): корень «/» — НОВЫЙ фронт (Vite-сборка), старый
        # уведён на «/legacy» для отката. Если сборки нет (dist отсутствует) —
        # падаем обратно на старый фронт с корня, чтобы стенд не остался без UI.
        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(dist_index if has_dist else legacy_index)

        # Старый фронт (rollback): отдаём по /legacy; его ассеты — /static.
        @app.get("/legacy")
        async def legacy() -> FileResponse:
            return FileResponse(legacy_index)

        if os.path.isdir(self._static_dir):
            app.mount("/static", StaticFiles(directory=self._static_dir),
                      name="static")

        # Старый адрес нового фронта «/v2» → редирект на корень.
        @app.get("/v2")
        @app.get("/v2/")
        async def v2_redirect() -> RedirectResponse:
            return RedirectResponse("/", status_code=307)

        # Ассеты нового фронта (абсолютные пути base="/"): /assets/* + favicon.
        if has_dist:
            app.mount("/assets",
                      StaticFiles(directory=os.path.join(self._web_dist_dir,
                                                         "assets")),
                      name="assets")

            @app.get("/favicon.svg")
            async def favicon() -> FileResponse:
                return FileResponse(dist_favicon)

        # Замок ставится ВНУТРИ сессии: add_middleware кладёт обёртку в
        # начало стека, поэтому добавленный последним SessionMiddleware —
        # внешний и распарсит куку до того, как AuthGate прочитает session.
        if self._auth_required:
            app.add_middleware(AuthGateMiddleware)
        app.add_middleware(
            SessionMiddleware, secret_key=self._secret_key,
            session_cookie="st_session", same_site="strict",
            https_only=self._cookie_secure, max_age=7 * 24 * 3600)
        # самый внешний — заголовки на любой ответ (вкл. 401 замка и логин)
        app.add_middleware(SecurityHeadersMiddleware)
        # ещё внешнее — проверка Host (отсекаем чужой Host до всякой логики)
        if self._allowed_hosts:
            app.add_middleware(TrustedHostMiddleware,
                               allowed_hosts=self._allowed_hosts)

        # Непойманные ошибки наружу — без стек-трейсов (Этап 4); детали — в лог.
        @app.exception_handler(Exception)
        async def _unhandled(_request, _exc):  # noqa: ANN001
            self.log.exception("необработанная ошибка HTTP")
            return JSONResponse({"error": "Внутренняя ошибка сервера"},
                                status_code=500)

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
