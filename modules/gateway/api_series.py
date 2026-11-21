"""HTTP points of the series block (Р-19) — the old contract adapted to the bus.

The shapes of the answers repeat the old routes of routes/series.py; all the logic is in the modules, here are only the moving of the
fields and the routing of the errors of the bus to HTTP codes (404 — no such entity, 409 — busy, 400 — a refusal of validation).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from calc.gateway.series import downloaded_total, series_object
from core.module import BusRequestError
from core.reasons import code_of, reason, relayed
from core.topics import (CATALOG_SERIES_CREATE, CATALOG_SERIES_DELETE, CATALOG_SERIES_GET, CATALOG_SERIES_LIST,
                         CATALOG_SERIES_UPDATE, CATALOG_VIEWING_START, CATALOG_VIEWING_STOP, LIBRARY_RELOCATION_GET,
                         LIBRARY_RELOCATION_PLAN, METADATA_MAP_GET, METADATA_MAP_LIST, METADATA_MAP_SET,
                         PLANNER_DOWNLOADED_COUNTS, PLANNER_FAILURES_FORGET, SCAN_RUN_CANCEL, SCAN_SERIES_RUN,
                         TORRENTS_DB_DOWNLOADED_COUNTS, TORRENTS_DB_PROGRESS_LIST, TORRENTS_FS_VERIFY, Topic)

from .routing import admitted
from .schemas import (CreatedSeries, ErrorResponse, FailuresReset, FailuresResetBody, OkResponse, SyncCancelled, SyncScopeBody,
                      QueueTask, SeriesCreate, SeriesObject, SeriesUpdate)

# The common documentation of the error branches (they go out through JSONResponse, so the response_model does not cover them).
_ERRORS = {code: {"model": ErrorResponse} for code in (400, 404, 409, 500)}

# Refusals of a state that does not allow the change: the table of what is allowed, or a relocation under way.
_CONFLICT_CODES = frozenset({"not_allowed", "move_in_progress"})


def _error_response(exc: BusRequestError, *, with_success: bool = True):
    msg = str(exc)
    if "LookupError" in msg:
        code, text = 404, reason("series_not_found")
    elif code_of(msg) in _CONFLICT_CODES:
        code, text = 409, relayed(msg)
    elif "LibraryError" in msg:
        code, text = 400, relayed(msg)
    elif "ValueError" in msg:
        code, text = 400, relayed(msg)
    else:
        code, text = 500, relayed(msg)
    body = {"success": False, "error": text} if with_success \
        else {"error": text}
    return JSONResponse(body, status_code=code)


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    async def _counts(topic: Topic) -> dict:
        """The counts of the downloaded; a module may be down (torrents without qBit) — then its count is empty and the cards go on."""
        try:
            return (await gw.reply_of(topic, {})).counts
        except BusRequestError:
            return {}

    # --- the list and the cards -----------------------------------------------

    @r.get("/api/series", response_model=list[SeriesObject])
    async def list_series():
        series = (await gw.reply_of(CATALOG_SERIES_LIST, {})).series
        mappings = {m.series_id: m for m in (await gw.reply_of(METADATA_MAP_LIST, {})).mappings}
        counts = [await _counts(PLANNER_DOWNLOADED_COUNTS), await _counts(TORRENTS_DB_DOWNLOADED_COUNTS)]
        return [series_object(s, mappings.get(s.id), downloaded_episodes_count=downloaded_total(counts, s.id)) for s in series]

    @r.post("/api/series", response_model=CreatedSeries, responses=_ERRORS)
    async def add_series(body: SeriesCreate):
        data = body.model_dump(mode="json", exclude={"tmdb_data"}, exclude_unset=True)
        try:
            series = await gw.reply_of(CATALOG_SERIES_CREATE, {"data": data})
        except BusRequestError as exc:
            return _error_response(exc)
        series_id = series.id
        if body.tmdb_data:
            await gw.reply_of(METADATA_MAP_SET, {"series_id": series_id, "tmdb_data": body.tmdb_data})
        return {"success": True, "series_id": series_id}

    # IMPORTANT: the static path is registered before /{series_id}.
    @r.get("/api/series/active_torrents", response_model=list[QueueTask])
    async def active_torrents():
        """The progress of the torrent downloads with the names of the series (the table «Мониторинг» of the agents tab;
        Р-23 — the fix of the mapping of block 2: the source is torrent_progress of the module torrents, not the queue of the pipeline)."""
        try:
            reply = await gw.reply_of(TORRENTS_DB_PROGRESS_LIST, {})
        except BusRequestError:
            return []
        return reply.model_dump(mode="json")["tasks"]

    @r.get("/api/series/{series_id}", response_model=SeriesObject,
           responses=_ERRORS)
    async def series_details(series_id: int):
        try:
            s = await gw.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
        except BusRequestError as exc:
            return _error_response(exc, with_success=False)
        reply = await gw.reply_of(METADATA_MAP_GET, {"series_id": series_id})
        planned = (await gw.reply_of(LIBRARY_RELOCATION_GET, {"series_id": series_id})).new_path
        return series_object(s, reply.mapping, planned_save_path=planned)

    @r.post("/api/series/{series_id}", response_model=OkResponse,
            response_model_exclude_none=True, responses=_ERRORS)
    async def update_series(series_id: int, body: SeriesUpdate):
        """Saving the properties: catalog, then metadata, then the plan of library for the folder.
        The sources are not changed here; the layout of a video series is counted again on
        series.updated. The files take the new names and folder at the next sync (И-59)."""
        sent = body.model_dump(mode="json", exclude_unset=True)
        fields = {k: v for k, v in sent.items()
                  if k not in ("save_path", "tmdb_data")}
        # One save is one change: a sync does not open between its parts.
        try:
            async with admitted(gw, series_id):
                await gw.reply_of(CATALOG_SERIES_UPDATE, {"series_id": series_id, "fields": fields})
                if tmdb_data := sent.get("tmdb_data"):
                    await gw.reply_of(METADATA_MAP_SET, {"series_id": series_id, "tmdb_data": tmdb_data})
                if new_path := sent.get("save_path"):
                    await gw.reply_of(LIBRARY_RELOCATION_PLAN, {"series_id": series_id, "new_path": new_path})
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True,
                "message": reason("series_update_accepted")}

    @r.delete("/api/series/{series_id}", response_model=OkResponse,
              response_model_exclude_none=True, responses=_ERRORS)
    async def delete_series(series_id: int):
        try:
            await gw.reply_of(CATALOG_SERIES_DELETE, {"series_id": series_id})
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True}

    # --- watching and the small properties ----------------------------------------

    @r.post("/api/series/{series_id}/state", response_model=OkResponse,
            response_model_exclude_none=True)
    async def set_state(series_id: int, request: Request):
        """The old contract of the front: ['viewing'] when the modal opens, [] when it closes → the ephemeral viewing (Р-11).
        Opening the modal also starts the check of the files against the disk (Р-13/Р-14: the replacement of the periodic background checks
        of the old system)."""
        data = await request.json()
        viewing = "viewing" in data.get("state", [])
        gw.send_command(CATALOG_VIEWING_START if viewing else CATALOG_VIEWING_STOP, {"series_id": series_id})
        if viewing:
            # The owner of the files of the torrent pipeline checks its own and finds nothing of a foreign series, so
            # the gateway needs no kind to choose.
            gw.send_command(TORRENTS_FS_VERIFY, {"series_id": series_id})
        return {"success": True}

    @r.post("/api/series/{series_id}/toggle_auto_scan",
            response_model=OkResponse, response_model_exclude_none=True,
            responses=_ERRORS)
    async def toggle_auto_scan(series_id: int, request: Request):
        data = await request.json()
        enabled = data.get("enabled")
        if enabled is None:
            return JSONResponse(
                {"success": False, "error": reason("param_missing", name="enabled")},
                status_code=400)
        try:
            async with admitted(gw, series_id):
                await gw.reply_of(CATALOG_SERIES_UPDATE, {
                    "series_id": series_id,
                    "fields": {"auto_scan_enabled": bool(enabled)}})
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True}

    @r.post("/api/series/{series_id}/ignored-seasons",
            response_model=OkResponse, response_model_exclude_none=True,
            responses=_ERRORS)
    async def ignored_seasons(series_id: int, request: Request):
        data = await request.json()
        seasons = data.get("seasons")
        if seasons is None:
            return JSONResponse(
                {"success": False, "error": reason("param_missing", name="seasons")},
                status_code=400)
        try:
            async with admitted(gw, series_id):
                await gw.reply_of(CATALOG_SERIES_UPDATE, {
                    "series_id": series_id,
                    "fields": {"ignored_seasons": json.dumps(seasons)}})
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True}

    @r.put("/api/series/{series_id}/ignored-episodes",
           response_model=OkResponse, response_model_exclude_none=True,
           responses=_ERRORS)
    async def ignored_episodes(series_id: int, request: Request):
        """The memory of the switched-off episodes: {"episodes": {"<season>": [numbers]}}.

        The key is the number of the episode of the series, not the file of a release: the choice must survive the replacement of a release."""
        data = await request.json()
        episodes = data.get("episodes")
        if episodes is None:
            return JSONResponse(
                {"success": False, "error": reason("param_missing", name="episodes")},
                status_code=400)
        try:
            async with admitted(gw, series_id):
                await gw.reply_of(CATALOG_SERIES_UPDATE, {
                    "series_id": series_id,
                    "fields": {"ignored_episodes": json.dumps(episodes)}})
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True}

    @r.post("/api/series/{series_id}/scan", response_model=OkResponse,
            response_model_exclude_none=True,
            responses={409: {"model": ErrorResponse},
                       500: {"model": ErrorResponse}})
    async def scan_series(series_id: int, body: SyncScopeBody | None = None, download: bool = True):
        """A synchronous scan of one series, as in the original: HTTP waits for the result.
        download=false — a scan without a download: the check of the files, the tables, the layout and the plan, but nothing is fetched.
        With a body (a season, an episode of the season) — a sync within the season or the episode: the sources are the same, what is carried out
        is what concerns the scope (an episode of a compilation brings the whole compilation); the series is busy for all of it."""
        try:
            result = await gw.reply_of(SCAN_SERIES_RUN, {
                "series_id": series_id, **({} if download else {"download": False}),
                **(body.model_dump(exclude_none=True) if body is not None else {})})
        except BusRequestError as exc:
            msg = relayed(str(exc))
            # A refusal of the state of the series or of the provider that reads its pages (NotAllowed) is a conflict, not a failure.
            code = 409 if code_of(str(exc)) == "not_allowed" or str(exc).startswith("NotAllowed") else 500
            return JSONResponse({"success": False, "error": msg},
                                status_code=code)
        # A sync names what it came to by the fields it sets: the others are not in the answer.
        return {"success": True, **result.model_dump(mode="json", exclude_unset=True)}

    @r.post("/api/series/{series_id}/scan/cancel", response_model=SyncCancelled, responses={500: {"model": ErrorResponse}})
    async def cancel_scan(series_id: int):
        """Stops the sync of the series: the reading of the pages, the downloads, the jobs of the alignment service, the build of
        the files. Nothing is applied from a stopped sync; it waits for a journal of the library under way."""
        try:
            done = await gw.reply_of(SCAN_RUN_CANCEL, {"series_id": series_id})
        except BusRequestError as exc:
            return JSONResponse({"success": False, "error": relayed(str(exc))}, status_code=500)
        return {"success": True, "cancelled": done.cancelled}

    @r.post("/api/series/{series_id}/failures/reset", response_model=FailuresReset, responses=_ERRORS)
    async def reset_failures(series_id: int, body: FailuresResetBody | None = None):
        """The failed attempts of the series are forgotten: the rejected and failed voice-overs begin with the first source of
        their chain, a video that did not download takes part in the choice of the reference again. This is an edit of the series, not a sync:
        the work on them goes with the next sync. With a body — only the attempts of one level (a season, an episode, a voice-over, the cuts):
        the attempts of the rest stay, and the choice of the reference by them does not change."""
        try:
            async with admitted(gw, series_id):
                done = await gw.reply_of(PLANNER_FAILURES_FORGET, {
                    "series_id": series_id, **(body.model_dump(exclude_none=True) if body is not None else {})})
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True, "reset": done.reset}

    return r
