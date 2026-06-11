"""Точка входа: сборка шины, модулей и HTTP-приложения.

Запуск:  uvicorn run:app --host 0.0.0.0 --port 5000
Все модули живут в одном asyncio-процессе (см. ТЗ, раздел 2.1);
жизненным циклом управляет lifespan FastAPI.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from core import Bus, Runner
from core.logging import configure, get_logger
from modules.gateway import GatewayModule

configure()
log = get_logger("run")

bus = Bus()
gateway = GatewayModule(bus)

# Сюда по мере этапов добавляются остальные модули (catalog, scan, ...).
runner = Runner(bus, [gateway])


@asynccontextmanager
async def _lifespan(_app):
    await runner.start()
    log.info("система поднята")
    try:
        yield
    finally:
        await runner.stop()
        log.info("система остановлена")


app = gateway.app
app.router.lifespan_context = _lifespan
