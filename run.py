"""Entry point: assembles the bus, the modules and the HTTP application.

Run:    uvicorn run:app --host 0.0.0.0 --port 5000
Before the first run: alembic upgrade head (creates or updates the DB).

Environment:
  ST_DB_PATH   path to SQLite (default app.db)
  ST_CONFORM_URL
               address of the alignment daemon (track-muxer); empty — the conform module
               stays idle
  ST_READ_RECENT
               stand only (requires ST_STAND=1, otherwise the process does not start): the table is read for the
               last N episodes of each voice-over, so a check does not load the site with hundreds of requests; an
               old episode missing from the table leaves the plan. The product leaves it unset and reads the whole page
  ST_QBIT_URL / ST_QBIT_USER / ST_QBIT_PASS
               qBittorrent; without ST_QBIT_URL the torrents module does not start
               (convenient for development without qBit)
  ST_PROVIDER_URL / ST_PROVIDER_TOKEN
               the provider of the socket of external sources (contracts/provider_socket.md): its address and the
               token its requests carry; without an address there is no socket and the service reads what it has itself

All modules live in one asyncio process (technical spec, section 2.1); the
lifespan of FastAPI manages their lifecycle.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from assembly import Options, assemble
from core import proc
from core.bus import Bus
from core.envelope import Envelope
from core.runner import Runner
from core.db import Database
from core.logging import configure, get_logger
from core.loopwatch import LoopWatch, logged
from core.schema import require_head
from core.topics import SYSTEM_STARTED
from modules.sources.players.alloha_session import profile_of as alloha_profile_of

configure()
log = get_logger("run")

bus = Bus()
db_path = os.environ.get("ST_DB_PATH", "app.db")
read_recent = int(os.environ["ST_READ_RECENT"]) if os.environ.get("ST_READ_RECENT", "").strip() else None
if read_recent is not None and os.environ.get("ST_STAND") != "1":
    # A table cut to the newest episodes drops the older ones from the plan, and their files with them.
    raise RuntimeError("ST_READ_RECENT limits what a site is read for and belongs to a stand only: it starts with ST_STAND=1")
# The code runs only on the schema its migrations make: a restart before `alembic upgrade head` stops here with what to do.
require_head(db_path)
db = Database(db_path)

# Signing key of the login session cookie; it must be stable (from .env), or logins drop on restart.
# See docs/security.md, stage 1.
secret_key = os.environ.get("ST_SECRET_KEY", "")
if not secret_key:
    log.warning("ST_SECRET_KEY не задан — сессии входа будут сбрасываться "
                "при рестарте; задайте ST_SECRET_KEY в .env")
# Secure cookies are sent over https only: true behind nginx with TLS; for local http set ST_COOKIE_SECURE=false.
# Use that only until nginx is configured.
cookie_secure = os.environ.get("ST_COOKIE_SECURE", "true").lower() != "false"
# Staged rollout: the login code can ship with the lock switched off (ST_AUTH_REQUIRED=false) while the
# front end cannot log in yet; one flag enables it later. On by default for safety.
auth_required = os.environ.get("ST_AUTH_REQUIRED", "true").lower() != "false"
# /docs and /openapi.json are off in production (stage 4); gen:api needs ST_DOCS_ENABLED=true
# on a dev instance or temporarily.
docs_enabled = os.environ.get("ST_DOCS_ENABLED", "false").lower() == "true"
# Host header check: the domain plus loopback; empty means no check (dev).
_hosts = os.environ.get("ST_ALLOWED_HOSTS", "").strip()
allowed_hosts = [h.strip() for h in _hosts.split(",") if h.strip()] or None
gateway, modules = assemble(bus, db, Options(
    db_path=db_path, secret_key=secret_key, cookie_secure=cookie_secure, auth_required=auth_required,
    docs_enabled=docs_enabled, allowed_hosts=allowed_hosts, read_recent=read_recent,
    conform_url=os.environ.get("ST_CONFORM_URL", ""),
    qbit_url=os.environ.get("ST_QBIT_URL", ""), qbit_username=os.environ.get("ST_QBIT_USER", "admin"),
    qbit_password=os.environ.get("ST_QBIT_PASS", ""),
    alloha_profile=alloha_profile_of(os.environ), provider_url=os.environ.get("ST_PROVIDER_URL", "").strip(),
    provider_token=os.environ.get("ST_PROVIDER_TOKEN", "")))

runner = Runner(bus, modules)


@asynccontextmanager
async def _lifespan(_app):
    watch = LoopWatch(asyncio.get_running_loop(), logged(get_logger("loopwatch")))
    watch.start()  # before the modules: the stalls of their start are seen too
    proc.isolate()  # before any program starts: each lives in its own group of memory under the service
    await runner.start()
    # Whoever needs the whole system (the gateway reads the debug groups of logging) acts on this, not on a pause.
    bus.publish(Envelope(topic=SYSTEM_STARTED.name, kind="event"))
    log.info("система поднята")
    try:
        yield
    finally:
        await runner.stop()
        watch.stop()
        log.info("система остановлена")


app = gateway.app
app.router.lifespan_context = _lifespan
