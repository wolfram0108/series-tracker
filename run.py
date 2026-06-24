"""Точка входа: сборка шины, модулей и HTTP-приложения.

Запуск:  uvicorn run:app --host 0.0.0.0 --port 5000
Перед первым запуском: alembic upgrade head (создаёт/обновляет БД).

Окружение:
  ST_DB_PATH   путь к SQLite (по умолчанию app.db)
  ST_QBIT_URL / ST_QBIT_USER / ST_QBIT_PASS
               qBittorrent; без ST_QBIT_URL модуль torrents не стартует
               (удобно для разработки без qBit)

Все модули живут в одном asyncio-процессе (ТЗ, раздел 2.1); жизненным
циклом управляет lifespan FastAPI.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from core import Bus, Runner
from core.db import Database
from core.logging import configure, get_logger
from modules.auth import AuthModule
from modules.catalog import CatalogModule
from modules.downloads import DownloadsModule
from modules.gateway import GatewayModule
from modules.library import LibraryModule
from modules.metadata import MetadataModule
from modules.renaming import RenamingModule
from modules.rules import RulesModule
from modules.scan import ScanModule
from modules.settings import SettingsModule
from modules.slicing import SlicingModule
from modules.sources import SourcesModule
from modules.torrents import TorrentsModule
from modules.trackerauth import TrackerauthModule

configure()
log = get_logger("run")

bus = Bus()
db_path = os.environ.get("ST_DB_PATH", "app.db")
db = Database(db_path)

# Ключ подписи сессионной куки входа: стабильный из .env, иначе сессии
# слетают при рестарте (см. docs/security.md, Этап 1).
secret_key = os.environ.get("ST_SECRET_KEY", "")
if not secret_key:
    log.warning("ST_SECRET_KEY не задан — сессии входа будут сбрасываться "
                "при рестарте; задайте ST_SECRET_KEY в .env")
# Secure-куки (только https). За nginx с TLS — true; для входа по http
# (локально, до настройки nginx) временно ST_COOKIE_SECURE=false.
cookie_secure = os.environ.get("ST_COOKIE_SECURE", "true").lower() != "false"
# Поэтапный выкат: код входа можно задеплоить с ВЫКЛЮЧЕННЫМ замком
# (ST_AUTH_REQUIRED=false), пока фронт не умеет логиниться, и включить
# позже одним флагом. По умолчанию замок включён (безопасно).
auth_required = os.environ.get("ST_AUTH_REQUIRED", "true").lower() != "false"
gateway = GatewayModule(bus, db_path=db_path, secret_key=secret_key,
                        cookie_secure=cookie_secure,
                        auth_required=auth_required)

modules = [
    gateway,
    CatalogModule(bus, db),
    SettingsModule(bus, db),
    AuthModule(bus, db),
    RulesModule(bus, db),
    TrackerauthModule(bus, db),
    SourcesModule(bus, db),
    MetadataModule(bus, db),
    LibraryModule(bus, db),
    # scan/downloads/renaming — после catalog/settings/sources/rules:
    # их reconcile при старте шлёт запросы соседям.
    ScanModule(bus, db),
    DownloadsModule(bus, db),
    RenamingModule(bus, db),
    SlicingModule(bus, db),
]

# env имеет приоритет (стенд/тесты); без env torrents возьмёт креды
# qbittorrent из таблицы auth через шину (Р-22) и переподключится по
# событию trackerauth.credentials.changed — рестарт не нужен.
modules.append(TorrentsModule(
    bus, db, qbt_url=os.environ.get("ST_QBIT_URL", ""),
    qbt_username=os.environ.get("ST_QBIT_USER", "admin"),
    qbt_password=os.environ.get("ST_QBIT_PASS", "")))

runner = Runner(bus, modules)


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
