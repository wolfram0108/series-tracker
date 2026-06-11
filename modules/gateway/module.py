"""Gateway — единственная HTTP/SSE-точка входа.

Транслирует HTTP-запросы фронта в query/command шины и события шины —
в SSE. Сам не содержит бизнес-логики (она в модулях); его логика —
только адаптация протоколов и таблица соответствия топиков SSE-именам.

SSE_MAP заполняется по мере ревизии контракта (этап 5): ключ — топик
шины, значение — имя SSE-события для фронта.
"""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import FastAPI, Request  # Request нужен diag-роуту
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core import BaseModule, BusRequestError

# Топик шины -> имя SSE-события. Источник имён — contracts/sse_contract.md.
SSE_MAP: dict[str, str] = {}

_KEEPALIVE_SECONDS = 15.0


class GatewayModule(BaseModule):
    name = "gateway"

    def __init__(self, bus, *, static_dir: str = "static",
                 templates_dir: str = "templates", diag: bool = False) -> None:
        self._static_dir = static_dir
        self._templates_dir = templates_dir
        self._diag = diag
        self._sse_clients = 0
        super().__init__(bus)
        self.app = self._create_app()

    # Gateway ничего не слушает декларативно: SSE-подписки создаются
    # на каждое клиентское соединение в _sse_stream().
    def register(self) -> None:
        pass

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
                sse_name = SSE_MAP.get(env.topic)
                if sse_name is None:
                    continue
                data = json.dumps(env.payload, ensure_ascii=False)
                yield f"event: {sse_name}\ndata: {data}\n\n"
        finally:
            self.bus.unsubscribe(sub)
            self._sse_clients -= 1
            self.publish_event("gateway.sse.clients",
                               {"count": self._sse_clients})
            self.log.info("SSE-клиент отключился (осталось: %d)",
                          self._sse_clients)
