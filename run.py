"""Точка входа: сборка шины, модулей и HTTP-приложения.

Запуск:  uvicorn run:app --host 0.0.0.0 --port 5000
Перед первым запуском: alembic upgrade head (создаёт/обновляет БД).

Окружение:
  ST_DB_PATH   путь к SQLite (по умолчанию app.db)
  ST_CONFORM_URL
               address of the alignment daemon (track-muxer); empty — the conform module
               stays idle
  ST_READ_RECENT
               a stand only (it needs ST_STAND=1, or the process does not start): a table is read for the last this many
               episodes of each voice-over, so that a check does not load a site with hundreds of requests, and an old
               episode that is not in the table leaves the plan; the product leaves it unset and reads the whole page
  ST_QBIT_URL / ST_QBIT_USER / ST_QBIT_PASS
               qBittorrent; без ST_QBIT_URL модуль torrents не стартует
               (удобно для разработки без qBit)
  ST_APP_<NAME>
               a video application read through its API (modules/sources/apps): a JSON object with `label`,
               `protocol` and the private settings of the protocol; without it the application is not read

Все модули живут в одном asyncio-процессе (ТЗ, раздел 2.1); жизненным
циклом управляет lifespan FastAPI.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from assembly import Options, assemble
from core import Bus, Envelope, Runner
from core.db import Database
from core.logging import configure, get_logger
from modules.sources.apps import profiles_of
from modules.sources.players.alloha_session import profile_of as alloha_profile_of

configure()
log = get_logger("run")

bus = Bus()
db_path = os.environ.get("ST_DB_PATH", "app.db")
read_recent = int(os.environ["ST_READ_RECENT"]) if os.environ.get("ST_READ_RECENT", "").strip() else None
if read_recent is not None and os.environ.get("ST_STAND") != "1":
    # A table cut to the newest episodes drops the older ones from the plan, and their files with them.
    raise RuntimeError("ST_READ_RECENT limits what a site is read for and belongs to a stand only: it starts with ST_STAND=1")
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
# /docs и /openapi.json выключены в проде (Этап 4); для gen:api поднять с
# ST_DOCS_ENABLED=true (dev-инстанс или временно).
docs_enabled = os.environ.get("ST_DOCS_ENABLED", "false").lower() == "true"
# Проверка Host (Этап 2): домен + loopback. Пусто → проверки нет (dev).
_hosts = os.environ.get("ST_ALLOWED_HOSTS", "").strip()
allowed_hosts = [h.strip() for h in _hosts.split(",") if h.strip()] or None
gateway, modules = assemble(bus, db, Options(
    db_path=db_path, secret_key=secret_key, cookie_secure=cookie_secure, auth_required=auth_required,
    docs_enabled=docs_enabled, allowed_hosts=allowed_hosts, read_recent=read_recent,
    conform_url=os.environ.get("ST_CONFORM_URL", ""),
    qbit_url=os.environ.get("ST_QBIT_URL", ""), qbit_username=os.environ.get("ST_QBIT_USER", "admin"),
    qbit_password=os.environ.get("ST_QBIT_PASS", ""), apps=profiles_of(os.environ),
    alloha_profile=alloha_profile_of(os.environ)))

runner = Runner(bus, modules)


@asynccontextmanager
async def _lifespan(_app):
    await runner.start()
    # Whoever needs the whole system (the gateway reads the debug groups of logging) acts on this, not on a pause.
    bus.publish(Envelope(topic="system.started", kind="event"))
    log.info("система поднята")
    try:
        yield
    finally:
        await runner.stop()
        log.info("система остановлена")


app = gateway.app
app.router.lifespan_context = _lifespan
