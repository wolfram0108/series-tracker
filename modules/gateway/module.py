"""Gateway — the one HTTP/SSE entry point.

It carries the HTTP requests of the interface to queries and commands of the bus, and the events of the bus to SSE. It holds no business
logic (that is in the modules) and counts nothing of its own beyond the translation of the protocols: what it must count to answer is
in `calc/gateway`. Its work is the adaptation of protocols and the table of the topics to the names of SSE events.

SSE_MAP: the key is the record of the topic (core/topics.py), the value is an Outgoing (the name of the SSE event, the form of the
packet, the key of a picture). The stream of the interface is subscribed to exactly the topics of the table. The contract is
contracts/sse_contract.md (Р-18, Р-69).

Queries and commands: each goes by the record of its topic and its answer is read as the model of that record (`reply_of`); the gateway
makes its own answer of it and does not change what a module answered. Where the owner gives no model (the registry of the old torrent
pipeline, the profiles of the constructor listed), the answer is passed on as it came.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import signal
from typing import Any, Callable, Literal, NamedTuple

from fastapi import FastAPI, Request  # Request is the diagnostic route's
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from calc.gateway.flags import DEBUG_PREFIX, debug_groups
from calc.gateway.queues import agent_tasks
from calc.gateway.series import added_packet, updated_packet
from core import logging as core_logging
from core.contracts import (CompositionChanged, CompositionFailed, PackJournal, PlannerView, SeriesCard, SeriesDeleted,
                            SeriesDownloaded, SeriesReadProgress, SeriesStatusChanged, SeriesUpdated, SourceChanged, SourceEvent,
                            SseClients, TableFailed, TableProgress, TableReady)
from core.module import BaseModule, BusRequestError
from core.reasons import reason
from core.topics import (COMPOSITION_CHANGED, COMPOSITION_FAILED, COMPOSITION_SERIES_READING, CONFORM_QUEUE_CHANGED,
                         CONFORM_SETTINGS_CHANGED, DOWNLOADS_QUEUE_CHANGED, GATEWAY_SSE_CLIENTS, LIBRARY_RELOCATION_FINISHED,
                         LIBRARY_RELOCATION_STARTED, PLANNER_JOURNAL_CHANGED, PLANNER_PACK_CHANGED, PLANNER_SERIES_CHANGED,
                         SCAN_STATUS_CHANGED, SERIES_ADDED, SERIES_BUSY_CHANGED, SERIES_DELETED, SERIES_DOWNLOADED_CHANGED,
                         SERIES_SOURCE_ADDED, SERIES_SOURCE_REMOVED, SERIES_SOURCE_UPDATED, SERIES_STATUS_CHANGED, SERIES_UPDATED,
                         SETTINGS_VALUES_BY_PREFIX, SOURCES_PROVIDER_CHANGED, SOURCES_SETTINGS_CHANGED, SOURCES_TABLE_FAILED,
                         SOURCES_TABLE_FRESH, SOURCES_TABLE_PROGRESS, SOURCES_TABLE_READY, SYSTEM_STARTED, TORRENTS_PREVIEW_FAILED,
                         TORRENTS_PREVIEW_READY, TORRENTS_PROGRESS_CHANGED, TORRENTS_QUEUE_CHANGED, TORRENTS_RELEASE_CHANGED,
                         TORRENTS_STAGING_CHANGED, UNPACK_EDITOR_CHANGED, UNPACK_READ_PROGRESS, VOICES_SETTINGS_CHANGED, Topic)

from .routing import as_made


class AuthGateMiddleware:
    """The ASGI «lock»: everything but the login page and the static files of the front needs a valid session (else 401). Pure ASGI
    (not BaseHTTPMiddleware) so that the streaming SSE (/api/stream) is not broken: BaseHTTPMiddleware buffers a StreamingResponse. It is
    put INSIDE SessionMiddleware, so that scope['session'] is parsed already."""

    _PUBLIC_EXACT = {"/", "/legacy", "/v2", "/v2/", "/favicon.svg",
                     "/api/login", "/api/setup", "/api/auth/status"}
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
                                 "error": reason("login_required")}, status_code=401)
        await response(scope, receive, send)


class SecurityHeadersMiddleware:
    """The protective HTTP headers on every answer (stage 4). Pure ASGI — it adds the headers to http.response.start without buffering
    the body (SSE stays a stream). HSTS is not set here: it is for https and is set on nginx (stage 2)."""

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


# --- the forms of the packets of the stream ---------------------------------------------------------------------
# A form reads the payload of the event as the model of its record and tells it the way the interface takes it. A picture of a whole
# queue (a list of tasks) is told as its owner wrote it: its list is taken out, not read again (the cards were checked when made).

def _series_delta(payload: Any) -> dict:
    """series_updated — a delta instead of the whole card (Р-18).

    The front does Object.assign, which merges only the fields sent; `is_busy` must be in every event (finding 38 — on a falsy
    is_busy the front removes the spinner of saving)."""
    event = SeriesStatusChanged.model_validate(payload)
    return {"id": event.series_id, "statuses": event.statuses, "is_busy": event.is_busy}


def _series_downloaded(payload: Any) -> dict:
    """series_updated — a delta with the count of the downloaded alone (Д1): the front merges it into the card, the number is renewed
    without a reload."""
    event = SeriesDownloaded.model_validate(payload)
    return {"id": event.series_id, "downloaded_episodes_count": event.count}


def _bare_tasks(payload: Any) -> list:
    """The old contract of the queues — a bare list of tasks."""
    return payload["tasks"]


def _agent_tasks(payload: Any) -> list:
    """The queue of the torrent pipeline: the tasks of the old contract have the field `hash` (finding 39 — HTTP and SSE are the same)."""
    return agent_tasks(payload["tasks"])


def _journals(payload: Any) -> list:
    return payload["journals"]


def _series_updated(payload: Any) -> dict:
    """series.updated (catalog, Р-19): the fields applied and the obligatory statuses/is_busy — the delta for the Object.assign of the front."""
    return updated_packet(SeriesUpdated.model_validate(payload))


def _series_deleted(payload: Any) -> dict:
    return {"id": SeriesDeleted.model_validate(payload).series_id}


def _series_added(payload: Any) -> dict:
    """The whole card of the series (the contract of series_added); the date in ISO."""
    return added_packet(SeriesCard.model_validate(payload))


def _source_changed(what: str) -> Callable[[Any], dict]:
    """The packet of a source that was added, changed or removed: the event and what happened to the source."""
    def form(payload: Any) -> dict:
        return SourceChanged(**SourceEvent.model_validate(payload).model_dump(), what=what).model_dump(mode="json")
    return form


class Outgoing(NamedTuple):
    """What an event of the bus is in the stream of the interface."""
    name: str  # the SSE event
    form: Callable[[Any], Any] | None = None  # the packet made of the payload; None — the payload as it is
    # A picture of the whole state of its part of the screen, by the key of that part: of several queued at once only
    # the last one goes, since each replaces the one before. None — every event goes, in order.
    latest: Callable[[Any], Any] | None = None

    def packet(self, payload: Any) -> Any:
        """What the stream writes for the payload of an event."""
        return self.form(payload) if self.form else payload


def _whole(payload: Any) -> None:
    """The key of a picture of a whole queue: there is one."""
    return None


def _table_url(payload: Any) -> str:
    """The key of the progress of a read: the page."""
    return TableProgress.model_validate(payload).url


# The one table of the stream of the interface: the stream subscribes to the bus by its topics, makes the packets by it and
# keeps the last of each picture by it. The contract of every event — contracts/sse_contract.md.
# agent_heartbeat was removed by agreement (Р-18).
# The events of the sources, the layout and the references go as they are: a packet is whole for its part of
# the screen, so the interface asks nothing more.
SSE_MAP: dict[Topic, Outgoing] = {
    SERIES_STATUS_CHANGED: Outgoing("series_updated", _series_delta),
    SERIES_BUSY_CHANGED: Outgoing("series_updated", _series_delta),
    SERIES_DOWNLOADED_CHANGED: Outgoing("series_updated", _series_downloaded),
    SERIES_UPDATED: Outgoing("series_updated", _series_updated),
    SERIES_ADDED: Outgoing("series_added", _series_added),
    SERIES_DELETED: Outgoing("series_deleted", _series_deleted),
    TORRENTS_QUEUE_CHANGED: Outgoing("agent_queue_update", _agent_tasks, latest=_whole),
    TORRENTS_PROGRESS_CHANGED: Outgoing("torrent_progress_update", _bare_tasks, latest=_whole),
    TORRENTS_PREVIEW_READY: Outgoing("preview_naming_ready"),
    TORRENTS_PREVIEW_FAILED: Outgoing("preview_naming_failed"),
    DOWNLOADS_QUEUE_CHANGED: Outgoing("download_queue_update", _bare_tasks, latest=_whole),
    SCAN_STATUS_CHANGED: Outgoing("scanner_status_update"),
    SERIES_SOURCE_ADDED: Outgoing("series_sources_changed", _source_changed("added")),
    SERIES_SOURCE_UPDATED: Outgoing("series_sources_changed", _source_changed("updated")),
    SERIES_SOURCE_REMOVED: Outgoing("series_sources_changed", _source_changed("removed")),
    SOURCES_TABLE_PROGRESS: Outgoing("sources_table_progress", latest=_table_url),
    SOURCES_TABLE_READY: Outgoing("sources_table_ready"),
    # The term of the site of a page changed: the end of the term of its table moved.
    SOURCES_TABLE_FRESH: Outgoing("sources_table_fresh"),
    # The release of a torrent source and the editor of its page changed (stage 15): the tab «Торрент» shows them again.
    TORRENTS_RELEASE_CHANGED: Outgoing("torrent_release_update"),
    UNPACK_EDITOR_CHANGED: Outgoing("torrent_editor_update"),
    UNPACK_READ_PROGRESS: Outgoing("torrent_read_update"),
    SOURCES_TABLE_FAILED: Outgoing("sources_table_failed"),
    COMPOSITION_CHANGED: Outgoing("composition_changed"),
    COMPOSITION_FAILED: Outgoing("composition_failed"),
    COMPOSITION_SERIES_READING: Outgoing("series_read_progress"),
    CONFORM_QUEUE_CHANGED: Outgoing("conform_queue_update", latest=_whole),
    PLANNER_PACK_CHANGED: Outgoing("pack_queue_update", _bare_tasks, latest=_whole),
    PLANNER_JOURNAL_CHANGED: Outgoing("pack_journal_update", _journals, latest=_whole),
    PLANNER_SERIES_CHANGED: Outgoing("plan_update"),
    VOICES_SETTINGS_CHANGED: Outgoing("voice_settings_update"),
    SOURCES_SETTINGS_CHANGED: Outgoing("sources_settings_update"),
    TORRENTS_STAGING_CHANGED: Outgoing("torrent_staging_update"),
    CONFORM_SETTINGS_CHANGED: Outgoing("conform_settings_update"),
    SOURCES_PROVIDER_CHANGED: Outgoing("provider_update"),
    LIBRARY_RELOCATION_STARTED: Outgoing("relocation_started"),
    LIBRARY_RELOCATION_FINISHED: Outgoing("relocation_finished"),
}

# The names of the events of the stream, as the interface types them (from the one table).
SSE_EVENTS: tuple[str, ...] = tuple(dict.fromkeys(out.name for out in SSE_MAP.values()))

# The envelope carries the name of its topic: the entry of the table by that name.
_OUTGOING: dict[str, Outgoing] = {topic.name: out for topic, out in SSE_MAP.items()}


def latest_only(batch: list) -> list:
    """The envelopes of a batch in order, with each picture of SSE_MAP kept only as the last of its key."""
    seen: set = set()
    kept = []
    for env in reversed(batch):
        out = _OUTGOING.get(getattr(env, "topic", None))
        if out is not None and out.latest is not None:
            key = (env.topic, out.latest(env.payload or {}))
            if key in seen:
                continue
            seen.add(key)
        kept.append(env)
    return kept[::-1]


# The packets of the event stream that the interface types: their models go into the schema of the API.
EVENT_PACKETS = (PlannerView, PackJournal, TableProgress, TableReady, TableFailed, CompositionChanged,
                 CompositionFailed, SourceChanged, SeriesReadProgress)

_KEEPALIVE_SECONDS = 15.0
# The pace of the screen: after a frame, what comes within this time goes as the next frame, at once. A burst of telemetry
# (progress, the queues of a hundred works) then changes every card of a window twice a second, not on every turn of the bus.
FRAME_SECONDS = 0.5
# Put into a stream's queue to end it: uvicorn waits for open responses before it stops the app.
_STREAM_END = object()


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
        self._sse_subs: dict = {}
        self._restore_signals: list = []
        # The path to SQLite is for the administrator tab of the database alone (Р-22: a deliberate bypass of Р-7, a debugging
        # tool of the user).
        self.db_path = db_path
        # The key that signs the session cookie; empty → an ephemeral one (sessions are lost at a restart; production sets
        # ST_SECRET_KEY in run.py).
        self._secret_key = secret_key or secrets.token_hex(32)
        # A Secure cookie (https only); behind nginx with TLS it is True (stage 2).
        self._cookie_secure = cookie_secure
        # The «lock» is on in production; the tests of the business routes raise the gateway with auth_required=False (the login is
        # tested apart — test_auth.py).
        self._auth_required = auth_required
        # /docs and /openapi.json are off in production (stage 4); the flag ST_DOCS_ENABLED turns them on for development and the
        # generation of types (gen:api).
        self._docs_enabled = docs_enabled
        # The Hosts allowed (stage 2): None → no check (dev); in production the domain and the loopback (ST_ALLOWED_HOSTS), a guard
        # against host-header attacks.
        self._allowed_hosts = allowed_hosts
        super().__init__(bus)
        self.app = self._create_app()

    # SSE subscriptions are made per client connection in _sse_stream(); the one signal listened to
    # declaratively is "the system is up", which run.py sends.
    def register(self) -> None:
        self.handle(SYSTEM_STARTED, self.on_system_started)

    async def on_start(self) -> None:
        self._end_streams_on_stop_signal()

    async def on_system_started(self, env) -> None:
        """Debug groups of logging are read once every module is up: settings starts after the gateway,
        and waiting for it by retries would be a pause used as a sync."""
        await self._load_debug_groups()

    async def on_stop(self) -> None:
        for number, previous in self._restore_signals:
            signal.signal(number, previous)
        self._restore_signals.clear()

    def _end_streams_on_stop_signal(self) -> None:
        # The signal is the only notice before uvicorn starts waiting; the graceful-shutdown limit is the fuse.
        loop = asyncio.get_running_loop()
        for number in (signal.SIGINT, signal.SIGTERM):
            previous = signal.getsignal(number)

            def handler(signum, frame, previous=previous):
                loop.call_soon_threadsafe(self._end_streams)
                if callable(previous):
                    previous(signum, frame)

            try:
                signal.signal(number, handler)
            except ValueError:  # not the main thread: only the limit ends the streams
                return
            if previous is not None:
                self._restore_signals.append((number, previous))

    def _end_streams(self) -> None:
        for sub in list(self._sse_subs.values()):
            sub.queue.put_nowait(_STREAM_END)

    async def refresh_debug_groups(self) -> dict[str, str]:
        """The groups of logging that print debug lines are the settings of the window: read, and put into force. Returns the settings read."""
        flags = (await self.reply_of(SETTINGS_VALUES_BY_PREFIX, {"prefix": DEBUG_PREFIX})).values
        core_logging.set_debug_groups(debug_groups(flags))
        return flags

    async def _load_debug_groups(self) -> None:
        try:
            flags = await self.refresh_debug_groups()
        except (BusRequestError, asyncio.TimeoutError):
            self.log.warning("debug-группы логирования не загружены — "
                             "settings не ответил")
            return
        if groups := debug_groups(flags):
            self.log.info("DEBUG-логирование включено для групп: %s",
                          ", ".join(sorted(groups)))

    # --- the FastAPI application ----------------------------------------------

    @staticmethod
    def _describe_events(app: FastAPI) -> None:
        """The packets of the event stream are contracts too: they go into the schema next to the routes,
        so that the interface generates its types for them from the same models."""
        built = app.openapi

        def openapi() -> dict:
            if app.openapi_schema is None:
                schema = built()
                for model in EVENT_PACKETS:
                    described = model.model_json_schema(ref_template="#/components/schemas/{model}")
                    schema["components"]["schemas"].update(described.pop("$defs", {}))
                    schema["components"]["schemas"][model.__name__] = described
                # An event of a frame of a turn: its names are those of SSE_MAP, so the interface keeps no list of its own.
                schema["components"]["schemas"]["StreamEvent"] = {
                    "title": "StreamEvent", "type": "object", "required": ["event", "data"],
                    "properties": {"event": {"type": "string", "enum": list(SSE_EVENTS)}, "data": {}}}
                app.openapi_schema = schema
            return app.openapi_schema

        app.openapi = openapi

    def _create_app(self) -> FastAPI:
        # /docs and /openapi.json are off in production (stage 4: the map of the API is not shown outside). The flag docs_enabled
        # (ST_DOCS_ENABLED) turns them on for development and for the generation of the TS types (gen:api → openapi-typescript).
        app = FastAPI(
            title="Series Tracker", redoc_url=None,
            docs_url="/docs" if self._docs_enabled else None,
            openapi_url="/openapi.json" if self._docs_enabled else None)

        self._describe_events(app)

        @app.get("/api/stream")
        async def stream(frames: Literal["each", "batch"] = "each") -> StreamingResponse:
            return StreamingResponse(self._sse_stream(frames),
                                     media_type="text/event-stream")

        if self._diag:
            @app.post("/api/_diag/echo")
            async def diag_echo(request: Request) -> Response:
                body = await request.json()
                try:
                    # The one request whose topic is data: the diagnostic proxy asks the bus for any topic the body names.
                    result = await self.request(
                        body["topic"], body.get("payload"),
                        timeout=float(body.get("timeout", 5)))
                except BusRequestError as exc:
                    return JSONResponse({"error": str(exc)}, status_code=502)
                return await as_made({"reply": result})

        from .api_auth import build_router as auth_router
        from .api_media import build_router as media_router
        from .api_output import build_router as output_router
        from .api_series import build_router as series_router
        from .api_settings import build_router as settings_router
        from .api_sources import build_router as sources_router
        from .api_system import build_router as system_router
        from .api_torrents import build_router as torrents_router
        app.include_router(auth_router(self))
        app.include_router(series_router(self))
        app.include_router(sources_router(self))
        app.include_router(output_router(self))
        app.include_router(system_router(self))
        app.include_router(media_router(self))
        app.include_router(settings_router(self))
        app.include_router(torrents_router(self))

        legacy_index = os.path.join(self._templates_dir, "index.html")
        dist_index = os.path.join(self._web_dist_dir, "index.html")
        dist_favicon = os.path.join(self._web_dist_dir, "favicon.svg")
        has_dist = os.path.isdir(self._web_dist_dir)

        # Ф6 cutover (Р-Ф7): the root «/» is the NEW front (the Vite build), the old one is moved to «/legacy» for a rollback. Without
        # a build (no dist) the old front is served from the root, so that the stand is not left without a UI.
        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(dist_index if has_dist else legacy_index)

        # The old front (rollback): served at /legacy; its assets are at /static.
        @app.get("/legacy")
        async def legacy() -> FileResponse:
            return FileResponse(legacy_index)

        if os.path.isdir(self._static_dir):
            app.mount("/static", StaticFiles(directory=self._static_dir),
                      name="static")

        # The old address of the new front «/v2» → a redirect to the root.
        @app.get("/v2")
        @app.get("/v2/")
        async def v2_redirect() -> RedirectResponse:
            return RedirectResponse("/", status_code=307)

        # The assets of the new front (absolute paths, base="/"): /assets/* + the favicon.
        if has_dist:
            app.mount("/assets",
                      StaticFiles(directory=os.path.join(self._web_dist_dir,
                                                         "assets")),
                      name="assets")

            @app.get("/favicon.svg")
            async def favicon() -> FileResponse:
                return FileResponse(dist_favicon)

        # The lock is put INSIDE the session: add_middleware puts the wrapper at the start of the stack, so the SessionMiddleware
        # added last is the outer one and parses the cookie before AuthGate reads the session.
        if self._auth_required:
            app.add_middleware(AuthGateMiddleware)
        app.add_middleware(
            SessionMiddleware, secret_key=self._secret_key,
            session_cookie="st_session", same_site="strict",
            https_only=self._cookie_secure, max_age=7 * 24 * 3600)
        # the outermost — the headers on any answer (the 401 of the lock and the login too)
        app.add_middleware(SecurityHeadersMiddleware)
        # outer still — the check of the Host (a foreign Host is cut off before any logic)
        if self._allowed_hosts:
            app.add_middleware(TrustedHostMiddleware,
                               allowed_hosts=self._allowed_hosts)

        # Errors not caught go out without stack traces (stage 4); the details are in the log.
        @app.exception_handler(Exception)
        async def _unhandled(_request, _exc):  # noqa: ANN001
            self.log.exception("необработанная ошибка HTTP")
            return JSONResponse({"error": reason("server_error")},
                                status_code=500)

        return app

    # --- SSE: bus -> browser --------------------------------------------------

    async def _sse_batches(self):
        """The events of the stream by frames: what came within a frame of the screen (FRAME_SECONDS) is one list, each
        state once. An empty list is a quiet interval, for the keepalive."""
        # A broken connection is found by Starlette: it closes the generator (GeneratorExit), and the finally removes the subscription.
        # The stream is subscribed to the topics of its table and to nothing else: what is not for the interface does not
        # reach it, so nothing is sifted here.
        sub = self.bus.subscribe(*_OUTGOING)
        self._sse_subs[sub.sub_id] = sub
        self._sse_clients += 1
        # The count of the connections is a safeguard for the ephemeral states (Р-11): at count=0 the catalog drops every viewing.
        self.publish_event(GATEWAY_SSE_CLIENTS, SseClients(count=self._sse_clients).model_dump())
        self.log.info("SSE-клиент подключился (всего: %d)", self._sse_clients)
        loop = asyncio.get_running_loop()
        sent = loop.time() - FRAME_SECONDS
        try:
            while True:
                try:
                    env = await asyncio.wait_for(sub.queue.get(),
                                                 timeout=_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield []
                    continue
                # The first news after a quiet frame goes at once; what follows it within the frame joins it.
                batch = [env]
                while env is not _STREAM_END and (left := sent + FRAME_SECONDS - loop.time()) > 0:
                    try:
                        env = await asyncio.wait_for(sub.queue.get(), timeout=left)
                    except asyncio.TimeoutError:
                        break
                    batch.append(env)
                while not sub.queue.empty():
                    batch.append(sub.queue.get_nowait())
                sent = loop.time()
                events = []
                for env in latest_only(batch):
                    if env is _STREAM_END:
                        if events:
                            yield events
                        return
                    events.append((_OUTGOING[env.topic].name, _OUTGOING[env.topic].packet(env.payload)))
                if events:
                    yield events
        finally:
            self._sse_subs.pop(sub.sub_id, None)
            self.bus.unsubscribe(sub)
            self._sse_clients -= 1
            self.publish_event(GATEWAY_SSE_CLIENTS, SseClients(count=self._sse_clients).model_dump())
            self.log.info("SSE-клиент отключился (осталось: %d)",
                          self._sse_clients)

    async def _sse_stream(self, frames: str = "each"):
        """The stream as frames: `each` — a frame per event; `batch` — one frame for what came within FRAME_SECONDS, so that a burst of events is
        taken by the interface at once and its screen is counted once, not once per event."""
        async with contextlib.aclosing(self._sse_batches()) as turns:
            async for events in turns:
                if not events:
                    yield ": keepalive\n\n"  # a comment keeps the connection alive through proxies
                elif frames == "batch":
                    data = json.dumps([{"event": name, "data": payload} for name, payload in events], ensure_ascii=False)
                    yield f"event: batch\ndata: {data}\n\n"
                else:
                    for name, payload in events:
                        yield f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
