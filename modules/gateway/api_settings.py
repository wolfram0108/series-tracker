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

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core import BusRequestError
from core import logging as core_logging
from core.contracts import (ConformSettingsDraft, ConformSettingsSaved, ConformSettingsView, VoiceSettingsDraft,
                            VoiceSettingsSaved, VoiceSettingsView)
from core.reasons import reason

from .routing import refusal, route_per_method
from .schemas import DynamicObject, ErrorOnly, ErrorResponse, OkResponse, ParsedUrl, ParseUrlRequest

# Документация веток ошибок (JSONResponse, response_model не трогает).
_ERR = {c: {"model": ErrorResponse} for c in (400, 403, 404, 409, 500)}
_ERR1 = {c: {"model": ErrorOnly} for c in (400, 403, 404, 500)}

# Группы логирования новой системы (UI рендерит список из ответа —
# структура с бэкенда, как в оригинале).
LOGGING_MODULES = {
    "Ядро и шлюз": [
        {"name": "gateway", "description": "HTTP/SSE-шлюз: запросы фронта и трансляция событий."},
        {"name": "catalog", "description": "Каталог сериалов: CRUD, агрегатор статусов и занятости."},
        {"name": "settings", "description": "Хранилище настроек (ключ-значение)."},
        {"name": "auth", "description": "Вход администратора и смена пароля."},
    ],
    "Конвейер": [
        {"name": "scan", "description": "Синхронизация и скан: планировщик, референсы, план загрузки."},
        {"name": "torrents", "description": "qBittorrent и торрент-конвейер (стадии обработки)."},
        {"name": "downloads", "description": "Загрузка видео (yt-dlp), очередь и прогресс."},
        {"name": "planner", "description": "План видео-сериала: актив, загрузки, выравнивание, сборка контейнеров."},
        {"name": "slicing", "description": "Нарезка компиляций ffmpeg по главам."},
        {"name": "renaming", "description": "Переименование файлов и переобработка имён."},
        {"name": "library", "description": "Медиатека: каталоги и перемещение сериалов."},
        {"name": "voices", "description": "Озвучки: группы, загрузка и дорожки рядом с референсом."},
        {"name": "conform", "description": "Выравнивание озвучек: связь с демоном track-muxer."},
    ],
    "Источники и метаданные": [
        {"name": "sources", "description": "Парсеры трекеров, таблицы страниц сайтов и плееры."},
        {"name": "trackerauth", "description": "Авторизация на трекерах, сессии и куки."},
        {"name": "rules", "description": "Движок правил и форматирование имён."},
        {"name": "composition", "description": "Раскладка сериала и настройки выхода."},
        {"name": "metadata", "description": "TMDB: поиск и привязки сериалов."},
    ],
}

# Таблицы, закрытые от админ-вкладки БД: креды и живые куки трекеров.
DB_EXCLUDED_TABLES = {"auth", "tracker_sessions"}

AUTH_SERVICES = ("qbittorrent", "kinozal", "vk", "rutracker")


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    # --- авторизации ------------------------------------------------------------

    @r.get("/api/auth", response_model=DynamicObject)
    async def get_auth():
        # Секреты НЕ отдаются на фронт (Этап 3, docs/security.md): только
        # логин/URL и флаг has_password/configured (задан ли секрет).
        out = {}
        for service in AUTH_SERVICES:
            reply = await gw.request("trackerauth.credentials.get",
                                     {"service": service})
            out[service] = reply["credentials"] or {}
        tmdb = await gw.request("settings.value.get",
                                {"key": "tmdb_token"})
        out["tmdb"] = {"configured": bool(tmdb.get("value"))}
        return out

    @r.post("/api/auth", response_model=OkResponse,
            response_model_exclude_none=True)
    async def save_auth(request: Request):
        # Пустой пароль/токен = «не менять» (фронт не присылает текущий) — Этап 3.
        data = await request.json()
        for service in ("qbittorrent", "kinozal", "rutracker"):
            if creds := data.get(service):
                await gw.request("trackerauth.credentials.set", {
                    "service": service,
                    "username": creds.get("username"),
                    "password": creds.get("password") or None,
                    "url": creds.get("url")})
        if vk := data.get("vk"):
            # как в оригинале: токен VK — в поле password
            await gw.request("trackerauth.credentials.set", {
                "service": "vk", "username": "vk_token",
                "password": vk.get("token") or None})
        if (tmdb := data.get("tmdb")) and tmdb.get("token"):
            await gw.request("settings.value.set", {
                "key": "tmdb_token", "value": tmdb["token"]})
        return {"success": True}

    # --- разбор URL (форма добавления) ---------------------------------------------

    @r.post("/api/parse_url", response_model=ParsedUrl, response_model_exclude_unset=True, responses=_ERR1)
    async def parse_url(body: ParseUrlRequest):
        """A torrent page for the add form. A page that a site reads is no torrent page: `composition.preview`
        shows it, and here it is refused with a code."""
        try:
            site = await gw.request("sources.site.resolve", {"url": body.url})
            if site["site"] is not None:
                return JSONResponse({"error": reason("not_a_torrent_page")}, status_code=400)
            result = await gw.request("sources.parse", {"url": body.url})
        except BusRequestError as exc:
            return refusal(exc, 400, with_success=False)
        torrents = [{**rel,
                     "link": rel.get("link") or rel.get("magnet"),
                     "date_time": rel.get("date_marker")}
                    for rel in result.get("releases", [])]
        return {"success": True, "title": result.get("title"),
                "torrents": torrents, "tracker_info": result.get("tracker")}

    # --- настройки -------------------------------------------------------------------

    async def _setting_flag(key: str, request: Request,
                            response_key: str = "enabled"):
        if request.method == "POST":
            data = await request.json()
            if response_key in data:
                await gw.request("settings.value.set", {
                    "key": key, "value": str(data[response_key]).lower()})
            return {"success": True}
        reply = await gw.request("settings.value.get", {"key": key})
        return {response_key: (reply.get("value") or "false") == "true"}

    @route_per_method(r, "/api/settings/force_replace", methods=["GET", "POST"],
                      response_model=DynamicObject)
    async def force_replace(request: Request):
        return await _setting_flag("debug_force_replace", request)

    @route_per_method(r, "/api/settings/less_strict_scan", methods=["GET", "POST"],
                      response_model=DynamicObject)
    async def less_strict_scan(request: Request):
        return await _setting_flag("debug_less_strict_scan", request)

    @route_per_method(r, "/api/settings/slicing_delete_source",
                      methods=["GET", "POST"], response_model=DynamicObject)
    async def slicing_delete_source(request: Request):
        return await _setting_flag("slicing_delete_source_file", request)

    @route_per_method(r, "/api/settings/conform_clear_finished", methods=["GET", "POST"],
                      response_model=DynamicObject)
    async def conform_clear_finished(request: Request):
        return await _setting_flag("conform.clear_finished", request)

    @route_per_method(r, "/api/settings/parallel_downloads", methods=["GET", "POST"],
                      response_model=DynamicObject)
    async def parallel_downloads(request: Request):
        if request.method == "POST":
            data = await request.json()
            if "value" in data:
                await gw.request("settings.value.set", {
                    "key": "max_parallel_downloads",
                    "value": str(data["value"])})
            return {"success": True}
        reply = await gw.request("settings.value.get",
                                 {"key": "max_parallel_downloads"})
        return {"value": int(reply.get("value") or 2)}

    @route_per_method(r, "/api/settings/concurrent_fragments",
                      methods=["GET", "POST"], response_model=DynamicObject)
    async def concurrent_fragments(request: Request):
        """Число параллельных фрагментов yt-dlp (-N) — ускорение одной
        загрузки на hls-потоках."""
        if request.method == "POST":
            data = await request.json()
            if "value" in data:
                await gw.request("settings.value.set", {
                    "key": "yt_dlp_concurrent_fragments",
                    "value": str(data["value"])})
            return {"success": True}
        reply = await gw.request("settings.value.get",
                                 {"key": "yt_dlp_concurrent_fragments"})
        return {"value": int(reply.get("value") or 6)}

    @route_per_method(r, "/api/settings/saved_paths", methods=["GET", "POST", "DELETE"],
                      response_model=DynamicObject)
    async def saved_paths(request: Request):
        """Сохранённые пути загрузки: список, добавление, удаление.
        Источник истины — модуль settings (таблица saved_paths)."""
        if request.method == "POST":
            data = await request.json()
            reply = await gw.request("settings.paths.add",
                                     {"path": data.get("path", "")})
            return {"paths": reply["paths"]}
        if request.method == "DELETE":
            data = await request.json()
            reply = await gw.request("settings.paths.remove",
                                     {"id": data.get("id")})
            return {"paths": reply["paths"]}
        reply = await gw.request("settings.paths.list", {})
        return {"paths": reply["paths"]}

    # Checking and storing belong to the voices module; the gateway only forwards.

    @r.get("/api/voices/settings", response_model=VoiceSettingsView)
    async def voices_settings():
        """What applies now; the screen shows exactly this."""
        return await gw.request("voices.settings.get", {})

    @r.put("/api/voices/settings", response_model=VoiceSettingsSaved)
    async def save_voices_settings(draft: VoiceSettingsDraft):
        """Written whole or refused with coded reasons (`saved: false`); a refusal writes nothing."""
        return await gw.request("voices.settings.set", draft.model_dump(mode="json"))

    @r.get("/api/conform/settings", response_model=ConformSettingsView)
    async def conform_settings():
        """The address of the alignment server and the path map as they apply now."""
        return await gw.request("conform.settings.get", {})

    @r.put("/api/conform/settings", response_model=ConformSettingsSaved)
    async def save_conform_settings(draft: ConformSettingsDraft):
        """Written whole and taken at once, or refused with coded reasons (`saved: false`) and nothing written."""
        return await gw.request("conform.settings.set", draft.model_dump(mode="json"))

    async def _refresh_debug_groups():
        reply = await gw.request("settings.values.by_prefix",
                                 {"prefix": "debug_enabled_"})
        flags = reply["values"]
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
                     "error": "Module name not specified"}, status_code=400)
            await gw.request("settings.value.set", {
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
                {"success": False, "error": "Query is required"},
                status_code=400)
        try:
            reply = await gw.request("metadata.search", {"query": query})
        except BusRequestError as exc:
            return JSONResponse(
                {"success": False, "error": str(exc).split(": ", 1)[-1]},
                status_code=400)
        return {"success": True, "results": reply["results"]}

    @r.get("/api/tmdb/details/{tmdb_id}", response_model=DynamicObject,
           responses=_ERR)
    async def tmdb_details(tmdb_id: int):
        try:
            reply = await gw.request("metadata.details",
                                     {"tmdb_id": tmdb_id})
        except BusRequestError as exc:
            return JSONResponse(
                {"success": False, "error": str(exc).split(": ", 1)[-1]},
                status_code=404)
        return {"success": True, **reply}

    # --- трекеры (владелец — sources) -----------------------------------------------

    @r.get("/api/trackers", response_model=list[DynamicObject])
    async def trackers_list():
        return await gw.request("sources.trackers.list", {})

    @r.put("/api/trackers/{tracker_id}", response_model=OkResponse,
           response_model_exclude_none=True, responses=_ERR1)
    async def tracker_update(tracker_id: int, request: Request):
        data = await request.json()
        mirrors = data.get("mirrors")
        if mirrors is None:
            return JSONResponse({"error": "Список зеркал не предоставлен"},
                                status_code=400)
        await gw.request("sources.tracker.set_mirrors", {
            "tracker_id": tracker_id, "mirrors": mirrors})
        return {"success": True, "message": "Список зеркал обновлен."}

    # --- каталоги -------------------------------------------------------------------

    @r.get("/api/directories", response_model=DynamicObject, responses=_ERR1)
    async def directories(path: str = "/"):
        try:
            return await gw.request("library.directories.list",
                                    {"path": path})
        except BusRequestError as exc:
            msg = str(exc).split(": ", 1)[-1]
            code = 403 if ("запрещ" in msg or "нет доступа" in msg) else 400
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
                {"success": False, "error": "Имя профиля не указано"},
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
                {"success": False, "error": "Новое имя не указано"},
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
            return JSONResponse({"error": "Доступ к этой таблице запрещен"},
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
                {"success": False, "error": "Имя таблицы не указано"},
                status_code=400)
        tables = await asyncio.to_thread(_db_tables)
        if table_name in DB_EXCLUDED_TABLES or table_name not in tables:
            return JSONResponse(
                {"success": False,
                 "error": f"Не удалось очистить таблицу '{table_name}'."},
                status_code=500)

        def clear() -> None:
            import sqlite3
            with sqlite3.connect(gw.db_path) as conn:
                conn.execute(f'DELETE FROM "{table_name}"')  # noqa: S608 — verified against table list
                conn.commit()

        await asyncio.to_thread(clear)
        gw.log.warning("админ-вкладка БД: таблица '%s' очищена", table_name)
        return {"success": True,
                "message": f"Таблица '{table_name}' успешно очищена."}

    return r
