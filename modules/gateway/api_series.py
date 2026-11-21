"""HTTP-точки блока «серии» (Р-19) — адаптация старого контракта к шине.

Формы ответов повторяют старые роуты routes/series.py; вся логика —
в модулях, здесь только перекладка полей и маршрутизация ошибок шины
в HTTP-коды (404 — нет сущности, 409 — занято, 400 — отказ валидации).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.contracts import SeriesCard, TmdbMapping
from core.module import BusRequestError
from core.reasons import code_of, reason, relayed
from core.topics import (CATALOG_SERIES_CREATE, CATALOG_SERIES_GET, CATALOG_SERIES_LIST, LIBRARY_RELOCATION_GET,
                         LIBRARY_RELOCATION_PLAN, METADATA_MAP_GET, METADATA_MAP_LIST, METADATA_MAP_SET, SCAN_RUN_CANCEL,
                         SCAN_SERIES_RUN, TORRENTS_DB_DOWNLOADED_COUNTS, TORRENTS_DB_PROGRESS_LIST, TORRENTS_FS_VERIFY, Topic)
from .routing import admitted

from .schemas import (CreatedSeries, ErrorResponse, FailuresReset, FailuresResetBody, OkResponse, SyncCancelled, SyncScopeBody,
                      QueueTask, SeriesCreate, SeriesObject, SeriesUpdate)

# Общая документация веток ошибок (отдаются через JSONResponse, на них
# response_model не распространяется).
_ERRORS = {code: {"model": ErrorResponse} for code in (400, 404, 409, 500)}


def _iso(value):
    """Наивная UTC-строка БД ('%Y-%m-%d %H:%M:%S.%f') → ISO с 'T'
    (контракт старого API: datetime.isoformat())."""
    return value.replace(" ", "T", 1) if isinstance(value, str) else value


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

    async def _counts(topic: Topic | str) -> dict:
        """Счётчики скачанного; модуль может быть не запущен (torrents
        без qBit) — тогда счётчик пустой, карточки живут дальше."""
        try:
            return (await gw.request(topic, {}))["counts"]
        except BusRequestError:
            return {}

    def _card(series: SeriesCard, mapping: TmdbMapping | None, **added) -> dict:
        """The series as the window shows it: the card the catalog answered written as data with its date in ISO, the
        mapping of TMDB (its date in ISO too) and the fields the gateway adds. What a module answered is its own and is
        not changed."""
        tmdb = {**mapping.model_dump(), "last_updated": _iso(mapping.last_updated)} if mapping else None
        return {**series.model_dump(mode="json"), "last_scan_time": _iso(series.last_scan_time), "tmdb_info": tmdb, **added}

    # --- список и карточки ----------------------------------------------------

    @r.get("/api/series", response_model=list[SeriesObject])
    async def list_series():
        series = (await gw.reply_of(CATALOG_SERIES_LIST, {})).series
        mappings = {m.series_id: m for m in (await gw.reply_of(METADATA_MAP_LIST, {})).mappings}
        # Each owner counts its own rows and a series has rows with one of them only, so the sum needs no kind.
        counts = [await _counts("planner.downloaded_counts"),
                  await _counts(TORRENTS_DB_DOWNLOADED_COUNTS)]
        return [_card(s, mappings.get(s.id),
                      downloaded_episodes_count=sum(each.get(s.id, 0) for each in counts)) for s in series]

    @r.post("/api/series", response_model=CreatedSeries, responses=_ERRORS)
    async def add_series(body: SeriesCreate):
        data = body.model_dump(mode="json", exclude={"tmdb_data"}, exclude_unset=True)
        try:
            series = await gw.reply_of(CATALOG_SERIES_CREATE, {"data": data})
        except BusRequestError as exc:
            return _error_response(exc)
        series_id = series.id
        if body.tmdb_data:
            await gw.request(METADATA_MAP_SET, {
                "series_id": series_id, "tmdb_data": body.tmdb_data})
        return {"success": True, "series_id": series_id}

    # ВАЖНО: статический путь регистрируется до /{series_id}.
    @r.get("/api/series/active_torrents", response_model=list[QueueTask])
    async def active_torrents():
        """Прогресс торрент-загрузок с именами серий (таблица
        «Мониторинг» вкладки агентов; Р-23 — исправление маппинга
        блока 2: источник — torrent_progress модуля torrents, не очередь конвейера)."""
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
        return _card(s, reply.mapping, planned_save_path=planned)

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
                    await gw.request(METADATA_MAP_SET, {
                        "series_id": series_id, "tmdb_data": tmdb_data})
                if new_path := sent.get("save_path"):
                    await gw.request(LIBRARY_RELOCATION_PLAN, {
                        "series_id": series_id, "new_path": new_path})
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True,
                "message": reason("series_update_accepted")}

    @r.delete("/api/series/{series_id}", response_model=OkResponse,
              response_model_exclude_none=True, responses=_ERRORS)
    async def delete_series(series_id: int):
        try:
            await gw.request("catalog.series.delete", {"series_id": series_id})
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
                {"success": False, "error": reason("param_missing", name="seasons")},
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
                {"success": False, "error": reason("param_missing", name="episodes")},
                status_code=400)
        try:
            async with admitted(gw, series_id):
                await gw.request("catalog.series.update", {
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
        """Синхронный скан одной серии, как в оригинале: HTTP ждёт
        результата.
        download=false — скан без загрузки: сверка файлов, таблицы, раскладка и план, но ничего не качается.
        С телом (сезон, серия сезона) — синхронизация в рамках сезона или серии: источники те же, исполняется
        то, что касается области (серия компиляции приводит всю компиляцию); сериал занят на всё время."""
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
        """Остановить синхронизацию сериала: чтение страниц, загрузки, задачи сервиса выравнивания, сборку файлов.
        Из остановленной синхронизации ничего не применяется; идущий журнал библиотеки она дожидается."""
        try:
            done = await gw.reply_of(SCAN_RUN_CANCEL, {"series_id": series_id})
        except BusRequestError as exc:
            return JSONResponse({"success": False, "error": relayed(str(exc))}, status_code=500)
        return {"success": True, "cancelled": done.cancelled}

    @r.post("/api/series/{series_id}/failures/reset", response_model=FailuresReset, responses=_ERRORS)
    async def reset_failures(series_id: int, body: FailuresResetBody | None = None):
        """Неудачные попытки сериала забываются: отклонённые и сбойные озвучки начинаются с первого источника своей
        цепочки, видео, которое не скачалось, снова участвует в выборе референса. Это правка сериала, а не
        синхронизация: работа по ним идёт со следующей синхронизацией. С телом — только попытки одного уровня (сезона,
        серии, озвучки, нарезок): попытки остальных остаются, и выбор референса по ним не меняется."""
        try:
            async with admitted(gw, series_id):
                done = await gw.request("planner.failures.forget", {
                    "series_id": series_id, **(body.model_dump(exclude_none=True) if body is not None else {})})
        except BusRequestError as exc:
            return _error_response(exc)
        return {"success": True, "reset": int((done or {}).get("reset") or 0)}

    return r
