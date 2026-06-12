"""HTTP-точки блока «серии» (Р-19) — адаптация старого контракта к шине.

Формы ответов повторяют старые роуты routes/series.py; вся логика —
в модулях, здесь только перекладка полей и маршрутизация ошибок шины
в HTTP-коды (404 — нет сущности, 409 — занято, 400 — отказ валидации).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core import BusRequestError


def _iso(value):
    """Наивная UTC-строка БД ('%Y-%m-%d %H:%M:%S.%f') → ISO с 'T'
    (контракт старого API: datetime.isoformat())."""
    return value.replace(" ", "T", 1) if isinstance(value, str) else value


def _error_response(exc: BusRequestError, *, with_success: bool = True):
    msg = str(exc)
    if "LookupError" in msg:
        code, text = 404, "Сериал не найден"
    elif "уже выполняется" in msg or "уже запущен" in msg:
        code, text = 409, msg.split(": ", 1)[-1]
    elif "LibraryError" in msg:
        code, text = 400, msg.split(": ", 1)[-1]
    else:
        code, text = 500, msg
    body = {"success": False, "error": text} if with_success \
        else {"error": text}
    return JSONResponse(body, status_code=code)


def build_router(gw) -> APIRouter:  # gw: GatewayModule
    r = APIRouter()

    async def _counts(topic: str) -> dict:
        """Счётчики скачанного; модуль может быть не запущен (torrents
        без qBit) — тогда счётчик пустой, карточки живут дальше."""
        try:
            return (await gw.request(topic, {}, timeout=10))["counts"]
        except BusRequestError:
            return {}

    def _attach_tmdb(series: dict, mapping: dict | None) -> None:
        if mapping:
            mapping["last_updated"] = _iso(mapping.get("last_updated"))
        series["tmdb_info"] = mapping

    # --- список и карточки ----------------------------------------------------

    @r.get("/api/series")
    async def list_series():
        series = await gw.request("catalog.series.list", {}, timeout=15)
        reply = await gw.request("metadata.map.list", {}, timeout=10)
        mappings = {m["series_id"]: m for m in reply["mappings"]}
        vk = await _counts("scan.media.downloaded_counts")
        torrent = await _counts("torrents.db.downloaded_counts")
        for s in series:
            s["last_scan_time"] = _iso(s.get("last_scan_time"))
            _attach_tmdb(s, mappings.get(s["id"]))
            counts = vk if s.get("source_type") == "vk_video" else torrent
            s["downloaded_episodes_count"] = counts.get(s["id"], 0)
        return series

    @r.post("/api/series")
    async def add_series(request: Request):
        data = await request.json()
        series = await gw.request("catalog.series.create", {"data": data},
                                  timeout=15)
        series_id = series["id"]
        if tmdb_data := data.get("tmdb_data"):
            await gw.request("metadata.map.set", {
                "series_id": series_id, "tmdb_data": tmdb_data}, timeout=10)
        if data.get("source_type", "torrent") == "torrent" \
                and data.get("torrents"):
            try:
                await gw.request("torrents.db.add", {
                    "series_id": series_id, "torrents": data["torrents"]},
                    timeout=10)
            except BusRequestError as exc:
                gw.log.warning("торренты формы добавления не записаны "
                               "(модуль torrents недоступен?): %s", exc)
        return {"success": True, "series_id": series_id}

    # ВАЖНО: статический путь регистрируется до /{series_id}.
    @r.get("/api/series/active_torrents")
    async def active_torrents():
        try:
            reply = await gw.request("torrents.queue.get", {}, timeout=10)
        except BusRequestError:
            return []
        # старый контракт: у задач поле hash (находка 39)
        return [{"hash": t.get("torrent_hash"), **t} for t in reply["tasks"]]

    @r.get("/api/series/{series_id}")
    async def series_details(series_id: int):
        try:
            s = await gw.request("catalog.series.get",
                                 {"series_id": series_id}, timeout=10)
        except BusRequestError as exc:
            return _error_response(exc, with_success=False)
        s["last_scan_time"] = _iso(s.get("last_scan_time"))
        if s.get("source_type") == "torrent":
            reply = await gw.request("sources.tracker.resolve",
                                     {"url": s["url"]}, timeout=10)
            s["tracker_info"] = reply["tracker"]
        reply = await gw.request("metadata.map.get",
                                 {"series_id": series_id}, timeout=10)
        _attach_tmdb(s, reply["mapping"])
        return s

    @r.post("/api/series/{series_id}")
    async def update_series(series_id: int, request: Request):
        """Сохранение свойств: catalog → metadata → library/renaming
        (сценарий Р-19; цепочка «переместили → переименовали» — Р-17)."""
        data = await request.json()
        try:
            series = await gw.request("catalog.series.get",
                                      {"series_id": series_id}, timeout=10)
            fields = {k: v for k, v in data.items()
                      if k not in ("save_path", "last_scan_time",
                                   "tmdb_data")}
            await gw.request("catalog.series.update", {
                "series_id": series_id, "fields": fields}, timeout=10)
        except BusRequestError as exc:
            return _error_response(exc)
        if tmdb_data := data.get("tmdb_data"):
            await gw.request("metadata.map.set", {
                "series_id": series_id, "tmdb_data": tmdb_data}, timeout=10)
        new_path = data.get("save_path")
        if new_path and new_path != series.get("save_path"):
            try:
                await gw.request("library.relocate", {
                    "series_id": series_id, "new_path": new_path},
                    timeout=15)
            except BusRequestError as exc:
                return _error_response(exc)
        else:
            # как в оригинале: каждое сохранение свойств переобрабатывает
            # имена; при перемещении это сделает сам library (Р-17)
            gw.send_command("renaming.reprocess", {"series_id": series_id})
        return {"success": True,
                "message": "Задача на обновление принята в обработку."}

    @r.delete("/api/series/{series_id}")
    async def delete_series(series_id: int, delete_from_qb: bool = False):
        try:
            await gw.request("catalog.series.delete", {
                "series_id": series_id, "delete_from_qb": delete_from_qb},
                timeout=15)
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True}

    # --- просмотр и мелкие свойства ---------------------------------------------

    @r.post("/api/series/{series_id}/state")
    async def set_state(series_id: int, request: Request):
        """Старый контракт фронта: ['viewing'] при открытии модалки,
        [] при закрытии → эфемерный viewing (Р-11)."""
        data = await request.json()
        topic = ("catalog.viewing.start"
                 if "viewing" in data.get("state", [])
                 else "catalog.viewing.stop")
        gw.send_command(topic, {"series_id": series_id})
        return {"success": True}

    @r.post("/api/series/{series_id}/toggle_auto_scan")
    async def toggle_auto_scan(series_id: int, request: Request):
        data = await request.json()
        enabled = data.get("enabled")
        if enabled is None:
            return JSONResponse(
                {"success": False, "error": "Параметр 'enabled' не указан"},
                status_code=400)
        try:
            await gw.request("catalog.series.update", {
                "series_id": series_id,
                "fields": {"auto_scan_enabled": bool(enabled)}}, timeout=10)
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True}

    @r.post("/api/series/{series_id}/ignored-seasons")
    async def ignored_seasons(series_id: int, request: Request):
        data = await request.json()
        seasons = data.get("seasons")
        if seasons is None:
            return JSONResponse(
                {"success": False, "error": "Параметр 'seasons' не указан"},
                status_code=400)
        try:
            await gw.request("catalog.series.update", {
                "series_id": series_id,
                "fields": {"ignored_seasons": json.dumps(seasons)}},
                timeout=10)
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True}

    @r.put("/api/series/{series_id}/vk-quality-priority")
    async def vk_quality_priority(series_id: int, request: Request):
        data = await request.json()
        priority = data.get("priority")
        if not isinstance(priority, list):
            return JSONResponse(
                {"success": False, "error": "Приоритет должен быть списком"},
                status_code=400)
        try:
            await gw.request("catalog.series.update", {
                "series_id": series_id,
                "fields": {"vk_quality_priority": json.dumps(priority)}},
                timeout=10)
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True, "message": "Приоритет качества сохранен."}

    @r.get("/api/series/{series_id}/torrents/history")
    async def torrents_history(series_id: int):
        try:
            return await gw.request("torrents.db.history",
                                    {"series_id": series_id}, timeout=10)
        except BusRequestError:
            return []

    return r
