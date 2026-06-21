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

from .schemas import DynamicObject, ErrorOnly, ErrorResponse, OkResponse

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
    ],
    "Конвейер": [
        {"name": "scan", "description": "Сканирование: планировщик, кандидаты, план загрузки."},
        {"name": "torrents", "description": "qBittorrent и торрент-конвейер (стадии обработки)."},
        {"name": "downloads", "description": "Загрузка VK-видео (yt-dlp), очередь и прогресс."},
        {"name": "slicing", "description": "Нарезка компиляций ffmpeg по главам."},
        {"name": "renaming", "description": "Переименование файлов и переобработка имён."},
        {"name": "library", "description": "Медиатека: каталоги и перемещение сериалов."},
    ],
    "Источники и метаданные": [
        {"name": "sources", "description": "Парсеры трекеров и VK API."},
        {"name": "trackerauth", "description": "Авторизация на трекерах, сессии и куки."},
        {"name": "rules", "description": "Движок правил и форматирование имён."},
        {"name": "metadata", "description": "TMDB: поиск и привязки сериалов."},
    ],
}
FILE_DUMP_FLAGS = [
    {"name": "save_html_kinozal", "description": "Kinozal (HTML)"},
    {"name": "save_html_anilibria", "description": "Anilibria (HTML)"},
    {"name": "save_html_anilibria_tv", "description": "Anilibria.TV (HTML)"},
    {"name": "save_html_astar", "description": "Astar.bz (HTML)"},
    {"name": "save_html_rutracker", "description": "RuTracker (HTML)"},
    {"name": "save_json_vk_scraper", "description": "VK Scraper (JSON)"},
]

# Таблицы, закрытые от админ-вкладки БД: креды и живые куки трекеров.
DB_EXCLUDED_TABLES = {"auth", "tracker_sessions"}

AUTH_SERVICES = ("qbittorrent", "kinozal", "vk", "rutracker")


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    # --- авторизации ------------------------------------------------------------

    @r.get("/api/auth", response_model=DynamicObject)
    async def get_auth():
        out = {}
        for service in AUTH_SERVICES:
            reply = await gw.request("trackerauth.credentials.get",
                                     {"service": service}, timeout=10)
            out[service] = reply["credentials"]
        tmdb = await gw.request("settings.value.get",
                                {"key": "tmdb_token"}, timeout=10)
        out["tmdb"] = {"token": tmdb.get("value") or ""}
        return out

    @r.post("/api/auth", response_model=OkResponse,
            response_model_exclude_none=True)
    async def save_auth(request: Request):
        data = await request.json()
        for service in ("qbittorrent", "kinozal", "rutracker"):
            if creds := data.get(service):
                await gw.request("trackerauth.credentials.set", {
                    "service": service,
                    "username": creds.get("username"),
                    "password": creds.get("password"),
                    "url": creds.get("url")}, timeout=10)
        if vk := data.get("vk"):
            # как в оригинале: токен VK — в поле password
            await gw.request("trackerauth.credentials.set", {
                "service": "vk", "username": "vk_token",
                "password": vk.get("token")}, timeout=10)
        if tmdb := data.get("tmdb"):
            await gw.request("settings.value.set", {
                "key": "tmdb_token", "value": tmdb.get("token", "")},
                timeout=10)
        return {"success": True}

    # --- разбор URL (форма добавления) ---------------------------------------------

    @r.post("/api/parse_url", response_model=DynamicObject, responses=_ERR1)
    async def parse_url(request: Request):
        data = await request.json()
        try:
            result = await gw.request("sources.parse",
                                      {"url": data["url"]}, timeout=300)
        except BusRequestError as exc:
            return JSONResponse({"error": str(exc).split(": ", 1)[-1]},
                                status_code=400)
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
                    "key": key, "value": str(data[response_key]).lower()},
                    timeout=10)
            return {"success": True}
        reply = await gw.request("settings.value.get", {"key": key},
                                 timeout=10)
        return {response_key: (reply.get("value") or "false") == "true"}

    @r.api_route("/api/settings/force_replace", methods=["GET", "POST"],
                 response_model=DynamicObject)
    async def force_replace(request: Request):
        return await _setting_flag("debug_force_replace", request)

    @r.api_route("/api/settings/less_strict_scan", methods=["GET", "POST"],
                 response_model=DynamicObject)
    async def less_strict_scan(request: Request):
        return await _setting_flag("debug_less_strict_scan", request)

    @r.api_route("/api/settings/slicing_delete_source",
                 methods=["GET", "POST"], response_model=DynamicObject)
    async def slicing_delete_source(request: Request):
        return await _setting_flag("slicing_delete_source_file", request)

    @r.api_route("/api/settings/parallel_downloads", methods=["GET", "POST"],
                 response_model=DynamicObject)
    async def parallel_downloads(request: Request):
        if request.method == "POST":
            data = await request.json()
            if "value" in data:
                await gw.request("settings.value.set", {
                    "key": "max_parallel_downloads",
                    "value": str(data["value"])}, timeout=10)
            return {"success": True}
        reply = await gw.request("settings.value.get",
                                 {"key": "max_parallel_downloads"},
                                 timeout=10)
        return {"value": int(reply.get("value") or 2)}

    @r.api_route("/api/settings/concurrent_fragments",
                 methods=["GET", "POST"], response_model=DynamicObject)
    async def concurrent_fragments(request: Request):
        """Число параллельных фрагментов yt-dlp (-N) — ускорение одной
        загрузки на hls-потоках."""
        if request.method == "POST":
            data = await request.json()
            if "value" in data:
                await gw.request("settings.value.set", {
                    "key": "yt_dlp_concurrent_fragments",
                    "value": str(data["value"])}, timeout=10)
            return {"success": True}
        reply = await gw.request("settings.value.get",
                                 {"key": "yt_dlp_concurrent_fragments"},
                                 timeout=10)
        return {"value": int(reply.get("value") or 6)}

    @r.api_route("/api/settings/saved_paths", methods=["GET", "POST", "DELETE"],
                 response_model=DynamicObject)
    async def saved_paths(request: Request):
        """Сохранённые пути загрузки: список, добавление, удаление.
        Источник истины — модуль settings (таблица saved_paths)."""
        if request.method == "POST":
            data = await request.json()
            reply = await gw.request("settings.paths.add",
                                     {"path": data.get("path", "")}, timeout=10)
            return {"paths": reply["paths"]}
        if request.method == "DELETE":
            data = await request.json()
            reply = await gw.request("settings.paths.remove",
                                     {"id": data.get("id")}, timeout=10)
            return {"paths": reply["paths"]}
        reply = await gw.request("settings.paths.list", {}, timeout=10)
        return {"paths": reply["paths"]}

    async def _refresh_debug_groups():
        reply = await gw.request("settings.values.by_prefix",
                                 {"prefix": "debug_enabled_"}, timeout=10)
        flags = reply["values"]
        groups = {key.removeprefix("debug_enabled_")
                  for key, value in flags.items() if value == "true"}
        core_logging.set_debug_groups(groups)
        return flags

    @r.api_route("/api/settings/debug_flags", methods=["GET", "POST"],
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
                "value": str(data.get("enabled")).lower()}, timeout=10)
            await _refresh_debug_groups()
            return {"success": True}
        flags = await _refresh_debug_groups()

        def enabled(name: str) -> bool:
            return flags.get(f"debug_enabled_{name}") == "true"

        logging_structure = {
            group: [{**m, "enabled": enabled(m["name"])} for m in mods]
            for group, mods in LOGGING_MODULES.items()}
        dump_structure = [{**f, "enabled": enabled(f["name"])}
                          for f in FILE_DUMP_FLAGS]
        return {"logging_modules": logging_structure,
                "file_dump_flags": dump_structure}

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
            reply = await gw.request("metadata.search", {"query": query},
                                     timeout=30)
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
                                     {"tmdb_id": tmdb_id}, timeout=30)
        except BusRequestError as exc:
            return JSONResponse(
                {"success": False, "error": str(exc).split(": ", 1)[-1]},
                status_code=404)
        return {"success": True, **reply}

    # --- трекеры (владелец — sources) -----------------------------------------------

    @r.get("/api/trackers", response_model=list[DynamicObject])
    async def trackers_list():
        return await gw.request("sources.trackers.list", {}, timeout=10)

    @r.put("/api/trackers/{tracker_id}", response_model=OkResponse,
           response_model_exclude_none=True, responses=_ERR1)
    async def tracker_update(tracker_id: int, request: Request):
        data = await request.json()
        mirrors = data.get("mirrors")
        if mirrors is None:
            return JSONResponse({"error": "Список зеркал не предоставлен"},
                                status_code=400)
        await gw.request("sources.tracker.set_mirrors", {
            "tracker_id": tracker_id, "mirrors": mirrors}, timeout=10)
        return {"success": True, "message": "Список зеркал обновлен."}

    # --- каталоги -------------------------------------------------------------------

    @r.get("/api/directories", response_model=DynamicObject, responses=_ERR1)
    async def directories(path: str = "/"):
        try:
            return await gw.request("library.directories.list",
                                    {"path": path}, timeout=30)
        except BusRequestError as exc:
            msg = str(exc).split(": ", 1)[-1]
            code = 403 if ("запрещ" in msg or "нет доступа" in msg) else 400
            return JSONResponse({"error": msg}, status_code=code)

    # --- конструктор правил -----------------------------------------------------------

    def _value_error(exc: BusRequestError, code: int):
        return JSONResponse(
            {"success": False, "error": str(exc).split(": ", 1)[-1]},
            status_code=code)

    @r.get("/api/parser-profiles", response_model=list[DynamicObject])
    async def profiles_list():
        return await gw.request("rules.profiles.list", {}, timeout=10)

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
                                     {"name": name}, timeout=10)
        except BusRequestError as exc:
            return _value_error(exc, 409)
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
                "profile_id": profile_id, "data": {"name": data["name"]}},
                timeout=10)
        except BusRequestError as exc:
            return _value_error(exc, 409)
        return {"success": True}

    @r.delete("/api/parser-profiles/{profile_id}", response_model=OkResponse,
              response_model_exclude_none=True, responses=_ERR)
    async def profile_delete(profile_id: int):
        try:
            await gw.request("rules.profiles.delete",
                             {"profile_id": profile_id}, timeout=10)
        except BusRequestError as exc:
            return _value_error(exc, 400)
        return {"success": True}

    @r.get("/api/parser-profiles/{profile_id}/rules",
           response_model=list[DynamicObject])
    async def rules_list(profile_id: int):
        return await gw.request("rules.rules.list",
                                {"profile_id": profile_id}, timeout=10)

    @r.post("/api/parser-profiles/{profile_id}/rules",
            response_model=OkResponse, response_model_exclude_none=True)
    async def rule_add(profile_id: int, request: Request):
        data = await request.json()
        reply = await gw.request("rules.rules.add", {
            "profile_id": profile_id, "data": data}, timeout=10)
        return {"success": True, "id": reply["id"]}

    @r.post("/api/parser-profiles/scrape-titles",
            response_model=list[DynamicObject], responses=_ERR1)
    async def scrape_titles(request: Request):
        data = await request.json()
        if not data.get("channel_url"):
            return JSONResponse(
                {"error": "Необходимо указать URL канала"}, status_code=400)
        try:
            reply = await gw.request("sources.vk.scan", {
                "channel_url": data["channel_url"],
                "query": data.get("query"),
                "search_mode": data.get("search_mode", "search")},
                timeout=900)
        except BusRequestError as exc:
            return JSONResponse(
                {"error": f"Ошибка на сервере при скрапинге: "
                          f"{str(exc).split(': ', 1)[-1]}"},
                status_code=500)
        return reply["videos"]

    @r.post("/api/parser-profiles/test", response_model=list[DynamicObject],
            responses=_ERR1)
    async def rules_test(request: Request):
        data = await request.json()
        if not data.get("profile_id"):
            return JSONResponse({"error": "profile_id не указан"},
                                status_code=400)
        videos = data.get("videos", [])
        if not videos:
            return JSONResponse(
                {"error": "Не переданы данные для тестирования"},
                status_code=400)
        return await gw.request("rules.test", {
            "profile_id": data["profile_id"], "videos": videos},
            timeout=60)

    @r.put("/api/parser-rules/{rule_id}", response_model=OkResponse,
           response_model_exclude_none=True)
    async def rule_update(rule_id: int, request: Request):
        data = await request.json()
        await gw.request("rules.rules.update",
                         {"rule_id": rule_id, "data": data}, timeout=10)
        return {"success": True}

    @r.delete("/api/parser-rules/{rule_id}", response_model=OkResponse,
              response_model_exclude_none=True)
    async def rule_delete(rule_id: int):
        await gw.request("rules.rules.delete", {"rule_id": rule_id},
                         timeout=10)
        return {"success": True}

    @r.post("/api/parser-rules/reorder", response_model=OkResponse,
            response_model_exclude_none=True)
    async def rules_reorder(request: Request):
        ordered_ids = await request.json()
        await gw.request("rules.rules.reorder",
                         {"ordered_ids": ordered_ids}, timeout=10)
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
                path = os.path.join(core_logging.LOG_DIR, fname)
                if not os.path.exists(path):
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
                path = os.path.join(core_logging.LOG_DIR, fname)
                if not os.path.exists(path):
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
                    f"SELECT * FROM {table_name}").fetchall()  # noqa: S608 — имя проверено по списку таблиц
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
                conn.execute(f"DELETE FROM {table_name}")  # noqa: S608 — имя проверено по списку таблиц
                conn.commit()

        await asyncio.to_thread(clear)
        gw.log.warning("админ-вкладка БД: таблица '%s' очищена", table_name)
        return {"success": True,
                "message": f"Таблица '{table_name}' успешно очищена."}

    return r
