"""HTTP-точки блока «настройки и справочники» (Р-22).

Формы — старые routes/settings.py, parser.py, tmdb.py, trackers.py,
filebrowser.py и database/logs из system.py. Не перенесены (Р-22):
POST /api/database/clear (мёртвая полная очистка БД), тестовые страницы
hello-world / hello-info / directory-picker-test.
"""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core.module import BusRequestError
from core import logging as core_logging
from core.contracts import (LINE_FIELDS, ConformParams, ConformSettingsDraft, ConformSettingsSaved, ConformSettingsView,
                            PlayersListReply, ProviderSaved, ProviderSettingsDraft, ProviderView, VoiceSettingsDraft,
                            VoiceSettingsSaved, VoiceSettingsView)
from core.reasons import code_of, reason, relayed
from core.topics import (LIBRARY_DIRECTORIES_LIST, METADATA_DETAILS, METADATA_SEARCH, SETTINGS_PATHS_ADD, SETTINGS_PATHS_LIST,
                         SETTINGS_PATHS_REMOVE, SETTINGS_VALUE_GET, SETTINGS_VALUE_SET, SETTINGS_VALUES_BY_PREFIX,
                         SOURCES_PROVIDER_GET, SOURCES_PROVIDER_SET, SOURCES_TERMS_GET, SOURCES_TERMS_SET,
                         SOURCES_TRACKER_SET_MIRRORS, SOURCES_TRACKERS_LIST, TORRENTS_STAGING_GET, TORRENTS_STAGING_SET,
                         TRACKERAUTH_CREDENTIALS_GET, TRACKERAUTH_CREDENTIALS_SET, TRACKERAUTH_SECRET_GET)

from .routing import refusal, route_per_method
from .schemas import DynamicObject, ErrorOnly, ErrorResponse, OkResponse

# Документация веток ошибок (JSONResponse, response_model не трогает).
_ERR = {c: {"model": ErrorResponse} for c in (400, 403, 404, 409, 500)}
_ERR1 = {c: {"model": ErrorOnly} for c in (400, 403, 404, 500)}

# The groups of logging the window lists: a code of each section and the modules in it; the window words both
# (`logs.section.<code>`, `logs.module.<name>` of its catalog).
LOGGING_MODULES = {
    "core": [
        {"name": "gateway"},
        {"name": "catalog"},
        {"name": "settings"},
        {"name": "auth"},
        {"name": "runlog"},
    ],
    "pipeline": [
        {"name": "scan"},
        {"name": "torrents"},
        {"name": "unpack"},
        {"name": "downloads"},
        {"name": "planner"},
        {"name": "renaming"},
        {"name": "library"},
        {"name": "voices"},
        {"name": "conform"},
    ],
    "sources": [
        {"name": "sources"},
        {"name": "trackerauth"},
        {"name": "rules"},
        {"name": "composition"},
        {"name": "metadata"},
    ],
}

# Таблицы, закрытые от админ-вкладки БД: креды и живые куки трекеров.
DB_EXCLUDED_TABLES = {"auth", "tracker_sessions"}

AUTH_SERVICES = ("qbittorrent", "kinozal", "vk", "rutracker")

# Refusals of the library about a path: the path is forbidden, or the folder cannot be read.
_FORBIDDEN_PATH_CODES = frozenset({"path_forbidden", "path_no_access"})


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    # --- авторизации ------------------------------------------------------------

    @r.get("/api/auth", response_model=DynamicObject)
    async def get_auth():
        # Секреты НЕ отдаются на фронт (Этап 3, docs/security.md): только
        # логин/URL и флаг has_password/configured (задан ли секрет).
        out = {}
        for service in AUTH_SERVICES:
            reply = await gw.reply_of(TRACKERAUTH_CREDENTIALS_GET, {"service": service})
            out[service] = reply.credentials.model_dump() if reply.credentials else {}
        tmdb = await gw.reply_of(SETTINGS_VALUE_GET, {"key": "tmdb_token"})
        out["tmdb"] = {"configured": bool(tmdb.value)}
        return out

    @r.get("/api/auth/{service}/secret", response_model=DynamicObject)
    async def get_auth_secret(service: str):
        """The secret of one service, as the eye of its field asks it (Этап 3, docs/security.md: a secret is never given with the
        credentials, only on this request, to the administrator who is signed in): {secret}, empty when none is set. Not cached."""
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
        # Пустой пароль/токен = «не менять» (фронт не присылает текущий) — Этап 3.
        data = await request.json()
        for service in ("qbittorrent", "kinozal", "rutracker"):
            if creds := data.get(service):
                await gw.request(TRACKERAUTH_CREDENTIALS_SET, {
                    "service": service,
                    "username": creds.get("username"),
                    "password": creds.get("password") or None,
                    "url": creds.get("url")})
        if vk := data.get("vk"):
            # as in the original: the VK token goes in the field password
            await gw.request(TRACKERAUTH_CREDENTIALS_SET, {
                "service": "vk", "username": "vk_token",
                "password": vk.get("token") or None})
        if (tmdb := data.get("tmdb")) and tmdb.get("token"):
            await gw.request(SETTINGS_VALUE_SET, {"key": "tmdb_token", "value": tmdb["token"]})
        return {"success": True}

    # --- настройки -------------------------------------------------------------------

    async def _setting_flag(key: str, request: Request,
                            response_key: str = "enabled"):
        if request.method == "POST":
            data = await request.json()
            if response_key in data:
                await gw.request(SETTINGS_VALUE_SET, {
                    "key": key, "value": str(data[response_key]).lower()})
            return {"success": True}
        reply = await gw.reply_of(SETTINGS_VALUE_GET, {"key": key})
        return {response_key: (reply.value or "false") == "true"}


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
            reply = await gw.request("downloads.limits.set", await request.json())
            if not reply.get("ok"):
                return JSONResponse({"error": reply["reason"]}, status_code=400)
            return reply["limits"]
        return await gw.request("downloads.limits.get", {})

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
        """Сохранённые пути загрузки: список, добавление, удаление.
        Источник истины — модуль settings (таблица saved_paths)."""
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
        return await gw.request("voices.settings.get", {})

    @r.get("/api/voices/players", response_model=PlayersListReply)
    async def voices_players():
        """Every player of the order of sources with where it is among the sources of the series."""
        return await gw.request("voices.players.get", {})

    @r.put("/api/voices/settings", response_model=VoiceSettingsSaved)
    async def save_voices_settings(draft: VoiceSettingsDraft):
        """Written whole or refused with coded reasons (`saved: false`); a refusal writes nothing."""
        return await gw.request("voices.settings.set", draft.model_dump(mode="json"))

    @r.get("/api/conform/settings", response_model=ConformSettingsView)
    async def conform_settings():
        """The address of the alignment server and the path map as they apply now."""
        return await gw.request("conform.settings.get", {})

    @r.put("/api/conform/params", response_model=ConformSettingsSaved)
    async def save_conform_params(params: ConformParams):
        """How the daemon is asked to align: written whole or refused with coded reasons (`saved: false`)."""
        return await gw.request("conform.params.set", params.model_dump(mode="json"))

    @r.put("/api/conform/settings", response_model=ConformSettingsSaved)
    async def save_conform_settings(draft: ConformSettingsDraft):
        """Written whole and taken at once, or refused with coded reasons (`saved: false`) and nothing written."""
        return await gw.request("conform.settings.set", draft.model_dump(mode="json"))

    @r.get("/api/provider", response_model=ProviderView)
    async def provider_view():
        """The provider of external sources as it stands: its address, the state of the socket, what its manifest says."""
        return await gw.reply_of(SOURCES_PROVIDER_GET, {})

    @r.put("/api/provider", response_model=ProviderSaved)
    async def save_provider(draft: ProviderSettingsDraft):
        """The address and the token: written whole and taken at once, or refused with coded reasons (`saved: false`)."""
        return await gw.reply_of(SOURCES_PROVIDER_SET, draft.model_dump(mode="json"))

    async def _refresh_debug_groups():
        flags = (await gw.reply_of(SETTINGS_VALUES_BY_PREFIX, {"prefix": "debug_enabled_"})).values
        groups = {key.removeprefix("debug_enabled_")
                  for key, value in flags.items() if value == "true"}
        core_logging.set_debug_groups(groups)
        return flags

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
            await gw.request(SETTINGS_VALUE_SET, {
                "key": f"debug_enabled_{module}",
                "value": str(data.get("enabled")).lower()})
            await _refresh_debug_groups()
            return {"success": True}
        flags = await _refresh_debug_groups()

        def enabled(name: str) -> bool:
            return flags.get(f"debug_enabled_{name}") == "true"

        logging_structure = {
            group: [{**m, "enabled": enabled(m["name"])} for m in mods]
            for group, mods in LOGGING_MODULES.items()}
        return {"logging_modules": logging_structure}

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

    # --- трекеры (владелец — sources) -----------------------------------------------

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
        await gw.request(SOURCES_TRACKER_SET_MIRRORS, {
            "tracker_id": tracker_id, "mirrors": mirrors})
        return {"success": True, "message": reason("mirrors_saved")}

    # --- каталоги -------------------------------------------------------------------

    @r.get("/api/directories", response_model=DynamicObject, responses=_ERR1)
    async def directories(path: str = "/"):
        try:
            return (await gw.reply_of(LIBRARY_DIRECTORIES_LIST, {"path": path})).model_dump(mode="json")
        except BusRequestError as exc:
            msg = relayed(str(exc))
            code = 403 if code_of(str(exc)) in _FORBIDDEN_PATH_CODES else 400
            return JSONResponse({"error": msg}, status_code=code)

    # --- конструктор правил -----------------------------------------------------------

    @r.get("/api/parser-profiles", response_model=list[DynamicObject])
    async def profiles_list():
        return await gw.request("rules.profiles.list", {})

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
            reply = await gw.request("rules.profiles.create",
                                     {"name": name})
        except BusRequestError as exc:
            return refusal(exc, 409)
        return JSONResponse({"success": True, "id": reply["id"]},
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
            await gw.request("rules.profiles.update", {
                "profile_id": profile_id, "data": {"name": data["name"]}})
        except BusRequestError as exc:
            return refusal(exc, 409)
        return {"success": True}

    @r.delete("/api/parser-profiles/{profile_id}", response_model=OkResponse,
              response_model_exclude_none=True, responses=_ERR)
    async def profile_delete(profile_id: int):
        try:
            await gw.request("rules.profiles.delete",
                             {"profile_id": profile_id})
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
        return await gw.request("rules.rules.list",
                                {"profile_id": profile_id})

    @r.post("/api/parser-profiles/{profile_id}/rules",
            response_model=OkResponse, response_model_exclude_none=True,
            responses=_ERR)
    async def rule_add(profile_id: int, request: Request):
        data = await request.json()
        try:
            reply = await gw.request("rules.rules.add", {
                "profile_id": profile_id, "data": data})
        except BusRequestError as exc:
            return refusal(exc, 400)
        return {"success": True, "id": reply["id"]}

    @r.put("/api/parser-rules/{rule_id}", response_model=OkResponse,
           response_model_exclude_none=True, responses=_ERR)
    async def rule_update(rule_id: int, request: Request):
        data = await request.json()
        try:
            await gw.request("rules.rules.update",
                             {"rule_id": rule_id, "data": data})
        except BusRequestError as exc:
            return refusal(exc, 400)
        return {"success": True}

    @r.delete("/api/parser-rules/{rule_id}", response_model=OkResponse,
              response_model_exclude_none=True)
    async def rule_delete(rule_id: int):
        await gw.request("rules.rules.delete", {"rule_id": rule_id})
        return {"success": True}

    @r.post("/api/parser-rules/reorder", response_model=OkResponse,
            response_model_exclude_none=True)
    async def rules_reorder(request: Request):
        ordered_ids = await request.json()
        await gw.request("rules.rules.reorder",
                         {"ordered_ids": ordered_ids})
        return {"success": True}

    # --- логи -----------------------------------------------------------------------

    @r.get("/api/logs", response_model=list[DynamicObject])
    async def logs(group: str | None = None, level: str | None = None,
                   limit: int = 200):
        files = ([f"{level.lower()}.log"] if level else
                 ["error.log", "warning.log", "info.log", "debug.log"])

        def read_logs() -> list[dict]:
            entries = []
            for fname in files:
                path = core_logging.log_file(fname)
                if path is None or not os.path.exists(path):
                    continue
                with open(path, encoding="utf-8") as f:
                    for line in reversed(f.readlines()):
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if group and entry.get("group") != group:
                            continue
                        entries.append(entry)
            entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
            return entries[:limit]

        return await asyncio.to_thread(read_logs)

    @r.get("/api/logs/groups", response_model=list[str])
    async def log_groups():
        """Динамический список групп логов: distinct поля 'group' из всех
        лог-файлов. Фильтр просмотрщика берёт его, а не статичный набор,
        чтобы не отставать от реальных групп (имя группы = имя модуля)."""
        def collect() -> list[str]:
            groups: set[str] = set()
            for fname in ("error.log", "warning.log", "info.log", "debug.log"):
                path = core_logging.log_file(fname)
                if path is None or not os.path.exists(path):
                    continue
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            g = json.loads(line).get("group")
                        except json.JSONDecodeError:
                            continue
                        if g:
                            groups.add(g)
            return sorted(groups)

        return await asyncio.to_thread(collect)

    # --- админ-вкладка БД (сознательный обход Р-7 — отладочный инструмент) -----------

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
        tables = await asyncio.to_thread(_db_tables)
        return [t for t in tables if t not in DB_EXCLUDED_TABLES]

    @r.get("/api/database/table/{table_name}",
           response_model=list[DynamicObject], responses=_ERR1)
    async def database_table(table_name: str):
        tables = await asyncio.to_thread(_db_tables)
        if table_name in DB_EXCLUDED_TABLES or table_name not in tables:
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
        if table_name in DB_EXCLUDED_TABLES or table_name not in tables:
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
