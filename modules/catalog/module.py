"""Модуль catalog — владелец таблиц series и series_sources и агрегатор статусов (Р-11).

Сериал и его источники (Д-38). Источник — адрес страницы или `channel|query`
канала VK; он несёт вид (`torrent`, `vk_video`, `web_video`), профиль
конструктора, режим поиска VK и выбор по источнику (сезон каталога Vibix,
отключённые плееры). Поля источника — контракт SeriesSource. Торрентный
сериал тоже имеет один источник. Адрес, вид и профиль у самого сериала
исчезли; запись этих полей (url, source_type, parser_profile_id,
vk_search_mode, vk_quality_priority) в catalog.series.create/update — отказ
ValueError с названием поля.

Вид сериала `kind` — `torrent`, если среди источников есть торрент, иначе
`video`; считается одной функцией (series_sources.kind_of) и отдаётся рядом
со списком источников `sources` (по position, затем id) в каждом ответе о
сериале.

Отказы — ValueError с кодом core/reasons: `no_source` (создание без
источника), `last_source` (удаление единственного источника), `source_exists`
(тот же адрес у сериала дважды), `profile_not_found` (профиля нет).

Events (входящие):
  series.status.contribution {source, series_id, flags} — свёртка
      модуля-владельца задач; публикуется им при старте (после своего
      reconcile) и при каждом изменении вклада. Все false = вклад снят.
  gateway.sse.clients {count} — счётчик SSE-подключений; при 0 все
      эфемерные viewing сбрасываются (вкладка не может держать модалку
      без живого SSE).
  series.purged {series_id, owner} — отчёт владельца серийных таблиц:
      его данные по удалённой серии вычищены.

Commands:
  catalog.viewing.start {series_id} / catalog.viewing.stop {series_id}
      — открытие/закрытие модалки (действия пользователя).

Queries:
  catalog.series.list           → [{...series, kind, sources, statuses: [...]}]
  catalog.series.get {series_id} → {...series, kind, sources, statuses: [...]}
  catalog.status.get {series_id} → {series_id, statuses: [...]}
  catalog.series.create {data}   → {...series} (+ событие series.added,
      затем series.source.added на каждый начальный источник)
      — data.sources: список источников (минимум один), каждый — поля
      SeriesSource без id и series_id; position по умолчанию — порядок
      в списке. Поля data, не входящие в схему series, игнорируются.
  catalog.series.update {series_id, fields} → {applied: {...}}
      (+ событие series.updated с применёнными полями — дельта SSE);
      источники здесь не меняются
  catalog.series.delete {series_id, delete_from_qb?} → {ok}
      — удаляет строку series вместе с её источниками и публикует
      series.deleted (отдельных series.source.removed нет); владельцы
      остальных таблиц чистят своё по подписке (Р-19, событийный каскад)
      и отчитываются series.purged — молчащий владелец означает
      оборванный каскад и попадает в лог
  catalog.sources.list {series_id} → [source]  — по position, затем id
  catalog.sources.by_url {url}     → [source]  — источники с этим адресом
      во всех сериалах
  catalog.sources.by_profile {profile_id} → [source]  — источники, читаемые этим
      профилем правил (конструктор по нему отказывает в удалении профиля)
  catalog.sources.add {series_id, source} → {id}  — в конец списка, если
      position не задан (+ событие series.source.added)
  catalog.sources.update {source_id, fields} → {applied: {...}}  — только
      изменившиеся поля; без изменений событие не публикуется
      (+ событие series.source.updated)
  catalog.sources.remove {source_id} → {ok}  — единственный источник
      сериала удалить нельзя (`last_source`); +series.source.removed

Events (исходящие):
  series.source.added / .updated / .removed {series_id, source_id} —
      модель SourceEvent; публикуются после записи в БД.
  series.status.changed {series_id, statuses, is_busy} — только при
      изменении набора; транслируется в SSE series_updated (Р-18;
      оживляет ветку фронта s.statuses, находка 24).
  series.busy.changed {series_id, is_busy, statuses} — смена занятости
      карточки; тоже series_updated. Оба события несут ПОЛНУЮ дельту
      {statuses, is_busy}: обработчик series_updated на фронте снимает
      спиннер сохранения по falsy is_busy, поэтому каждое series_updated
      обязано нести актуальный is_busy (находка 38).
"""
from __future__ import annotations

import asyncio
import os
import time

from core import BaseModule, sanitize_name, sanitize_path
from core.contracts import SourceEvent
from core.db import Database
from core.envelope import Envelope
from core.reasons import reason

from .aggregator import StatusAggregator
from .repository import CatalogRepository
from .series_sources import checked_fields, checked_sources, refuse_mixed_kinds, refuse_removed_fields

# Columns that end up as path components on disk; the owner normalises
# them on every write so no client can store an unusable name.
_PATH_FIELDS = ("save_path",)
_NAME_FIELDS = ("name_en", "quality_override", "resolution_override")

# Владельцы серийных таблиц: после series.deleted каждый чистит своё и
# отчитывается series.purged. Молчание — обрыв каскада.
PURGE_OWNERS = ("scan", "torrents", "downloads", "slicing", "renaming",
                 "library", "metadata", "voices", "composition")

# Сколько ждём отчёты владельцев, прежде чем считать каскад оборванным.
PURGE_REPORT_TIMEOUT = 15.0


def fs_safe(fields: dict) -> dict:
    out = dict(fields)
    for key in _PATH_FIELDS:
        if isinstance(out.get(key), str):
            out[key] = sanitize_path(out[key])
            if out[key] and not os.path.isabs(out[key]):
                raise ValueError(
                    "путь сохранения должен быть абсолютным (например, /nas/...)")
    for key in _NAME_FIELDS:
        if isinstance(out.get(key), str):
            out[key] = sanitize_name(out[key])
    return out


class CatalogModule(BaseModule):
    name = "catalog"

    def __init__(self, bus, db: Database) -> None:
        self.repo = CatalogRepository(db)
        self.agg = StatusAggregator()
        # is_busy (блокировка карточки): эфемерный набор вкладов
        # series_id -> {источники активной работы} (Р-17). Ошибка
        # больше не держит busy (находка 36) — только активная работа.
        self._busy: dict[int, set[str]] = {}
        # Отчёты владельцев о чистке удалённой серии: series_id -> owners.
        self._purged: dict[int, set[str]] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle("series.status.contribution", self.on_contribution)
        self.handle("series.busy.contribution", self.on_busy_contribution)
        self.handle("series.purged", self.on_series_purged)
        self.handle("gateway.sse.clients", self.on_sse_clients)
        self.handle("catalog.series.set_save_path", self.on_set_save_path)
        self.handle("catalog.viewing.start", self.on_viewing_start)
        self.handle("catalog.viewing.stop", self.on_viewing_stop)
        self.handle("catalog.series.list", self.on_series_list)
        self.handle("catalog.series.get", self.on_series_get)
        self.handle("catalog.status.get", self.on_status_get)
        self.handle("catalog.series.touch_scan_time", self.on_touch)
        self.handle("catalog.series.create", self.on_series_create)
        self.handle("catalog.series.update", self.on_series_update)
        self.handle("catalog.series.delete", self.on_series_delete)
        self.handle("catalog.sources.list", self.on_sources_list)
        self.handle("catalog.sources.by_url", self.on_sources_by_url)
        self.handle("catalog.sources.by_profile", self.on_sources_by_profile)
        self.handle("catalog.sources.add", self.on_source_add)
        self.handle("catalog.sources.update", self.on_source_update)
        self.handle("catalog.sources.remove", self.on_source_remove)

    # --- статусы: вклады и viewing ------------------------------------------

    async def on_contribution(self, env: Envelope) -> None:
        p = env.payload
        changed = self.agg.set_contribution(
            p["series_id"], p["source"], p["flags"])
        self._publish_if_changed(p["series_id"], changed)

    async def on_viewing_start(self, env: Envelope) -> None:
        series_id = env.payload["series_id"]
        self._publish_if_changed(series_id, self.agg.set_viewing(series_id, True))

    async def on_viewing_stop(self, env: Envelope) -> None:
        series_id = env.payload["series_id"]
        self._publish_if_changed(series_id, self.agg.set_viewing(series_id, False))

    async def on_busy_contribution(self, env: Envelope) -> None:
        p = env.payload
        series_id, source = p["series_id"], p["source"]
        sources = self._busy.setdefault(series_id, set())
        before = bool(sources)
        if p["busy"]:
            sources.add(source)
        else:
            sources.discard(source)
        now = bool(sources)
        if not sources:
            self._busy.pop(series_id, None)
        if before != now:
            self.publish_event("series.busy.changed", {
                "series_id": series_id, "is_busy": now,
                "statuses": self.agg.statuses(series_id)})

    async def on_set_save_path(self, env: Envelope) -> dict:
        """save_path — наша колонка; library пишет её после перемещения."""
        path = sanitize_path(env.payload["save_path"])
        if not os.path.isabs(path):
            raise ValueError(
                "путь сохранения должен быть абсолютным (например, /nas/...)")
        await self.repo.set_save_path(env.payload["series_id"], path)
        return {"ok": True}

    async def on_sse_clients(self, env: Envelope) -> None:
        if env.payload["count"] > 0:
            return
        changes = self.agg.clear_all_viewing()
        for series_id, statuses in changes.items():
            self.log.info("SSE-клиентов нет — сброшен viewing для серии %d",
                          series_id)
            self._publish_if_changed(series_id, statuses)

    def _publish_if_changed(self, series_id: int,
                            statuses: list[str] | None) -> None:
        if statuses is None:
            return
        self.publish_event("series.status.changed", {
            "series_id": series_id, "statuses": statuses,
            "is_busy": bool(self._busy.get(series_id))})

    # --- queries --------------------------------------------------------------

    async def on_series_list(self, env: Envelope) -> list[dict]:
        rows = await self.repo.all_series()
        for row in rows:
            row["statuses"] = self.agg.statuses(row["id"])
            row["is_busy"] = bool(self._busy.get(row["id"]))
        return rows

    async def on_series_get(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        row = await self.repo.get_series(series_id)
        if row is None:
            raise LookupError(f"сериал {series_id} не найден")
        row["statuses"] = self.agg.statuses(series_id)
        row["is_busy"] = bool(self._busy.get(series_id))
        return row

    # --- CRUD (Р-19) ----------------------------------------------------------

    async def on_series_create(self, env: Envelope) -> dict:
        data = env.payload["data"]
        refuse_removed_fields(data)
        sources = checked_sources(data.get("sources"))
        series_id = await self.repo.create_series(fs_safe(data), sources)
        row = await self.repo.get_series(series_id)
        row["statuses"] = self.agg.statuses(series_id)
        row["is_busy"] = False
        self.log.info("добавлен сериал %d: %s", series_id, row.get("name"))
        self.publish_event("series.added", row)
        # The first sources are sources that appeared like any added later, so consumers need no second rule.
        for source in row["sources"]:
            self._publish_source("added", series_id, source["id"])
        return row

    async def on_series_update(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        fields = env.payload["fields"]
        refuse_removed_fields(fields)
        if "sources" in fields:
            raise ValueError("поле сериала 'sources' не пишется: источники меняются запросами catalog.sources.*")
        if await self.repo.get_series(series_id) is None:
            raise LookupError(f"сериал {series_id} не найден")
        applied = await self.repo.update_series(series_id, fs_safe(fields))
        if applied:
            # Дельта для SSE series_updated: применённые поля + обязательные
            # statuses/is_busy (находка 38).
            self.publish_event("series.updated", {
                "series_id": series_id, **applied,
                "statuses": self.agg.statuses(series_id),
                "is_busy": bool(self._busy.get(series_id))})
        return {"applied": applied}

    async def on_series_delete(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        if await self.repo.get_series(series_id) is None:
            raise LookupError(f"сериал {series_id} не найден")
        await self.repo.delete_series(series_id)
        self.agg.forget(series_id)
        self._busy.pop(series_id, None)
        self.log.info("сериал %d удалён; каскад — у владельцев таблиц",
                      series_id)
        self.publish_event("series.deleted", {
            "series_id": series_id,
            "delete_from_qb": bool(env.payload.get("delete_from_qb"))})
        self._spawn(self._await_purge_reports(series_id))
        return {"ok": True}

    # --- sources of a series (Д-38) -------------------------------------------

    async def on_sources_list(self, env: Envelope) -> list[dict]:
        series_id = env.payload["series_id"]
        await self._require_series(series_id)
        return await self.repo.list_sources(series_id)

    async def on_sources_by_url(self, env: Envelope) -> list[dict]:
        return await self.repo.sources_by_url(env.payload["url"])

    async def on_sources_by_profile(self, env: Envelope) -> list[dict]:
        return await self.repo.sources_by_profile(int(env.payload["profile_id"]))

    async def on_source_add(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        await self._require_series(series_id)
        draft = checked_fields(env.payload["source"])
        refuse_mixed_kinds([each["source_type"] for each in await self.repo.list_sources(series_id)]
                           + [draft["source_type"]])
        source_id = await self.repo.add_source(series_id, draft)
        self.log.info("сериал %d: добавлен источник %d", series_id, source_id)
        self._publish_source("added", series_id, source_id)
        return {"id": source_id}

    async def on_source_update(self, env: Envelope) -> dict:
        source_id = env.payload["source_id"]
        stored = await self._require_source(source_id)
        values = checked_fields(env.payload["fields"], stored)
        if "source_type" in values:
            others = [each["source_type"] for each in await self.repo.list_sources(stored["series_id"])
                      if each["id"] != source_id]
            refuse_mixed_kinds(others + [values["source_type"]])
        # A value equal to the stored one is no change, so it raises no event and no recount downstream.
        applied = {name: value for name, value in values.items() if value != stored[name]}
        if applied:
            await self.repo.update_source(source_id, applied)
            self._publish_source("updated", stored["series_id"], source_id)
        return {"applied": applied}

    async def on_source_remove(self, env: Envelope) -> dict:
        source_id = env.payload["source_id"]
        stored = await self._require_source(source_id)
        if not await self.repo.remove_source(source_id):
            # Either the last source, or the series took it along between the read and the delete.
            await self._require_source(source_id)
            raise ValueError(reason("last_source", series_id=stored["series_id"], source_id=source_id))
        self.log.info("сериал %d: удалён источник %d", stored["series_id"], source_id)
        self._publish_source("removed", stored["series_id"], source_id)
        return {"ok": True}

    async def _require_series(self, series_id: int) -> None:
        if not await self.repo.series_exists(series_id):
            raise LookupError(f"сериал {series_id} не найден")

    async def _require_source(self, source_id: int) -> dict:
        source = await self.repo.get_source(source_id)
        if source is None:
            raise LookupError(f"источник {source_id} не найден")
        return source

    def _publish_source(self, what: str, series_id: int, source_id: int) -> None:
        self.publish_event(f"series.source.{what}",
                           SourceEvent(series_id=series_id, source_id=source_id).model_dump())

    def on_series_purged(self, env: Envelope) -> None:
        """Отчёт владельца: его таблицы вычищены."""
        p = env.payload
        self._purged.setdefault(p["series_id"], set()).add(p["owner"])

    async def _await_purge_reports(self, series_id: int) -> None:
        """Ждём отчёты владельцев о чистке: молчание — обрыв каскада."""
        deadline = time.monotonic() + PURGE_REPORT_TIMEOUT
        while time.monotonic() < deadline:
            if set(PURGE_OWNERS) <= self._purged.get(series_id, set()):
                break
            await asyncio.sleep(0.2)
        missing = set(PURGE_OWNERS) - self._purged.get(series_id, set())
        self._purged.pop(series_id, None)
        if missing:
            self.log.warning("сериал %d: не отчитались о чистке: %s",
                             series_id, ", ".join(sorted(missing)))

    def _spawn(self, coro) -> None:
        self._tasks.append(asyncio.create_task(coro))

    async def on_status_get(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        return {"series_id": series_id, "statuses": self.agg.statuses(series_id)}

    async def on_touch(self, env: Envelope) -> None:
        """Обновление last_scan_time («время жизни» карточки) после
        успешной загрузки/обработки — поведение старой системы.

        Реактивно толкаем новую дату в SSE (series.updated → дельта
        {id, last_scan_time}, фронт Object.assign в карточку) — иначе
        пилюля даты обновлялась только по F5. Формат — как у HTTP-пути
        (_iso: ' '→'T'), чтобы SSE и перезагрузка показывали одно. Без
        таймеров."""
        series_id = env.payload["series_id"]
        now = await self.repo.touch_scan_time(series_id)
        self.publish_event("series.updated", {
            "series_id": series_id,
            "last_scan_time": now.replace(" ", "T", 1)})
