"""HTTP points of the «settings and references» block (Р-22).

The shapes are those of the old routes/settings.py, parser.py, tmdb.py, trackers.py, filebrowser.py and the database/logs of
system.py. Not carried over (Р-22): POST /api/database/clear (the dead full clearing of the database), the test pages
hello-world / hello-info / directory-picker-test.
"""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from calc.gateway.database import is_shown, shown_tables
from calc.gateway.flags import flag_on, flag_text
from calc.gateway.logs import LOG_FILES, groups_in_logs, log_entries, log_files, logging_structure
from core import logging as core_logging
from core.contracts import (LINE_FIELDS, ConformParams, ConformSettingsDraft, ConformSettingsSaved, ConformSettingsView,
                            PlayersListReply, ProviderSaved, ProviderSettingsDraft, ProviderView, VoiceSettingsDraft,
                            VoiceSettingsSaved, VoiceSettingsView)
from core.module import BusRequestError
from core.reasons import code_of, reason, relayed
from core.topics import (CONFORM_PARAMS_SET, CONFORM_SETTINGS_GET, CONFORM_SETTINGS_SET, DOWNLOADS_LIMITS_GET,
                         DOWNLOADS_LIMITS_SET, LIBRARY_DIRECTORIES_LIST, METADATA_DETAILS, METADATA_SEARCH, RULES_PROFILES_CREATE,
                         RULES_PROFILES_DELETE, RULES_PROFILES_LIST, RULES_PROFILES_UPDATE, RULES_RULES_ADD, RULES_RULES_DELETE,
                         RULES_RULES_LIST, RULES_RULES_REORDER, RULES_RULES_UPDATE, SETTINGS_PATHS_ADD, SETTINGS_PATHS_LIST,
                         SETTINGS_PATHS_REMOVE, SETTINGS_VALUE_GET, SETTINGS_VALUE_SET, SOURCES_PROVIDER_GET, SOURCES_PROVIDER_SET,
                         SOURCES_TERMS_GET, SOURCES_TERMS_SET, SOURCES_TRACKER_SET_MIRRORS, SOURCES_TRACKERS_LIST,
                         TORRENTS_STAGING_GET, TORRENTS_STAGING_SET, TRACKERAUTH_CREDENTIALS_GET, TRACKERAUTH_CREDENTIALS_SET,
                         TRACKERAUTH_SECRET_GET, VOICES_PLAYERS_GET, VOICES_SETTINGS_GET, VOICES_SETTINGS_SET)

from .routing import refusal, route_per_method
from .schemas import DynamicObject, ErrorOnly, ErrorResponse, OkResponse

# The documentation of the error branches (JSONResponse, the response_model does not touch them).
_ERR = {c: {"model": ErrorResponse} for c in (400, 403, 404, 409, 500)}
_ERR1 = {c: {"model": ErrorOnly} for c in (400, 403, 404, 500)}

AUTH_SERVICES = ("qbittorrent", "kinozal", "vk", "rutracker")

# Refusals of the library about a path: the path is forbidden, or the folder cannot be read.
_FORBIDDEN_PATH_CODES = frozenset({"path_forbidden", "path_no_access"})


def _read_files(names: list[str]) -> list[list[str]]:
    """The lines of the log files that exist, in the order of the names."""
    found = []
    for name in names:
        path = core_logging.log_file(name)
        if path is None or not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            found.append(f.readlines())
    return found


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    # --- authorizations ---------------------------------------------------------

    @r.get("/api/auth", response_model=DynamicObject)
    async def get_auth():
        # Secrets are NOT given to the front (stage 3, docs/security.md): only the login/URL and the flag has_password/configured
        # (whether the secret is set).
        out = {}
        for service in AUTH_SERVICES:
            reply = await gw.reply_of(TRACKERAUTH_CREDENTIALS_GET, {"service": service})
            out[service] = reply.credentials.model_dump() if reply.credentials else {}
        tmdb = await gw.reply_of(SETTINGS_VALUE_GET, {"key": "tmdb_token"})
        out["tmdb"] = {"configured": bool(tmdb.value)}
        return out

    @r.get("/api/auth/{service}/secret", response_model=DynamicObject)
    async def get_auth_secret(service: str):
        """The secret of one service, as the eye of its field asks it (stage 3, docs/security.md: a secret is never given
        with the credentials, only on this request, to the administrator who is signed in): {secret}, empty when none is set. Not cached."""
        if service == "tmdb":
            secret = (await gw.reply_of(SETTINGS_VALUE_GET, {"key": "tmdb_token"})).value or ""
        elif service in AUTH_SERVICES:
            secret = (await gw.reply_of(TRACKERAUTH_SECRET_GET, {"service": service})).secret
        else:
            raise HTTPException(status_code=404)
        return JSONResponse({"secret": secret}, headers={"Cache-Control": "no-store"})

    @r.post("/api/auth", response_model=OkResponse,
            response_model_exclude_none=True)
    async def save_auth(request: Request):
        # An empty password/token = «do not change» (the front does not send the current one) — stage 3.
        data = await request.json()
        for service in ("qbittorrent", "kinozal", "rutracker"):
            if creds := data.get(service):
                await gw.reply_of(TRACKERAUTH_CREDENTIALS_SET, {
                    "service": service,
                    "username": creds.get("username"),
                    "password": creds.get("password") or None,
                    "url": creds.get("url")})
        if vk := data.get("vk"):
            # as in the original: the VK token goes in the field password
            await gw.reply_of(TRACKERAUTH_CREDENTIALS_SET, {
                "service": "vk", "username": "vk_token",
                "password": vk.get("token") or None})
        if (tmdb := data.get("tmdb")) and tmdb.get("token"):
            await gw.reply_of(SETTINGS_VALUE_SET, {"key": "tmdb_token", "value": tmdb["token"]})
        return {"success": True}

    # --- settings ---------------------------------------------------------------------

    async def _setting_flag(key: str, request: Request,
                            response_key: str = "enabled"):
        if request.method == "POST":
            data = await request.json()
            if response_key in data:
                await gw.reply_of(SETTINGS_VALUE_SET, {
                    "key": key, "value": flag_text(data[response_key])})
            return {"success": True}
        reply = await gw.reply_of(SETTINGS_VALUE_GET, {"key": key})
        return {response_key: flag_on(reply.value)}

    @route_per_method(r, "/api/settings/conform_clear_finished", methods=["GET", "POST"],
                      response_model=DynamicObject)
    async def conform_clear_finished(request: Request):
        return await _setting_flag("conform.clear_finished", request)

    @route_per_method(r, "/api/settings/download_limits", methods=["GET", "POST"], response_model=DynamicObject,
                      responses=_ERR1)
    async def download_limits(request: Request):
        """How many downloads go at once, by the way a file comes (downloads, limits.py): {threads_total,
        threads_per_file, single_at_once, provider_at_once, multi_at_once} both ways; a value the module refuses is 400 with its reason."""
        if request.method == "POST":
            reply = await gw.reply_of(DOWNLOADS_LIMITS_SET, await request.json())
            if not reply.ok:
                return JSONResponse({"error": reply.reason}, status_code=400)
            return reply.limits.model_dump(mode="json")
        return (await gw.reply_of(DOWNLOADS_LIMITS_GET, {})).model_dump(mode="json")

    @route_per_method(r, "/api/settings/source_terms", methods=["GET", "POST"], response_model=DynamicObject,
                      responses=_ERR1)
    async def source_terms(request: Request):
        """The terms of the sites the sources read (sources, term.py): GET {sites: [{site, app, sources, minutes}]}, POST
        {site, minutes} sets one; a value the sources refuse is 400 with its reason."""
        if request.method == "POST":
            data = await request.json()
            reply = await gw.reply_of(SOURCES_TERMS_SET, {"site": data.get("site"), "minutes": data.get("minutes")})
            if not reply.ok:
                return JSONResponse({"error": reply.reason}, status_code=400)
            return {"success": True, "minutes": reply.minutes}
        return (await gw.reply_of(SOURCES_TERMS_GET, {})).model_dump(mode="json")

    @route_per_method(r, "/api/settings/torrent_staging", methods=["GET", "POST"], response_model=DynamicObject,
                      responses=_ERR1)
    async def torrent_staging(request: Request):
        """The folder the releases of the torrent sources download into (torrents): {value} both ways; a value the module
        refuses is 400 with its reason."""
        if request.method == "POST":
            data = await request.json()
            reply = await gw.reply_of(TORRENTS_STAGING_SET, {"value": data.get("value")})
            if not reply.ok:
                return JSONResponse({"error": reply.reason}, status_code=400)
            return {"success": True, "value": reply.value}
        return {"value": (await gw.reply_of(TORRENTS_STAGING_GET, {})).value}

    @route_per_method(r, "/api/settings/saved_paths", methods=["GET", "POST", "DELETE"],
                      response_model=DynamicObject)
    async def saved_paths(request: Request):
        """The saved download paths: the list, the addition, the removal. The source of truth is the module settings (the table
        saved_paths)."""
        if request.method == "POST":
            data = await request.json()
            reply = await gw.reply_of(SETTINGS_PATHS_ADD, {"path": data.get("path", "")})
        elif request.method == "DELETE":
            data = await request.json()
            reply = await gw.reply_of(SETTINGS_PATHS_REMOVE, {"id": data.get("id")})
        else:
            reply = await gw.reply_of(SETTINGS_PATHS_LIST, {})
        return {"paths": [each.model_dump() for each in reply.paths]}

    # Checking and storing belong to the voices module; the gateway only forwards.

    @r.get("/api/voices/settings", response_model=VoiceSettingsView)
    async def voices_settings():
        """What applies now; the screen shows exactly this."""
        return await gw.reply_of(VOICES_SETTINGS_GET, {})

    @r.get("/api/voices/players", response_model=PlayersListReply)
    async def voices_players():
        """Every player of the order of sources with where it is among the sources of the series."""
        return await gw.reply_of(VOICES_PLAYERS_GET, {})

    @r.put("/api/voices/settings", response_model=VoiceSettingsSaved)
    async def save_voices_settings(draft: VoiceSettingsDraft):
        """Written whole or refused with coded reasons (`saved: false`); a refusal writes nothing."""
        return await gw.reply_of(VOICES_SETTINGS_SET, draft.model_dump(mode="json"))

    @r.get("/api/conform/settings", response_model=ConformSettingsView)
    async def conform_settings():
        """The address of the alignment server and the path map as they apply now."""
        return await gw.reply_of(CONFORM_SETTINGS_GET, {})

    @r.put("/api/conform/params", response_model=ConformSettingsSaved)
    async def save_conform_params(params: ConformParams):
        """How the daemon is asked to align: written whole or refused with coded reasons (`saved: false`)."""
        return await gw.reply_of(CONFORM_PARAMS_SET, params.model_dump(mode="json"))

    @r.put("/api/conform/settings", response_model=ConformSettingsSaved)
    async def save_conform_settings(draft: ConformSettingsDraft):
        """Written whole and taken at once, or refused with coded reasons (`saved: false`) and nothing written."""
        return await gw.reply_of(CONFORM_SETTINGS_SET, draft.model_dump(mode="json"))

    @r.get("/api/provider", response_model=ProviderView)
    async def provider_view():
        """The provider of external sources as it stands: its address, the state of the socket, what its manifest says."""
        return await gw.reply_of(SOURCES_PROVIDER_GET, {})

    @r.put("/api/provider", response_model=ProviderSaved)
    async def save_provider(draft: ProviderSettingsDraft):
        """The address and the token: written whole and taken at once, or refused with coded reasons (`saved: false`)."""
        return await gw.reply_of(SOURCES_PROVIDER_SET, draft.model_dump(mode="json"))

    @route_per_method(r, "/api/settings/debug_flags", methods=["GET", "POST"],
                      response_model=DynamicObject, responses=_ERR)
    async def debug_flags(request: Request):
        if request.method == "POST":
            data = await request.json()
            module = data.get("module")
            if not module:
                return JSONResponse(
                    {"success": False,
                     "error": reason("module_missing")}, status_code=400)
            await gw.reply_of(SETTINGS_VALUE_SET, {
                "key": f"debug_enabled_{module}",
                "value": flag_text(data.get("enabled"))})
            await gw.refresh_debug_groups()
            return {"success": True}
        return {"logging_modules": logging_structure(await gw.refresh_debug_groups())}

    # --- TMDB ----------------------------------------------------------------------

    @r.post("/api/tmdb/search", response_model=OkResponse,
            response_model_exclude_none=True, responses=_ERR)
    async def tmdb_search(request: Request):
        data = await request.json()
        query = data.get("query")
        if not query:
            return JSONResponse(
                {"success": False, "error": reason("query_missing")},
                status_code=400)
        try:
            reply = await gw.reply_of(METADATA_SEARCH, {"query": query})
        except BusRequestError as exc:
            return JSONResponse(
                {"success": False, "error": relayed(str(exc))},
                status_code=400)
        return {"success": True, "results": [each.model_dump() for each in reply.results]}

    @r.get("/api/tmdb/details/{tmdb_id}", response_model=DynamicObject,
           responses=_ERR)
    async def tmdb_details(tmdb_id: int):
        try:
            reply = await gw.reply_of(METADATA_DETAILS, {"tmdb_id": tmdb_id})
        except BusRequestError as exc:
            return JSONResponse(
                {"success": False, "error": relayed(str(exc))},
                status_code=404)
        return {"success": True, **reply.model_dump()}

    # --- the trackers (the owner is sources) -------------------------------------

    @r.get("/api/trackers", response_model=list[DynamicObject])
    async def trackers_list():
        return [each.model_dump(mode="json") for each in (await gw.reply_of(SOURCES_TRACKERS_LIST, {})).trackers]

    @r.put("/api/trackers/{tracker_id}", response_model=OkResponse,
           response_model_exclude_none=True, responses=_ERR1)
    async def tracker_update(tracker_id: int, request: Request):
        data = await request.json()
        mirrors = data.get("mirrors")
        if mirrors is None:
            return JSONResponse({"error": reason("mirrors_missing")},
                                status_code=400)
        await gw.reply_of(SOURCES_TRACKER_SET_MIRRORS, {
            "tracker_id": tracker_id, "mirrors": mirrors})
        return {"success": True, "message": reason("mirrors_saved")}

    # --- the directories ------------------------------------------------------------

    @r.get("/api/directories", response_model=DynamicObject, responses=_ERR1)
    async def directories(path: str = "/"):
        try:
            return (await gw.reply_of(LIBRARY_DIRECTORIES_LIST, {"path": path})).model_dump(mode="json")
        except BusRequestError as exc:
            msg = relayed(str(exc))
            code = 403 if code_of(str(exc)) in _FORBIDDEN_PATH_CODES else 400
            return JSONResponse({"error": msg}, status_code=code)

    # --- the constructor of rules -------------------------------------------------------

    @r.get("/api/parser-profiles", response_model=list[DynamicObject])
    async def profiles_list():
        return await gw.reply_of(RULES_PROFILES_LIST, {})

    @r.post("/api/parser-profiles", response_model=OkResponse,
            response_model_exclude_none=True,
            responses={201: {"model": OkResponse}, **_ERR})
    async def profile_create(request: Request):
        data = await request.json()
        name = data.get("name")
        if not name:
            return JSONResponse(
                {"success": False, "error": reason("profile_name_missing")},
                status_code=400)
        try:
            reply = await gw.reply_of(RULES_PROFILES_CREATE, {"name": name})
        except BusRequestError as exc:
            return refusal(exc, 409)
        return JSONResponse({"success": True, "id": reply.id},
                            status_code=201)

    @r.put("/api/parser-profiles/{profile_id}", response_model=OkResponse,
           response_model_exclude_none=True, responses=_ERR)
    async def profile_update(profile_id: int, request: Request):
        data = await request.json()
        if not data.get("name"):
            return JSONResponse(
                {"success": False, "error": reason("profile_new_name_missing")},
                status_code=400)
        try:
            await gw.reply_of(RULES_PROFILES_UPDATE, {
                "profile_id": profile_id, "data": {"name": data["name"]}})
        except BusRequestError as exc:
            return refusal(exc, 409)
        return {"success": True}

    @r.delete("/api/parser-profiles/{profile_id}", response_model=OkResponse,
              response_model_exclude_none=True, responses=_ERR)
    async def profile_delete(profile_id: int):
        try:
            await gw.reply_of(RULES_PROFILES_DELETE, {"profile_id": profile_id})
        except BusRequestError as exc:
            return refusal(exc, 400)
        return {"success": True}

    @r.get("/api/rules/line-fields", response_model=list[str])
    async def line_fields():
        """The codes of the fields of a line of the constructor, in its order (LINE_FIELDS): the quick templates of a
        rule «Таблица» are made of them."""
        return [code for code, _value in LINE_FIELDS if code]

    @r.get("/api/parser-profiles/{profile_id}/rules",
           response_model=list[DynamicObject])
    async def rules_list(profile_id: int):
        return await gw.reply_of(RULES_RULES_LIST, {"profile_id": profile_id})

    @r.post("/api/parser-profiles/{profile_id}/rules",
            response_model=OkResponse, response_model_exclude_none=True,
            responses=_ERR)
    async def rule_add(profile_id: int, request: Request):
        data = await request.json()
        try:
            reply = await gw.reply_of(RULES_RULES_ADD, {"profile_id": profile_id, "data": data})
        except BusRequestError as exc:
            return refusal(exc, 400)
        return {"success": True, "id": reply.id}

    @r.put("/api/parser-rules/{rule_id}", response_model=OkResponse,
           response_model_exclude_none=True, responses=_ERR)
    async def rule_update(rule_id: int, request: Request):
        data = await request.json()
        try:
            await gw.reply_of(RULES_RULES_UPDATE, {"rule_id": rule_id, "data": data})
        except BusRequestError as exc:
            return refusal(exc, 400)
        return {"success": True}

    @r.delete("/api/parser-rules/{rule_id}", response_model=OkResponse,
              response_model_exclude_none=True)
    async def rule_delete(rule_id: int):
        await gw.reply_of(RULES_RULES_DELETE, {"rule_id": rule_id})
        return {"success": True}

    @r.post("/api/parser-rules/reorder", response_model=OkResponse,
            response_model_exclude_none=True)
    async def rules_reorder(request: Request):
        ordered_ids = await request.json()
        await gw.reply_of(RULES_RULES_REORDER, {"ordered_ids": ordered_ids})
        return {"success": True}

    # --- logs -----------------------------------------------------------------------

    @r.get("/api/logs", response_model=list[DynamicObject])
    async def logs(group: str | None = None, level: str | None = None,
                   limit: int = 200):
        def read() -> list[dict]:
            return log_entries(_read_files(log_files(level)), group, limit)

        return await asyncio.to_thread(read)

    @r.get("/api/logs/groups", response_model=list[str])
    async def log_groups():
        """The dynamic list of the log groups: the distinct field 'group' of all the log files. The filter of the viewer takes it,
        not a static set, so as not to fall behind the real groups (the name of a group is the name of a module)."""
        def collect() -> list[str]:
            return groups_in_logs(_read_files(list(LOG_FILES)))

        return await asyncio.to_thread(collect)

    # --- the administrator tab of the database (a deliberate bypass of Р-7 — a debugging tool) -----

    def _db_tables() -> list[str]:
        import sqlite3
        with sqlite3.connect(gw.db_path) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE "
                "'alembic_%'").fetchall()
        return [r[0] for r in rows]

    @r.get("/api/database/tables", response_model=list[str])
    async def database_tables():
        return shown_tables(await asyncio.to_thread(_db_tables))

    @r.get("/api/database/table/{table_name}",
           response_model=list[DynamicObject], responses=_ERR1)
    async def database_table(table_name: str):
        tables = await asyncio.to_thread(_db_tables)
        if not is_shown(table_name, tables):
            return JSONResponse({"error": reason("table_forbidden")},
                                status_code=403)

        def read_table() -> list[dict]:
            import sqlite3
            with sqlite3.connect(gw.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    f'SELECT * FROM "{table_name}"').fetchall()  # noqa: S608 — verified against table list
            return [dict(r) for r in rows]

        return await asyncio.to_thread(read_table)

    @r.post("/api/database/clear_table", response_model=OkResponse,
            response_model_exclude_none=True, responses=_ERR)
    async def database_clear_table(request: Request):
        data = await request.json()
        table_name = data.get("table_name")
        if not table_name:
            return JSONResponse(
                {"success": False, "error": reason("table_name_missing")},
                status_code=400)
        tables = await asyncio.to_thread(_db_tables)
        if not is_shown(table_name, tables):
            return JSONResponse(
                {"success": False,
                 "error": reason("table_clear_failed", table=table_name)},
                status_code=500)

        def clear() -> None:
            import sqlite3
            with sqlite3.connect(gw.db_path) as conn:
                conn.execute(f'DELETE FROM "{table_name}"')  # noqa: S608 — verified against table list
                conn.commit()

        await asyncio.to_thread(clear)
        gw.log.warning("админ-вкладка БД: таблица '%s' очищена", table_name)
        return {"success": True,
                "message": reason("table_cleared", table=table_name)}

    return r
