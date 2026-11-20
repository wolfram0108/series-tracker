"""Module catalog: owner of the tables series and series_sources and the aggregator of statuses (Р-11).

A series and its sources (Д-38). A source is a page address, or `channel|query` of a VK channel; it carries a kind
(`torrent`, `vk_video`, `web_video`), a constructor profile and the choices made per source (the season of the Vibix
catalog, the players switched off, the name of the voice-over). The fields of a source are the contract `SeriesSource`.
Sources of any kinds live in one series (stage 15). The address, kind and profile of the series itself are gone; writing
those fields (url, source_type, parser_profile_id, vk_search_mode, vk_quality_priority) in catalog.series.create/update
is refused with a ValueError naming the field.

The list of sources `sources` (by position, then id) goes in every answer about a series.

Refusals are a ValueError with a code of core/reasons: `no_source` (a series made without a source), `last_source` (the only
source of a series removed), `source_exists` (the same address twice in a series), `profile_not_found` (no such profile).

The counts of `calc/catalog` (the checks of the fields, the cards and the packets told) are made here without the pool: a
card is built in 4 µs, the list of the 34 series of the service in 0.13 ms, the check of the fields of a source in 12–16 µs
(2026-11-21, the copy of the base of the service), less than a hop to the pool, and a request for a card must not queue
behind a count.

Events in:
  series.status.contribution {source, series_id, flags}: the fold of the module that owns tasks, published by it at its
      start (after its own reconcile) and at every change of its contribution. All false = the contribution is withdrawn.
  series.busy.contribution {source, series_id, busy}: the same for the block of the card (is_busy).
  gateway.sse.clients {count}: the count of SSE connections; at 0 every ephemeral viewing is dropped (a tab cannot hold a
      modal without a live SSE).
  series.purged {series_id, owner}: the report of the owner of series tables that its data of the deleted series is cleaned.

Commands:
  catalog.viewing.start {series_id} / catalog.viewing.stop {series_id}: the modal opened or closed (user actions).
  catalog.series.set_save_path {series_id, save_path} -> {ok}: `save_path` is our column; `library` writes it after a move.
  catalog.series.touch_scan_time {series_id}: the scan time of the card ("the lifetime" of the card) after a successful
      download or processing; the event series.updated carries it to the interface (the date pill) without a reload.

Queries (the answer is the model, as it is: nobody changes it):
  catalog.series.list {} -> SeriesListReply: every series as `SeriesCard`: its columns, `sources`, `statuses`, `is_busy`.
  catalog.series.get {series_id} -> SeriesCard
  catalog.sources.list {series_id} -> SourcesReply: by position, then id.
  catalog.sources.by_profile {profile_id} -> SourcesReply: the sources read by this profile of the rules (the constructor
      refuses to delete the profile by it).

Commands that answer:
  catalog.series.create {data} -> SeriesCard (+ the event series.added, then series.source.added for every initial source):
      data.sources is a list of sources (at least one), each the fields of SeriesSource without id and series_id; the
      position is the order in the list by default. The fields of data that are not in the schema of series are ignored.
  catalog.series.update {series_id, fields} -> {applied: {...}} (+ the event series.updated with the fields applied: the
      delta of SSE); the sources are not changed here. A series that is busy is not changed (the table of what is allowed).
  catalog.series.delete {series_id} -> {ok}: deletes the row of series with its sources and publishes series.deleted (there
      are no separate series.source.removed); the owners of the other tables clean theirs on the event (Р-19, the cascade of
      events) and report with series.purged: a silent owner means a broken cascade and goes to the log.
  catalog.sources.add {series_id, source} -> {id}: to the end of the list if the position is not given (+ the event
      series.source.added).
  catalog.sources.update {source_id, fields, series_id?} -> {applied: {...}}: only the fields that changed; without a change
      no event is published (+ the event series.source.updated).
  catalog.sources.remove {source_id} -> {ok}: the only source of a series cannot be removed (`last_source`);
      + series.source.removed.

Events out (a JSON packet of the model, as the stream of the interface and the dump of a run take it):
  series.added SeriesCard; series.updated SeriesUpdated (only the fields set); series.deleted SeriesDeleted;
  series.source.added / .updated / .removed SourceEvent {series_id, source_id, source, changed}, published after the write.
  series.status.changed / series.busy.changed SeriesStatusChanged {series_id, statuses, is_busy}: the first only when the set
      changed, the second when the busy mark of the card changed; both go to SSE series_updated (Р-18; it revives the branch
      of the front s.statuses, finding 24). Both carry the WHOLE delta {statuses, is_busy}: the handler of series_updated on
      the front removes the spinner of saving by a falsy is_busy, so every series_updated must carry the current is_busy
      (finding 38).
"""
from __future__ import annotations

import asyncio

from calc.catalog.aggregator import StatusAggregator
from calc.catalog.series import (absolute_path, card_of, fs_safe, list_of, source_event, status_of, touched_of, updated_of,
                                 written_of)
from calc.catalog.series_sources import changed_fields, checked_fields, checked_sources, refuse_removed_fields
from core.contracts import (BusyContribution, SeriesCard, SeriesDeleted, SeriesListReply, SeriesPurged, SeriesSource,
                            SourcesReply, StatusContribution, availability)
from core.db import Database
from core.envelope import Envelope
from core.module import BaseModule
from core.reasons import reason
from core.topics import (CATALOG_SERIES_CREATE, CATALOG_SERIES_DELETE, CATALOG_SERIES_GET, CATALOG_SERIES_LIST,
                         CATALOG_SERIES_SET_SAVE_PATH, CATALOG_SERIES_TOUCH_SCAN_TIME, CATALOG_SERIES_UPDATE,
                         CATALOG_SOURCES_ADD, CATALOG_SOURCES_BY_PROFILE, CATALOG_SOURCES_LIST, CATALOG_SOURCES_REMOVE,
                         CATALOG_SOURCES_UPDATE, CATALOG_VIEWING_START, CATALOG_VIEWING_STOP, GATEWAY_SSE_CLIENTS,
                         SERIES_ADDED, SERIES_BUSY_CHANGED, SERIES_BUSY_CONTRIBUTION, SERIES_DELETED, SERIES_PURGED,
                         SERIES_SOURCE_ADDED, SERIES_SOURCE_REMOVED, SERIES_SOURCE_UPDATED, SERIES_STATUS_CHANGED,
                         SERIES_STATUS_CONTRIBUTION, SERIES_UPDATED, Topic)

from .repository import CatalogRepository

# The owners of series tables: after series.deleted each cleans its own and reports with series.purged. Silence is a broken
# cascade.
PURGE_OWNERS = ("scan", "torrents", "library", "metadata", "composition", "planner", "sources")

# How long the reports of the owners are awaited before the cascade is counted as broken: a safeguard, not a way to wait
# (the reports are awaited by their signal).
PURGE_REPORT_TIMEOUT = 15.0

# The event of each change of a source.
_SOURCE_EVENTS: dict[str, Topic] = {"added": SERIES_SOURCE_ADDED, "updated": SERIES_SOURCE_UPDATED,
                                    "removed": SERIES_SOURCE_REMOVED}


class CatalogModule(BaseModule):
    name = "catalog"

    def __init__(self, bus, db: Database) -> None:
        self.repo = CatalogRepository(db)
        self.agg = StatusAggregator()
        # is_busy (the block of the card): the ephemeral set of contributions series_id -> {sources of active work} (Р-17).
        # An error no longer holds busy (finding 36), only active work does. A series is here only while some source holds it.
        self._busy: dict[int, set[str]] = {}
        # The reports of the owners about cleaning a deleted series: series_id -> owners.
        self._purged: dict[int, set[str]] = {}
        self._purge_waiters: dict[int, asyncio.Event] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle(SERIES_STATUS_CONTRIBUTION, self.on_contribution)
        self.handle(SERIES_BUSY_CONTRIBUTION, self.on_busy_contribution)
        self.handle(SERIES_PURGED, self.on_series_purged)
        self.handle(GATEWAY_SSE_CLIENTS, self.on_sse_clients)
        self.handle(CATALOG_SERIES_SET_SAVE_PATH, self.on_set_save_path)
        self.handle(CATALOG_VIEWING_START, self.on_viewing_start)
        self.handle(CATALOG_VIEWING_STOP, self.on_viewing_stop)
        self.handle(CATALOG_SERIES_LIST, self.on_series_list)
        self.handle(CATALOG_SERIES_GET, self.on_series_get)
        self.handle(CATALOG_SERIES_TOUCH_SCAN_TIME, self.on_touch)
        self.handle(CATALOG_SERIES_CREATE, self.on_series_create)
        self.handle(CATALOG_SERIES_UPDATE, self.on_series_update)
        self.handle(CATALOG_SERIES_DELETE, self.on_series_delete)
        self.handle(CATALOG_SOURCES_LIST, self.on_sources_list)
        self.handle(CATALOG_SOURCES_BY_PROFILE, self.on_sources_by_profile)
        self.handle(CATALOG_SOURCES_ADD, self.on_source_add)
        self.handle(CATALOG_SOURCES_UPDATE, self.on_source_update)
        self.handle(CATALOG_SOURCES_REMOVE, self.on_source_remove)

    # --- statuses: contributions and viewing ------------------------------------

    async def on_contribution(self, env: Envelope) -> None:
        p = StatusContribution.model_validate(env.payload)
        self._publish_if_changed(p.series_id, self.agg.set_contribution(p.series_id, p.source, p.flags))

    async def on_viewing_start(self, env: Envelope) -> None:
        series_id = env.payload["series_id"]
        self._publish_if_changed(series_id, self.agg.set_viewing(series_id, True))

    async def on_viewing_stop(self, env: Envelope) -> None:
        series_id = env.payload["series_id"]
        self._publish_if_changed(series_id, self.agg.set_viewing(series_id, False))

    async def on_busy_contribution(self, env: Envelope) -> None:
        p = BusyContribution.model_validate(env.payload)
        series_id, source = p.series_id, p.source
        sources = self._busy.setdefault(series_id, set())
        before = bool(sources)
        if p.busy:
            sources.add(source)
        else:
            sources.discard(source)
        now = bool(sources)
        if not sources:
            self._busy.pop(series_id, None)
        if before != now:
            self.publish_event(SERIES_BUSY_CHANGED,
                               status_of(series_id, self.agg.statuses(series_id), now).model_dump(mode="json"))

    async def on_set_save_path(self, env: Envelope) -> dict:
        """save_path is our column; library writes it after a move."""
        await self.repo.set_save_path(env.payload["series_id"], absolute_path(env.payload["save_path"]))
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
        self.publish_event(SERIES_STATUS_CHANGED,
                           status_of(series_id, statuses, series_id in self._busy).model_dump(mode="json"))

    # --- queries ---------------------------------------------------------------

    async def on_series_list(self, env: Envelope) -> SeriesListReply:
        rows = await self.repo.all_series()
        return list_of(rows, {row["id"]: self.agg.statuses(row["id"]) for row in rows}, self._busy.keys())

    async def on_series_get(self, env: Envelope) -> SeriesCard:
        series_id = env.payload["series_id"]
        row = await self.repo.get_series(series_id)
        if row is None:
            raise LookupError(reason("series_not_found", series_id=series_id))
        return card_of(row, self.agg.statuses(series_id), series_id in self._busy)

    # --- CRUD (Р-19) -----------------------------------------------------------

    async def on_series_create(self, env: Envelope) -> SeriesCard:
        data = env.payload["data"]
        refuse_removed_fields(data)
        sources = checked_sources(data.get("sources"))
        series_id = await self.repo.create_series(fs_safe(data), sources)
        card = card_of(await self.repo.get_series(series_id), self.agg.statuses(series_id), False)
        self.log.info("добавлен сериал %d: %s", series_id, card.name)
        self.publish_event(SERIES_ADDED, card.model_dump(mode="json"))
        # The first sources are sources that appeared like any added later, so consumers need no second rule.
        for source in card.sources:
            await self._publish_source("added", series_id, source.id)
        return card

    def _require_free(self, series_id: int) -> None:
        """The card and the sources of a busy series are not changed: a sync, a move or a renaming of it is under way
        and works by what it found (the table of what is allowed, as every owner of the series refuses)."""
        availability.require(availability.Action.EDIT,
                             availability.SERIES_SCANNING if series_id in self._busy else availability.SERIES_IDLE)

    async def on_series_update(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        fields = env.payload["fields"]
        self._require_free(series_id)
        refuse_removed_fields(fields)
        if "sources" in fields:
            raise ValueError(reason("series_sources_not_written"))
        await self._require_series(series_id)
        written = written_of(series_id, fs_safe(fields))
        applied = await self.repo.update_series(series_id, written.model_dump(exclude={"series_id"}, exclude_unset=True))
        if applied:
            # The delta for SSE series_updated: the fields applied and the statuses/is_busy it must carry (finding 38).
            self.publish_event(SERIES_UPDATED, updated_of(written, self.agg.statuses(series_id), series_id in self._busy)
                               .model_dump(mode="json", exclude_unset=True))
        return {"applied": applied}

    async def on_series_delete(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        await self._require_series(series_id)
        await self.repo.delete_series(series_id)
        self.agg.forget(series_id)
        self._busy.pop(series_id, None)
        self.log.info("сериал %d удалён; каскад — у владельцев таблиц",
                      series_id)
        self.publish_event(SERIES_DELETED, SeriesDeleted(series_id=series_id).model_dump(mode="json"))
        self.spawn(self._await_purge_reports(series_id))
        return {"ok": True}

    # --- sources of a series (decision D-38) ------------------------------------

    async def on_sources_list(self, env: Envelope) -> SourcesReply:
        series_id = env.payload["series_id"]
        await self._require_series(series_id)
        return SourcesReply(sources=tuple(await self.repo.list_sources(series_id)))

    async def on_sources_by_profile(self, env: Envelope) -> SourcesReply:
        return SourcesReply(sources=tuple(await self.repo.sources_by_profile(int(env.payload["profile_id"]))))

    async def on_source_add(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        await self._require_series(series_id)
        self._require_free(series_id)
        draft = checked_fields(env.payload["source"])
        source_id = await self.repo.add_source(series_id, draft)
        self.log.info("сериал %d: добавлен источник %d", series_id, source_id)
        await self._publish_source("added", series_id, source_id)
        return {"id": source_id}

    async def on_source_update(self, env: Envelope) -> dict:
        source_id = env.payload["source_id"]
        stored = await self._require_source(source_id)
        self._require_own(stored, env.payload.get("series_id"))
        self._require_free(stored.series_id)
        applied = changed_fields(checked_fields(env.payload["fields"], stored), stored)
        if applied:
            await self.repo.update_source(source_id, applied)
            await self._publish_source("updated", stored.series_id, source_id, sorted(applied))
        return {"applied": applied}

    async def on_source_remove(self, env: Envelope) -> dict:
        source_id = env.payload["source_id"]
        stored = await self._require_source(source_id)
        self._require_own(stored, env.payload.get("series_id"))
        self._require_free(stored.series_id)
        if not await self.repo.remove_source(source_id):
            # Either the last source, or the series took it along between the read and the delete.
            await self._require_source(source_id)
            raise ValueError(reason("last_source", series_id=stored.series_id, source_id=source_id))
        self.log.info("сериал %d: удалён источник %d", stored.series_id, source_id)
        await self._publish_source("removed", stored.series_id, source_id)
        return {"ok": True}

    async def _require_series(self, series_id: int) -> None:
        if not await self.repo.series_exists(series_id):
            raise LookupError(reason("series_not_found", series_id=series_id))

    @staticmethod
    def _require_own(stored: SeriesSource, series_id: int | None) -> None:
        """A caller that names the series it works in must not reach a source of another one."""
        if series_id is not None and stored.series_id != series_id:
            raise LookupError(reason("source_not_in_series", source_id=stored.id, series_id=series_id))

    async def _require_source(self, source_id: int) -> SeriesSource:
        source = await self.repo.get_source(source_id)
        if source is None:
            raise LookupError(reason("source_not_found", source_id=source_id))
        return source

    async def _publish_source(self, what: str, series_id: int, source_id: int, changed: list[str] | None = None) -> None:
        """After the write: the source as it is stored now travels with the event (none for one that went), an update with
        the fields it changed."""
        source = None if what == "removed" else await self.repo.get_source(source_id)
        self.publish_event(_SOURCE_EVENTS[what],
                           source_event(series_id, source_id, source, changed or ()).model_dump(mode="json"))

    def on_series_purged(self, env: Envelope) -> None:
        """The report of an owner: its tables are cleaned; the last report wakes the one awaiting the cascade."""
        p = SeriesPurged.model_validate(env.payload)
        reported = self._purged.setdefault(p.series_id, set())
        reported.add(p.owner)
        if set(PURGE_OWNERS) <= reported and p.series_id in self._purge_waiters:
            self._purge_waiters[p.series_id].set()

    async def _await_purge_reports(self, series_id: int) -> None:
        """Awaits the reports of the owners about cleaning by their signal, not by polling; silence past the term is a broken
        cascade."""
        done = self._purge_waiters.setdefault(series_id, asyncio.Event())
        if set(PURGE_OWNERS) <= self._purged.get(series_id, set()):
            done.set()
        try:
            await asyncio.wait_for(done.wait(), PURGE_REPORT_TIMEOUT)
        except asyncio.TimeoutError:
            pass
        finally:
            self._purge_waiters.pop(series_id, None)
        missing = set(PURGE_OWNERS) - self._purged.get(series_id, set())
        self._purged.pop(series_id, None)
        if missing:
            self.log.warning("сериал %d: не отчитались о чистке: %s",
                             series_id, ", ".join(sorted(missing)))

    async def on_touch(self, env: Envelope) -> None:
        """Renews last_scan_time (the "lifetime" of the card) after a successful download or processing: the behaviour of
        the old system.

        The new date is pushed to SSE reactively (series.updated -> the delta {id, last_scan_time}, the front does
        Object.assign into the card), otherwise the date pill refreshed only on F5. The format is that of the HTTP path
        (_iso: ' '->'T'), so that SSE and a reload show the same. No timers."""
        series_id = env.payload["series_id"]
        stamp = await self.repo.touch_scan_time(series_id)
        self.publish_event(SERIES_UPDATED, touched_of(series_id, stamp).model_dump(mode="json", exclude_unset=True))
