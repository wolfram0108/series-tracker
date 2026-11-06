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
from core.contracts import DubItem

from .routing import admitted

from .schemas import (CreatedSeries, DynamicObject, ErrorResponse, FailuresReset, OkResponse,
                      QueueTask, SeriesCreate, SeriesObject, SeriesUpdate,
                      VoiceDubs)

# Общая документация веток ошибок (отдаются через JSONResponse, на них
# response_model не распространяется).
_ERRORS = {code: {"model": ErrorResponse} for code in (400, 404, 409, 500)}


def _iso(value):
    """Наивная UTC-строка БД ('%Y-%m-%d %H:%M:%S.%f') → ISO с 'T'
    (контракт старого API: datetime.isoformat())."""
    return value.replace(" ", "T", 1) if isinstance(value, str) else value


def _error_response(exc: BusRequestError, *, with_success: bool = True):
    msg = str(exc)
    if "LookupError" in msg:
        code, text = 404, "Сериал не найден"
    elif "уже выполняется" in msg or "not_allowed" in msg:
        code, text = 409, msg.split(": ", 1)[-1]
    elif "LibraryError" in msg:
        code, text = 400, msg.split(": ", 1)[-1]
    elif "ValueError" in msg:
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
            return (await gw.request(topic, {}))["counts"]
        except BusRequestError:
            return {}

    def _attach_tmdb(series: dict, mapping: dict | None) -> None:
        if mapping:
            mapping["last_updated"] = _iso(mapping.get("last_updated"))
        series["tmdb_info"] = mapping

    # --- список и карточки ----------------------------------------------------

    @r.get("/api/series", response_model=list[SeriesObject])
    async def list_series():
        series = await gw.request("catalog.series.list", {})
        reply = await gw.request("metadata.map.list", {})
        mappings = {m["series_id"]: m for m in reply["mappings"]}
        # Each owner counts its own rows and a series has rows with one of them only, so the sum needs no kind.
        counts = [await _counts("scan.media.downloaded_counts"),
                  await _counts("torrents.db.downloaded_counts")]
        for s in series:
            s["last_scan_time"] = _iso(s.get("last_scan_time"))
            _attach_tmdb(s, mappings.get(s["id"]))
            s["downloaded_episodes_count"] = sum(each.get(s["id"], 0) for each in counts)
        return series

    @r.post("/api/series", response_model=CreatedSeries, responses=_ERRORS)
    async def add_series(body: SeriesCreate):
        data = body.model_dump(mode="json", exclude={"tmdb_data", "torrents"}, exclude_unset=True)
        try:
            series = await gw.request("catalog.series.create", {"data": data})
        except BusRequestError as exc:
            return _error_response(exc)
        series_id = series["id"]
        if body.tmdb_data:
            await gw.request("metadata.map.set", {
                "series_id": series_id, "tmdb_data": body.tmdb_data})
        if body.torrents:
            try:
                await gw.request("torrents.db.add", {
                    "series_id": series_id, "torrents": body.torrents})
            except BusRequestError as exc:
                gw.log.warning("торренты формы добавления не записаны "
                               "(модуль torrents недоступен?): %s", exc)
        return {"success": True, "series_id": series_id}

    # ВАЖНО: статический путь регистрируется до /{series_id}.
    @r.get("/api/series/active_torrents", response_model=list[QueueTask])
    async def active_torrents():
        """Прогресс торрент-загрузок с именами серий (таблица
        «Мониторинг» вкладки агентов; Р-23 — исправление маппинга
        блока 2: источник — torrent_progress модуля torrents, не очередь конвейера)."""
        try:
            reply = await gw.request("torrents.db.progress.list", {})
        except BusRequestError:
            return []
        return reply["tasks"]

    @r.get("/api/series/{series_id}/voices", response_model=VoiceDubs)
    async def series_voices(series_id: int):
        """The voice-overs of a series by episode, each with its state; later changes come as the
        voice_dub_update event."""
        try:
            reply = await gw.request("voices.dubs.get",
                                     {"series_id": series_id})
        except BusRequestError:
            return {"items": []}
        return {"items": [DubItem.model_validate(item).model_dump(mode="json")
                          for item in reply["items"]]}

    @r.get("/api/series/{series_id}", response_model=SeriesObject,
           responses=_ERRORS)
    async def series_details(series_id: int):
        try:
            s = await gw.request("catalog.series.get",
                                 {"series_id": series_id})
        except BusRequestError as exc:
            return _error_response(exc, with_success=False)
        s["last_scan_time"] = _iso(s.get("last_scan_time"))
        # `sources` decides which address is a tracker's; a series with no such address has no tracker_info.
        for source in s["sources"]:
            reply = await gw.request("sources.tracker.resolve",
                                     {"url": source["url"]})
            if reply["tracker"]:
                s["tracker_info"] = reply["tracker"]
                break
        reply = await gw.request("metadata.map.get",
                                 {"series_id": series_id})
        _attach_tmdb(s, reply["mapping"])
        s["planned_save_path"] = (await gw.request("library.relocation.get", {"series_id": series_id}))["new_path"]
        return s

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
                await gw.request("catalog.series.update", {
                    "series_id": series_id, "fields": fields})
                if tmdb_data := sent.get("tmdb_data"):
                    await gw.request("metadata.map.set", {
                        "series_id": series_id, "tmdb_data": tmdb_data})
                if new_path := sent.get("save_path"):
                    await gw.request("library.relocation.plan", {
                        "series_id": series_id, "new_path": new_path})
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True,
                "message": "Задача на обновление принята в обработку."}

    @r.delete("/api/series/{series_id}", response_model=OkResponse,
              response_model_exclude_none=True, responses=_ERRORS)
    async def delete_series(series_id: int, delete_from_qb: bool = False):
        try:
            await gw.request("catalog.series.delete", {
                "series_id": series_id, "delete_from_qb": delete_from_qb})
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True}

    # --- просмотр и мелкие свойства ---------------------------------------------

    @r.post("/api/series/{series_id}/state", response_model=OkResponse,
            response_model_exclude_none=True)
    async def set_state(series_id: int, request: Request):
        """Старый контракт фронта: ['viewing'] при открытии модалки,
        [] при закрытии → эфемерный viewing (Р-11). Открытие модалки
        дополнительно запускает сверку файлов с диском (Р-13/Р-14:
        замена фоновых периодических проверок старой системы)."""
        data = await request.json()
        viewing = "viewing" in data.get("state", [])
        topic = "catalog.viewing.start" if viewing else "catalog.viewing.stop"
        gw.send_command(topic, {"series_id": series_id})
        if viewing:
            # Each owner of downloaded files checks its own and finds nothing of a foreign series, so the
            # gateway needs no kind to choose between them.
            for sync_topic in ("downloads.fs.sync", "torrents.fs.verify"):
                gw.send_command(sync_topic, {"series_id": series_id})
        return {"success": True}

    @r.post("/api/series/{series_id}/toggle_auto_scan",
            response_model=OkResponse, response_model_exclude_none=True,
            responses=_ERRORS)
    async def toggle_auto_scan(series_id: int, request: Request):
        data = await request.json()
        enabled = data.get("enabled")
        if enabled is None:
            return JSONResponse(
                {"success": False, "error": "Параметр 'enabled' не указан"},
                status_code=400)
        try:
            async with admitted(gw, series_id):
                await gw.request("catalog.series.update", {
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
                {"success": False, "error": "Параметр 'seasons' не указан"},
                status_code=400)
        try:
            async with admitted(gw, series_id):
                await gw.request("catalog.series.update", {
                    "series_id": series_id,
                    "fields": {"ignored_seasons": json.dumps(seasons)}})
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True}

    @r.put("/api/series/{series_id}/ignored-episodes",
           response_model=OkResponse, response_model_exclude_none=True,
           responses=_ERRORS)
    async def ignored_episodes(series_id: int, request: Request):
        """Память отключённых серий: {"episodes": {"<сезон>": [номера]}}.

        Ключ — номер серии сериала, а не файл раздачи: выбор обязан
        пережить замену релиза."""
        data = await request.json()
        episodes = data.get("episodes")
        if episodes is None:
            return JSONResponse(
                {"success": False, "error": "Параметр 'episodes' не указан"},
                status_code=400)
        try:
            async with admitted(gw, series_id):
                await gw.request("catalog.series.update", {
                    "series_id": series_id,
                    "fields": {"ignored_episodes": json.dumps(episodes)}})
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True}

    @r.get("/api/series/{series_id}/torrents/history",
           response_model=list[DynamicObject])
    async def torrents_history(series_id: int):
        try:
            return await gw.request("torrents.db.history",
                                    {"series_id": series_id})
        except BusRequestError:
            return []

    @r.post("/api/series/{series_id}/scan", response_model=OkResponse,
            response_model_exclude_none=True,
            responses={409: {"model": ErrorResponse},
                       500: {"model": ErrorResponse}})
    async def scan_series(series_id: int, download: bool = True):
        """Синхронный скан одной серии, как в оригинале: HTTP ждёт
        результата; force_replace scan берёт из настройки отладки.
        download=false — скан без загрузки: сверка файлов, таблицы, раскладка и план, но ничего не качается."""
        try:
            result = await gw.request("scan.series.run", {
                "series_id": series_id, **({} if download else {"download": False})})
        except BusRequestError as exc:
            msg = str(exc).split(": ", 1)[-1]
            code = 409 if "not_allowed" in msg else 500
            return JSONResponse({"success": False, "error": msg},
                                status_code=code)
        return {"success": True, **result}

    @r.post("/api/series/{series_id}/failures/reset", response_model=FailuresReset, responses=_ERRORS)
    async def reset_failures(series_id: int):
        """Неудачные попытки сериала забываются: отклонённые и сбойные озвучки начинаются с первого источника своей
        цепочки. Это правка сериала, а не синхронизация: работа по ним идёт со следующей синхронизацией."""
        try:
            async with admitted(gw, series_id):
                reply = await gw.request("voices.failures.forget", {"series_id": series_id})
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True, "reset": int((reply or {}).get("reset") or 0)}

    return r
