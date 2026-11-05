"""Module scan — the orchestrator of scans: the schedule, the torrent scan, and, for a series of kind `video`, the
synchronization of its sources and the choice of the reference of every episode (stage 10).

A video series is not read here. The tables of its pages are read by `sources`, the numbers come from the constructor
through `composition`; scan only waits for the tables, asks for the layout, chooses the reference of every slot
(season, episode, part) from it (`reference.py`) and keeps the plan: the rows of media_items (`candidates.py`,
`planner.py`) and the outcome of the choice in `scan_references`.

Queries:
  scan.runs.get → {runs: [{series_id, snapshot}]} — the open runs of syncs as their commands are written (an executor that
      starts again takes them before its works: И-60).
  scan.series.run {series_id, force_replace?, download?} → {changed, ...}. changed tells whether anything was
      actually changed: torrent — tasks were created; video — the references or the rows of the plan differ from
      what they were before the run. A video series answers {kind: "video", changed, sources: [{source_id, url,
      state: ready | stale | skipped, reason}], references: {by_level, own, other_player, none, unfit}, in_plan}.
      `stale` — the read of the page failed and the last complete table stays in force; `skipped` — it failed and there is no table.
      A scan of a series that is being scanned is refused by the table of what is allowed (`not_allowed`, the state
      `scanning`; finding 27) with the code every module refuses by. The steps of a video series
      are tied by signals: downloads.fs.sync, sources.table.get for every source at once and
      the wait for sources.table.ready / .failed of its page, composition.recompute (its answer means the layout is
      counted from these tables), composition.get with voices.settings.get, the choice and the plan, and with
      download (the default) downloads.dispatch and voices.dispatch. A failed step is the one signal of failure:
      the error carrier (scan_tasks.status='error') holds its reason. The wait for a table has a safeguard
      (`TABLE_WAIT_S`): when it runs out the source counts as failed, reason `table_wait_timeout`.
  scan.series.prepare {series_id} → the summary of a torrent scan without a download: the torrent is added to
      qBittorrent, split into its files (torrent_files) and left paused — the user picks the episodes in the
      composition, the next scan starts the download.
  scan.output.get {series_id} → [Reference]: every slot of the series. One Reference per slot: an episode with
      parts has one per part. Marks: by_level, own, other_player, none, unfit, and `off` for a slot the user switched
      off (an episode or a season switched off, or a row of the plan switched off): it takes nothing and plans no
      voice-over (core/contracts/reference.py).
  scan.output.switch {series_id, season, episode?, off} → {success}: switches an episode (without `episode`, a whole
      season) off or on. The memory of the series (ignored_seasons, ignored_episodes) and the rows of the plan change
      together, so the choice holds for the rows that exist and for those a new release brings; switching a season on
      switches on all of it. What changed goes out as scan.reference.changed. A series the catalog does not have is
      refused with the catalog's own answer.

Commands:
  scan.all.start {} — a pass over all series with auto_scan_enabled (what the internal scheduler does by the
      schedule from the settings).

Events (outgoing):
  scan.plan.updated {series_id} — the plan was counted again; it changes nothing on the disk (downloads counts the
      statuses of the series on it). The disk follows the plan in a sync alone: renaming.reprocess, then
      downloads.dispatch / voices.dispatch (with download) or downloads.settle / voices.settle (without).
  scan.reference.changed (ReferenceChanged: series_id and the whole Reference) — the reference or the mark of a slot
      differs from the stored one (a slot whose row left the layout reports `none` with the reason `reference_none`; a
      slot the user switched off reports `off` with the reason `reference_off`); sent
      after the write. Voices follow it, a window shows it without a second request.
  scan.status.changed {scanner_enabled, scan_interval, is_scanning, is_awaiting_tasks, next_scan_time} — the state
      of the scheduler (the contract of the old scanner_status_update).
  series.status.contribution — the fold of scanning/error (R-11); the carrier of the error is scan_tasks.status='error'.
  series.purged {series_id, owner} — report: the tables of the series are cleaned.
  scan.run.changed (RunChanged) — a sync of the series opened or closed its run: open from the start of the sync until
      every executor (downloads, voices) has no work of the series left (events work.series.state, queries
      <owner>.work.get). While it is open the series is busy (Action.EDIT and SYNC are refused, series.busy.contribution
      {source: sync}) and its plan stays as the sync found it: a recount an event asks for is done when the run closes.
      An open run is kept in scan_tasks (phase=working) and taken up again after a restart.
  scan.series.progress (SeriesReadProgress) — how far the read of a page is that a scan of the series waits for, per
      source, with the rate; `active` false ends it (read, failed or given up). The card of the series shows it.

Events (incoming):
  composition.changed {series_id, rev, episodes, summary_only} — the layout or the levers changed: the references
      and the plan of the series are counted again (the whole series: the count is a pure function, an episode the
      change did not touch comes out the same). Never starts a download. `summary_only` is skipped.
  voices.settings.changed — the order of players is an input of the choice: every video series is counted again.
  sources.table.ready / .failed {url, ...} — the verdicts a synchronization waits for.
  sources.table.progress (TableProgress) — how far a read is; told again as scan.series.progress for the series waiting.
  torrents.queue.changed {count} — the pipeline is empty: after a full pass the next scan is set when count=0.

Neighbours (all over the bus): catalog.series.get / .list (kind, sources), composition.get / .recompute,
voices.settings.get (the order of players), sources.table.get, downloads.dispatch / .settle / .fs.sync / .queue.get,
voices.dispatch / .settle, renaming.reprocess. Scan asks nothing of composition at start: its state is its own database.

Resumability: the plan of replacements of a torrent scan is in scan_tasks BEFORE it is carried out; additions are
idempotent (local infohash, R-2: adding again = existed); on_start goes on with the unfinished tasks. A video series
keeps its references and rows in the database; what a restart interrupted is brought back by the events of
composition and by the next scan.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from core import NO_HANDLER, BaseModule, BusRequestError, torrent_source
from core.deadlines import TABLE_WAIT_S
from core.contracts import (RUN_CHANGED, WORK_STATE, RunSnapshot, availability, CompositionReply, OutputSwitch, Reference,
                            ReferenceChanged, RunChanged, SeriesReadProgress, SeriesSource, SourceStatus, TableProgress,
                            VoiceSettingsView, WorkState)
from core.db import Database
from core.envelope import Envelope
from core.voice_names import Groups
from core.ignored import parse_memory
from core.reasons import reason
from . import candidates, output, planner, reference, sync
from .errors import ScanAborted, ScanError, SyncFailed
from .repository import ScanRepository
from .torrent import TorrentScan
from .sync import TableSignals

# The executors whose works a run of a video series waits for (`<owner>.work.get`, events `work.series.state`).
RUN_EXECUTORS = ("downloads", "voices")


@dataclass
class _RunWork:
    """What the run of a series knows of the work of its executors. An executor is working until a state of it says it
    is not (one not heard yet is working); of two states of one executor the later counted (`seq`) is in force, so a
    reply and an event that cross on the bus cannot undo each other. `polling` is set while the run asks every
    executor: the run is not closed from an event before every answer is in."""

    working: set[str]
    seen: dict[str, int]
    polling: bool = True

    def apply(self, state: WorkState) -> None:
        if state.seq <= self.seen.get(state.owner, -1):
            return
        self.seen[state.owner] = state.seq
        if state.active:
            self.working.add(state.owner)
        else:
            self.working.discard(state.owner)

    @property
    def done(self) -> bool:
        return not self.polling and not self.working


@dataclass(frozen=True)
class Refreshed:
    """What a count of the references and the plan came to."""

    in_plan: int
    references: list[dict]
    plan_changed: bool


def _sources_of(series: dict) -> list[SeriesSource]:
    return [SeriesSource.model_validate(each) for each in series.get("sources", [])]


def _torrent_source(series: dict) -> SeriesSource:
    """The source of a torrent series: the address and the profile of a torrent live in the source (Д-38)."""
    found = torrent_source(series)
    if found is None:
        raise ScanError(f"у сериала «{series.get('name')}» нет торрент-источника")
    return SeriesSource.model_validate(found)


class ScanModule(BaseModule):
    name = "scan"

    def __init__(self, bus, db: Database, *, scheduler_tick: float | None = 10.0,
                 table_wait_s: float = TABLE_WAIT_S) -> None:
        self.repo = ScanRepository(db)
        self._tick = scheduler_tick  # None — без планировщика (тесты)
        self._table_wait_s = table_wait_s
        self._signals = TableSignals(announce=self._announce_reading)
        self._locks: dict[int, asyncio.Lock] = {}
        self._running: set[int] = set()
        # The open runs: series -> its command in scan_tasks, the executors that still have work of it, and the
        # series whose recount an event asked for while its run was open (counted when the run ends).
        self._runs: dict[int, int] = {}
        self._working: dict[int, _RunWork] = {}
        self._owed: set[int] = set()
        self._closing: set[int] = set()
        self._owned: set[int] = set()  # the tasks of scan_tasks that the scans running here keep
        # The changes of a series by the user under way (scan.edit.begin/end) and the signal that none is left: a run
        # opens only after them, and none is admitted while a run is open or opening.
        self._edits: dict[int, set[str]] = {}
        # The configuration each open run works by (RunSnapshot), kept with its command in scan_tasks.
        self._snapshots: dict[int, RunSnapshot] = {}
        self._failed: set[int] = set()  # the series whose last run ended failed (its error stays until the next scan)
        self._edits_over: dict[int, asyncio.Event] = {}
        self._aborted: set[int] = set()
        self._scan_all_running = False
        self._awaiting_pipeline = False
        super().__init__(bus)
        self._torrents = TorrentScan(self.repo, self.request, self.log, self._aborted)

    def register(self) -> None:
        # concurrent: долгий скан одного сериала не должен блокировать
        # очередь запросов к модулю (защита от гонки — set _running).
        self.handle("scan.series.run", self.on_run, concurrent=True)
        self.handle("scan.series.prepare", self.on_prepare, concurrent=True)
        self.handle("scan.all.start", self.on_scan_all)
        self.handle("scan.media.list", self.on_media_list)
        self.handle("scan.media.downloaded_counts", self.on_downloaded_counts)
        self.handle("scan.seasons", self.on_seasons)
        self.handle("scan.item.set_ignored", self.on_set_ignored)
        self.handle("scan.output.switch", self.on_output_switch)
        self.handle("scan.edit.begin", self.on_edit_begin)
        self.handle("scan.edit.end", self.on_edit_end)
        self.handle("scan.output.get", self.on_output_get)
        self.handle("scan.status.get", self.on_status_get)
        self.handle("composition.changed", self.on_composition_changed)
        self.handle("voices.settings.changed", self.on_voice_settings_changed)
        self.handle("system.started", self.on_system_started)
        self.handle("sources.table.ready", self.on_table_ready)
        self.handle("sources.table.failed", self.on_table_failed)
        self.handle("sources.table.progress", self.on_table_progress)
        self.handle(WORK_STATE, self.on_work_state)
        self.handle("torrents.queue.changed", self.on_queue_changed)
        self.handle("series.deleted", self.on_series_deleted)
        self.handle("settings.changed", self.on_settings_changed)
        self.handle("gateway.sse.clients", self.on_sse_clients)
        self.handle("scan.runs.get", self.on_runs_get)

    async def on_start(self) -> None:
        # Носители прошлых ошибок — в свёртку (Р-11: поставщик публикует
        # вклад при старте после reconcile).
        for task in await self.repo.error_tasks():
            self._contribute(task["series_id"], scanning=False, error=True)
        if self._tick is not None:
            self._spawn(self._scheduler_loop())

    async def on_runs_get(self, env: Envelope) -> dict:
        """The runs of syncs that are open, as their commands are written: each with its snapshot. An executor that starts
        again takes them before it takes up its works, so that a pass of a run works by the snapshot (И-60)."""
        runs = []
        for task in await self.repo.incomplete_tasks():
            results = task["results_data"] or {}
            if results.get("video_sync"):
                runs.append({"series_id": task["series_id"], "snapshot": results.get("snapshot")})
        return {"runs": runs}

    async def on_system_started(self, env: Envelope) -> None:
        """Every module is up: the scans that were under way are taken up again. The work needs the catalog, the
        sources and the neighbours of a scan, which do not all answer while this module starts."""
        for task in await self.repo.incomplete_tasks():
            self.log.info("возобновление прерванного скана: задача %d, "
                          "сериал %d", task["id"], task["series_id"])
            self._spawn(self._resume(task))

    def _spawn(self, coro) -> None:
        self._tasks.append(asyncio.create_task(coro))

    # --- запуск сканов ---------------------------------------------------------

    async def on_run(self, env: Envelope) -> dict:
        p = env.payload
        force = p.get("force_replace")
        if force is None:  # как старый роут: режим из настройки отладки
            force = await self._debug_force_replace()
        # download=False — «полный процесс без загрузки» (сохранение свойств
        # VK-серии): пере-скан по профилю + переименование + усыновление, но
        # БЕЗ постановки новых загрузок (контракт «Сохранить → усыновление»).
        return await self._run_series(int(p["series_id"]), bool(force),
                                      download=p.get("download", True))

    async def on_media_list(self, env: Envelope) -> list[dict]:
        """Состав медиа-элементов серии (владелец строк — scan, Р-12);
        читают renaming (переобработка имён) и UI (этап 5)."""
        return await self.repo.items_for_series(env.payload["series_id"])

    async def on_set_ignored(self, env: Envelope) -> None:
        """is_ignored_by_user is our column; the UI and slicing set it.

        Switching a row off changes the real plan of the download, so scan.plan.updated goes out and downloads
        counts its tasks and the fold of statuses again (R-21). The row also leaves the output, so the references
        that changed with it are announced."""
        item = await self.repo.get_item(env.payload["unique_id"])
        if item is None:
            return
        ignored = bool(env.payload["is_ignored"])

        async def apply() -> None:
            await self.repo.set_ignored(item["unique_id"], ignored)
            await self._recount(item["series_id"])

        await self._announcing(item["series_id"], apply())
        self.publish_event("scan.plan.updated", {"series_id": item["series_id"]})

    # --- the admission of a change by the user (I-60) ------------------------------------------------------------

    async def on_edit_begin(self, env: Envelope) -> dict:
        """A change of the series by the user begins: refused while a sync of it runs or opens; admitted, it gets its
        number and holds off the opening of a sync until scan.edit.end with that number, which its admission sends once
        every request of the change is answered (core/admission.py)."""
        series_id = int(env.payload["series_id"])
        self._require_idle(series_id, availability.Action.EDIT)
        token = uuid.uuid4().hex
        self._edits.setdefault(series_id, set()).add(token)
        self._edits_over.setdefault(series_id, asyncio.Event()).clear()
        return {"ok": True, "token": token}

    async def on_edit_end(self, env: Envelope) -> None:
        """The change of that number is written whole; an end told twice is the same as once."""
        series_id = int(env.payload["series_id"])
        open_ = self._edits.get(series_id)
        if open_ is None:
            return
        open_.discard(env.payload.get("token") or "")
        if open_:
            return
        self._edits.pop(series_id, None)
        over = self._edits_over.pop(series_id, None)
        if over is not None:
            over.set()

    async def _edits_finished(self, series_id: int) -> None:
        """A run opens after the changes admitted before it are written whole; none is admitted meanwhile (the series
        is in `_running`). Each change is a request of the gateway with a fuse of its own, ended in `finally`."""
        over = self._edits_over.get(series_id)
        if over is not None and self._edits.get(series_id):
            await over.wait()

    async def on_output_switch(self, env: Envelope) -> dict:
        """An episode, or a whole season, is switched off or on by the user. The memory of the series and the rows of
        the plan change together, so that the choice holds for the rows that are there and for those that come with a
        new release; what the output shows of it goes out as references. Switching a season on switches on all of it."""
        request = OutputSwitch.model_validate(env.payload)
        series_id = request.series_id
        self._require_idle(series_id, availability.Action.EDIT, env)

        async def apply() -> None:
            series = await self.request("catalog.series.get", {"series_id": series_id})
            seasons, memory = self._ignored_seasons(series), self._ignored_episodes(series)
            if request.episode is None:
                seasons = seasons | {request.season} if request.off else seasons - {request.season}
                if not request.off:
                    memory.pop(request.season, None)
                    await self.repo.set_ignored_bulk_for_season(series_id, request.season, False)
            else:
                numbers = memory.get(request.season, set())
                numbers = numbers | {request.episode} if request.off else numbers - {request.episode}
                memory[request.season] = numbers
                await self.repo.set_ignored_for_episode(series_id, request.season, request.episode, request.off)
            await self.request("catalog.series.update", {"series_id": series_id, "fields": {
                "ignored_seasons": json.dumps(sorted(seasons)),
                "ignored_episodes": json.dumps({str(season): sorted(numbers) for season, numbers in memory.items() if numbers}),
            }})
            await self._recount(series_id)

        await self._announcing(series_id, apply())
        self.publish_event("scan.plan.updated", {"series_id": series_id})
        return {"success": True}

    async def on_output_get(self, env: Envelope) -> list[dict]:
        """The references of the series that are wanted, in the order of season, episode, part."""
        series_id = int(env.payload["series_id"])
        series = await self.request("catalog.series.get", {"series_id": series_id})
        shown = await self._output_of(series)
        return [output.reference_of(each).model_dump(mode="json") for each in shown]

    async def _output_of(self, series: dict) -> list[dict]:
        items = {each["unique_id"]: each for each in await self.repo.items_for_series(series["id"])}
        return output.shown(await self.repo.references(series["id"]), items, self._ignored_episodes(series),
                            self._ignored_seasons(series))

    async def _recount(self, series_id: int) -> None:
        """The plan follows the choice of the user: the rows of a video series are counted again, so that the rows
        that left the plan lose their place in it and downloads drops the tasks that wait for them, and the rows that
        came back have theirs. A torrent series has no plan of references."""
        try:
            series = await self.request("catalog.series.get", {"series_id": series_id})
        except BusRequestError as exc:
            if "LookupError" in str(exc):
                return
            raise
        if series.get("kind") == "video":
            await self._refresh(series, announce=True)

    async def _announcing(self, series_id: int, action):
        """Awaits `action` (a change of the rows that may switch a reference off) and announces what changed in the
        output of the series. A series the catalog does not have, or a torrent one, has no output to announce."""
        try:
            series = await self.request("catalog.series.get", {"series_id": series_id})
            before = await self._output_of(series)
        except (BusRequestError, asyncio.TimeoutError):
            return await action
        result = await action
        # The action may have changed the memory of the series: the output after it is read with the series as it is now.
        try:
            series = await self.request("catalog.series.get", {"series_id": series_id})
            after = await self._output_of(series)
        except (BusRequestError, asyncio.TimeoutError):
            return result
        for each in output.changes(before, after):
            self._announce(series_id, each)
        return result

    def _announce(self, series_id: int, changed: Reference) -> None:
        self.publish_event("scan.reference.changed", ReferenceChanged(
            series_id=series_id, **changed.model_dump()).model_dump(mode="json"))

    async def on_downloaded_counts(self, env: Envelope) -> dict:
        """{counts: {series_id: n}} — скачанные VK-эпизоды (Р-19)."""
        return {"counts": await self.repo.downloaded_counts()}

    async def on_seasons(self, env: Envelope) -> dict:
        """{seasons: [..]} — реальные сезоны VK-серии (distinct media_items.
        season) для агрегата TMDB по нескольким сезонам."""
        return {"seasons": await self.repo.seasons_for_series(
            env.payload["series_id"])}

    async def on_series_deleted(self, env: Envelope) -> None:
        """Каскад Р-19: владелец чистит media_items и scan_tasks; идущий
        скан серии прерывается (Д2 — как у загрузки, нарезки и переноса)."""
        series_id = env.payload["series_id"]
        self._aborted.add(series_id)
        # A count of the plan that is writing finishes first; one that comes after sees the series aborted.
        async with self._lock(series_id):
            await self.repo.delete_for_series(series_id)
        self._locks.pop(series_id, None)
        if self._runs.pop(series_id, None) is not None:  # its works go with the series: the run is over
            self._working.pop(series_id, None)
            self._owed.discard(series_id)
            self._announce_run(series_id, False)
        self.publish_event("series.purged",
                           {"series_id": series_id, "owner": self.name})

    async def on_prepare(self, env: Envelope) -> dict:
        """Контракт «Получить данные» торрент-композиции: раздача попадает
        в клиент и разбирается на состав, закачка не начинается."""
        return await self._prepare_series(int(env.payload["series_id"]))

    async def on_scan_all(self, env: Envelope) -> dict:
        if self._scan_all_running:
            self.log.warning("полный проход уже идёт — повтор отклонён")
            return {"started": False}
        force = bool((env.payload or {}).get("force_replace")) \
            or await self._debug_force_replace()
        self._spawn(self._scan_all(force))
        return {"started": True}

    async def on_status_get(self, env: Envelope) -> dict:
        """Состояние планировщика (контракт GET /api/scanner/status)."""
        return await self._status_payload()

    async def on_settings_changed(self, env: Envelope) -> None:
        """Р-20: смена настроек сканера пересчитывает расписание от
        текущего момента (вместо немедленного полного скана старой
        системы — лишняя нагрузка на трекеры)."""
        key = env.payload.get("key")
        if key not in ("scanner_agent_enabled", "scan_interval_minutes"):
            return
        if await self._setting("scanner_agent_enabled", "false") == "true":
            await self._schedule_next()
        else:
            await self._broadcast_status()

    async def on_sse_clients(self, env: Envelope) -> None:
        """Новый SSE-клиент: публикуем текущее состояние, чтобы вкладка
        настроек не жила на дефолтах до первого изменения (Р-20)."""
        if env.payload.get("count", 0) > 0:
            await self._broadcast_status()

    async def _debug_force_replace(self) -> bool:
        return await self._setting("debug_force_replace", "false") == "true"

    # --- signals of the neighbours ---------------------------------------------------------------

    def on_table_ready(self, env: Envelope) -> None:
        self._signals.ready(env.payload["url"])

    def on_table_failed(self, env: Envelope) -> None:
        self._signals.failed(env.payload["url"], env.payload.get("reason"))

    def on_table_progress(self, env: Envelope) -> None:
        self._signals.progress(TableProgress.model_validate(env.payload))

    def _announce_reading(self, packet: SeriesReadProgress) -> None:
        self.publish_event("scan.series.progress", packet.model_dump(mode="json"))

    async def on_composition_changed(self, env: Envelope) -> None:
        """The layout or the levers changed: the references and the plan follow, nothing is downloaded."""
        if env.payload.get("summary_only"):
            return  # no row and no lever moved, only what a window shows
        await self._follow_layout(int(env.payload["series_id"]))

    async def on_voice_settings_changed(self, env: Envelope) -> None:
        """The order of players decides ties of the choice, so a new order may change references."""
        try:
            listing = await self.request("catalog.series.list", {})
        except (BusRequestError, asyncio.TimeoutError) as exc:
            self.log.warning("порядок плееров изменён, список сериалов недоступен: %s", exc)
            return
        for series in listing:
            if series.get("kind") == "video":
                await self._follow_layout(series["id"])

    async def _follow_layout(self, series_id: int) -> None:
        """Counts the references of a video series again after an event; a failure is logged, the next event or the
        next scan counts again. While a run of the series is open its plan is the one the sync found (the snapshot):
        the recount is owed and done when the run ends."""
        if series_id in self._runs or series_id in self._running:
            self._owed.add(series_id)
            return
        try:
            series = await self.request("catalog.series.get", {"series_id": series_id})
            if series.get("kind") == "video":
                await self._refresh(series)
        except ScanAborted as exc:
            self.log.info("пересчёт референсов прерван: %s", exc)
        except (BusRequestError, asyncio.TimeoutError, ScanError) as exc:
            self.log.warning("сериал %d: референсы не пересчитаны: %s", series_id, exc)
        except Exception:  # noqa: BLE001 — one series must not stop the handling of the events of the others
            self.log.exception("сериал %d: сбой пересчёта референсов", series_id)

    def _lock(self, series_id: int) -> asyncio.Lock:
        return self._locks.setdefault(series_id, asyncio.Lock())

    def _alive(self, series_id: int) -> None:
        if series_id in self._aborted:
            raise ScanAborted(f"сериал {series_id} удалён во время скана")

    async def _step(self, topic: str, payload: dict, *, units: int = 1):
        """A request of a synchronization: any failure of it is the one signal that the step failed."""
        try:
            return await self.request(topic, payload, units=units)
        except (BusRequestError, asyncio.TimeoutError) as exc:
            if "LookupError" in str(exc):
                raise ScanAborted(f"сериал {payload.get('series_id')} удалён во время скана") from exc
            self.log.error("синхронизация: запрос %s не выполнен: %s: %s", topic, type(exc).__name__, exc)
            raise SyncFailed(reason("scan_step_failed", step=topic)) from exc

    async def _run_series(self, series_id: int, force: bool = False,
                          download: bool = True, resume: int | None = None, inventory: bool = False) -> dict:
        """A scan of one series. The command is written down before anything is waited for, so that a restart finds
        it: a video synchronization is a task of `scan_tasks` until it ends (`resume` is the task a restart found).
        `inventory` is a scan of the schedule: the channels of VK are read whole, the quick search may miss a new upload."""
        self._require_idle(series_id)
        self._running.add(series_id)
        try:
            await self._edits_finished(series_id)
        except BaseException:
            self._running.discard(series_id)
            raise
        self._failed.discard(series_id)
        self._contribute(series_id, scanning=True, error=False)
        intent = resume
        try:
            if intent is None:
                intent = await self.repo.create_task(series_id, [], {"video_sync": True, "download": download,
                                                                     "inventory": inventory})
            self._owned.add(intent)
            series = await self.request("catalog.series.get",
                                        {"series_id": series_id})
            video = series.get("kind") == "video"
            if not video:  # a torrent scan writes the task of its own plan
                await self.repo.delete_task(intent)
                intent = None
            if video:
                if not any(each.parser_profile_id for each in _sources_of(series)):
                    self._announce_run(series_id, True)
                    raise SyncFailed(reason("scan_no_profile"))
                # The run is told once, with its snapshot: no step of it has begun before (I-60).
                await self._take_snapshot(series, intent)
                self._announce_run(series_id, True)
            elif not _torrent_source(series).parser_profile_id:
                raise ScanError(
                    f"для сериала «{series.get('name')}» не назначен "
                    "профиль правил")
            # Прошлая ошибка сбрасывается новым сканом (Р-11).
            await self.repo.delete_error_tasks(series_id)
            series = await self._relocate(series)
            if video:
                result = await self._sync_video(series, download, inventory)
                await self._hold_run(series_id, intent)
                intent = None  # the run keeps the command now: it ends when the works of the run end
            else:
                await self._reprocess_names(series_id)
                # Without download a torrent series is a sync of its names alone: the trackers are not asked.
                result = (await self._torrents.scan(series, _torrent_source(series), force) if download
                          else {"changed": False})
            self._contribute(series_id, scanning=series_id in self._runs, error=series_id in self._failed)
            # Многосезонный агрегат TMDB: реальные сезоны могли измениться
            # (новый сезон/файлы) — пересчитать число серий по сезонам.
            self.send_command("metadata.seasons.recompute",
                              {"series_id": series_id})
            return result
        except ScanAborted as exc:
            self.log.info("скан сериала %d прерван: %s", series_id, exc)
            if intent is not None:
                await self.repo.delete_task(intent)
            self._announce_run(series_id, False)
            self._contribute(series_id, scanning=False, error=False)
            return {"changed": False, "aborted": True}
        except Exception as exc:
            # Носитель ошибки: error-запись в scan_tasks, если её не
            # оставила торрент-фаза. Серии нет — носитель не пишем.
            alive = await self._series_alive(series_id)
            if intent is not None and alive:
                await self.repo.set_error(intent, str(exc))  # the written command becomes the carrier of its failure
            elif intent is not None:
                await self.repo.delete_task(intent)
            elif alive and not await self._has_error_task(series_id):
                task_id = await self.repo.create_task(series_id, [])
                await self.repo.set_error(task_id, str(exc))
            self._announce_run(series_id, False)
            self._contribute(series_id, scanning=False, error=True)
            raise
        finally:
            self._running.discard(series_id)
            self._owned.discard(intent)

    async def _prepare_series(self, series_id: int) -> dict:
        """Состав раздачи для композиции без запуска закачки.

        Отдельный путь, а не флаг скана: здесь нет ни переобработки имён,
        ни диспетчеризации — только «добудь раздачу и покажи, что в ней»."""
        self._require_idle(series_id)
        self._running.add(series_id)
        self._contribute(series_id, scanning=True, error=False)
        try:
            series = await self.request("catalog.series.get",
                                        {"series_id": series_id})
            if series.get("kind") == "video":
                raise ScanError("подготовка состава — только для торрентов")
            await self.repo.delete_error_tasks(series_id)
            result = await self._torrents.scan(series, _torrent_source(series), False, prepare=True)
            self._contribute(series_id, scanning=False, error=False)
            return result
        except ScanAborted as exc:
            self.log.info("подготовка сериала %d прервана: %s", series_id, exc)
            self._contribute(series_id, scanning=False, error=False)
            return {"source_type": "torrent", "tasks_created": 0,
                    "changed": False, "aborted": True}
        except Exception as exc:
            if await self._series_alive(series_id) \
                    and not await self._has_error_task(series_id):
                task_id = await self.repo.create_task(series_id, [])
                await self.repo.set_error(task_id, str(exc))
            self._contribute(series_id, scanning=False, error=True)
            raise
        finally:
            self._running.discard(series_id)

    async def _has_error_task(self, series_id: int) -> bool:
        return any(t["series_id"] == series_id
                   for t in await self.repo.error_tasks())

    async def _series_alive(self, series_id: int) -> bool:
        """Есть ли серия в каталоге: носитель ошибки пишется только
        существующей серии — иначе внешний ключ не даст записать."""
        try:
            await self.request("catalog.series.get",
                                {"series_id": series_id})
            return True
        except BusRequestError:
            return False

    async def _relocate(self, series: dict) -> dict:
        """The first step of a sync: the move of the folder a save planned (И-59); everything after it works in the new
        folder. A failed move fails the sync, and its plan waits for the next one."""
        try:
            reply = await self.request("library.relocation.apply", {"series_id": series["id"]})
        except BusRequestError as exc:
            if NO_HANDLER in str(exc):
                return series
            raise
        if not reply["moved"]:
            return series
        return await self.request("catalog.series.get", {"series_id": series["id"]})

    async def _reprocess_names(self, series_id: int) -> None:
        """Переобработка имён до скана — request/reply вместо
        create+sleep-поллинга до 600 с (находка 16)."""
        try:
            await self.request("renaming.reprocess", {"series_id": series_id})
        except BusRequestError as exc:
            if NO_HANDLER in str(exc):
                self.log.warning("модуль renaming ещё не подключён — "
                                 "переобработка перед сканом пропущена")
            else:
                raise

    # --- video branch: synchronisation and the plan of references ----------------------------------

    async def _sync_video(self, series: dict, download: bool, inventory: bool = False) -> dict:
        """Tables of all sources -> layout -> references and plan -> (with download) the download and the voice-overs.

        Each step starts on the signal that the one before it ended, and any step that fails fails the scan."""
        series_id = series["id"]
        # Disk check before the scan: a downloaded file that is gone is reset from completed to pending so that dispatch
        # fetches it again; an unreachable path is absorbed inside downloads.on_fs_sync.
        try:
            await self.request("downloads.fs.sync", {"series_id": series_id})
        except BusRequestError as exc:
            self.log.warning("сверка диска перед видео-сканом пропущена: %s", exc)
        before = await self._fingerprint(series_id)
        self._alive(series_id)
        tables = await sync.sync_tables(_sources_of(series), self.request, self._signals, self._table_wait_s,
                                        inventory=inventory)
        for each in tables:
            if each.reason:
                self.log.warning("сериал %d, источник %d: страница %s — %s, причина %s", series_id, each.source_id,
                                 each.url, each.state, each.reason)
        self._alive(series_id)
        snapshot = self._snapshots.get(series_id)
        await self._step("composition.recompute", {"series_id": series_id, **(
            {"snapshot": snapshot.model_dump(mode="json")} if snapshot is not None else {})}, units=len(_sources_of(series)))
        self._alive(series_id)
        refreshed = await self._refresh(series, announce=True)
        # The disk follows the plan here and nowhere else: a recount on an event (a rule saved, an episode switched off)
        # changes the plan alone; a sync names the files after the plan, then removes, displaces and withdraws what the
        # plan does not want. Only a scan with download fetches what is missing.
        self._alive(series_id)
        await self._reprocess_names(series_id)
        self._alive(series_id)
        await self._hand_over(series_id, download)
        changed = before != await self._fingerprint(series_id)
        return {"kind": "video", "changed": changed, "sources": [each.as_dict() for each in tables],
                "references": output.marks(refreshed.references), "in_plan": refreshed.in_plan}

    async def _hand_over(self, series_id: int, download: bool = True) -> None:
        """The work goes to those who do it, and the command of the user is kept until each of them has written the work
        down: a receiver answers after its record, so a stop at any point of the hand-over finds the command again and
        the next run gives the work again (a receiver takes the same work twice as once). Without download the receivers
        bring the disk in line with the plan (`downloads.settle`, `voices.settle`) and fetch nothing."""
        settings = {"settings": self._snapshots[series_id].voices} if series_id in self._snapshots else {}
        answers = await asyncio.gather(
            self.request("downloads.dispatch" if download else "downloads.settle", {"series_id": series_id}),
            self.request("voices.dispatch" if download else "voices.settle", {"series_id": series_id, **settings}),
            return_exceptions=True)
        for answer in answers:
            if isinstance(answer, BusRequestError) and NO_HANDLER in str(answer):
                continue  # nobody does that kind of work in this process
            if isinstance(answer, BaseException):
                raise answer

    def _state(self, series_id: int) -> str:
        return availability.SERIES_SCANNING if series_id in self._running or series_id in self._runs \
            else availability.SERIES_IDLE

    def _require_idle(self, series_id: int, action: availability.Action = availability.Action.SYNC,
                      env: Envelope | None = None) -> None:
        """An action on a series that is being synchronized is refused by the table of what is allowed, as every module
        refuses: a sync is busy from its start until every work of its run has ended. A change admitted before the sync
        (its request carries the number of an admission still open) is not a new one: the opening sync waits for it."""
        if env is not None and env.admission and env.admission in self._edits.get(series_id, ()):
            return
        availability.require(action, self._state(series_id))

    # --- the run: a sync and every work it gave out ---------------------------------------------------------------

    def _announce_run(self, series_id: int, active: bool) -> None:
        """The modules hear the run (the snapshot, the refusals); the card of the series is busy for all of it."""
        if not active:
            self._snapshots.pop(series_id, None)
        self.publish_event(RUN_CHANGED, RunChanged(series_id=series_id, active=active,
                                                   snapshot=self._snapshots.get(series_id) if active else None
                                                   ).model_dump(mode="json"))
        self.publish_event("series.busy.contribution", {"source": "sync", "series_id": series_id, "busy": active})

    async def _hold_run(self, series_id: int, intent: int) -> None:
        """The pass of the sync is over and its works are given out: the run stays open, with its command written down,
        until each executor says it has no work of the series left. An executor that has none at once closes nothing."""
        task = await self.repo.get_task(intent)
        await self.repo.update_results(intent, {**(task["results_data"] if task else {}), "phase": "working"})
        self._runs[series_id] = intent
        await self._ask_working(series_id)

    async def _take_snapshot(self, series: dict, intent: int) -> None:
        """The rules of the profiles of the sources and the settings of the voice-overs as they are when the sync starts,
        written with its command: every step of the run works by them, and a run a restart takes up again too (И-60)."""
        series_id = series["id"]
        task = await self.repo.get_task(intent)
        results = task["results_data"] if task else {}
        if results.get("snapshot") is not None:
            self._snapshots[series_id] = RunSnapshot.model_validate(results["snapshot"])
            return
        profiles = sorted({each.parser_profile_id for each in _sources_of(series) if each.parser_profile_id})
        snapshot = RunSnapshot(profiles=(await self._exported("rules.profiles.export", {"profile_ids": profiles}))
                               .get("profiles", {}),
                               voices=(await self._exported("voices.settings.export", {})).get("values", {}))
        await self.repo.update_results(intent, {**results, "snapshot": snapshot.model_dump(mode="json")})
        self._snapshots[series_id] = snapshot

    async def _exported(self, topic: str, payload: dict) -> dict:
        try:
            return await self.request(topic, payload)
        except (BusRequestError, asyncio.TimeoutError) as exc:
            if NO_HANDLER in str(exc):
                return {}  # no such owner in this process: nothing of it to keep
            self.log.error("синхронизация: запрос %s не выполнен: %s: %s", topic, type(exc).__name__, exc)
            raise SyncFailed(reason("scan_step_failed", step=topic)) from exc

    async def _ask_working(self, series_id: int) -> None:
        """What each executor has of the series now; the run waits for those that have work (their events end it).
        Until its answer is in, an executor counts as working, and the run is closed only after every answer."""
        run = self._working[series_id] = _RunWork(working=set(RUN_EXECUTORS), seen={})
        try:
            for owner in RUN_EXECUTORS:
                try:
                    run.apply(WorkState.model_validate(await self.request(f"{owner}.work.get", {"series_id": series_id})))
                except BusRequestError as exc:
                    if NO_HANDLER not in str(exc):
                        raise
                    run.working.discard(owner)  # nobody does that kind of work in this process
        finally:
            run.polling = False
        if run.done:
            await self._close_run(series_id)

    async def on_work_state(self, env: Envelope) -> None:
        state = WorkState.model_validate(env.payload)
        run = self._working.get(state.series_id)
        if state.series_id not in self._runs or run is None:
            return
        run.apply(state)
        if run.done:
            await self._close_run(state.series_id)

    async def _close_run(self, series_id: int) -> None:
        """Every work of the run has ended: the containers of the series are made (the last step of the run, ЭТАП_11),
        then the command goes, the series is free, and a recount an event asked for while the run was open is done now
        (a plan; the disk follows it at the next sync)."""
        if series_id not in self._runs or series_id in self._closing:
            return  # the run is closing already: one packing, one end
        self._closing.add(series_id)
        failure = None
        try:
            packed = await self.request("downloads.pack", {"series_id": series_id})
            if packed.get("failed"):
                failure = reason("sync_pack_failed", failed=packed["failed"])
        except BusRequestError as exc:
            if NO_HANDLER not in str(exc):
                self.log.error("сериал %d: упаковка не выполнена: %s", series_id, exc)
                failure = reason("scan_step_failed", step="downloads.pack")
        finally:
            self._closing.discard(series_id)
        intent = self._runs.pop(series_id, None)
        self._working.pop(series_id, None)
        # The series is free either way; a failed last step ends the run as failed, with its reason kept as the error
        # of the series until the next sync, not as a success.
        if intent is not None:
            if failure is None:
                await self.repo.delete_task(intent)
            else:
                await self.repo.set_error(intent, failure)
        if failure is None:
            self.log.info("сериал %d: синхронизация завершена — все её работы закончились", series_id)
        else:
            self._failed.add(series_id)
            self.log.error("сериал %d: синхронизация завершена с ошибкой: %s", series_id, failure)
        self._announce_run(series_id, False)
        self._contribute(series_id, scanning=series_id in self._running, error=failure is not None)
        if series_id in self._owed:
            self._owed.discard(series_id)
            await self._follow_layout(series_id)

    async def _fingerprint(self, series_id: int) -> tuple:
        """The references and the rows of the plan as stored: two equal fingerprints mean the run changed nothing."""
        records = await self.repo.references(series_id)
        rows = sorted(each["unique_id"] for each in await self.repo.items_for_series(series_id))
        return tuple(tuple(each.items()) for each in records), tuple(rows)

    async def _refresh(self, series: dict, *, announce: bool = False) -> Refreshed:
        """Counts the references of the series from the layout as it stands, writes them with the plan in one
        transaction and announces what changed. Counting twice with the same inputs changes nothing."""
        series_id = series["id"]
        async with self._lock(series_id):
            self._alive(series_id)
            # The excluded rows too: they tell a video taken out by the configuration from one a read did not bring.
            layout = CompositionReply.model_validate(await self._step(
                "composition.get", {"series_id": series_id, "include_excluded": True}))
            # A run chooses by the settings it took when it started (RunSnapshot); a recount outside a run by the current.
            snapshot = self._snapshots.get(series_id)
            voices = VoiceSettingsView.model_validate(await self._step(
                "voices.settings.get", {"values": snapshot.voices} if snapshot is not None else {}))
            existing = await self.repo.items_for_series(series_id)
            stored = await self.repo.references(series_id)
            chosen = reference.choose(layout.rows, layout.settings, voices.priority,
                                      self._standing_sources(series, layout),
                                      previous=[output.reference_of(each) for each in stored],
                                      player_groups=Groups.of(voices.player_groups),
                                      on_disk=candidates.on_disk(existing))
            memory, seasons = self._ignored_episodes(series), self._ignored_seasons(series)
            queued = await self._queued(series_id)
            # When the queue cannot be told, every row that is not fetched is held: none is turned to another video.
            placements = candidates.place(series_id, chosen, existing, queued if queued is not None else {
                each["unique_id"] for each in existing if not candidates.is_downloaded(each)})
            winners = {each.slot: each.unique_id for each in placements}
            by_id = {each["unique_id"]: each for each in existing}
            inserts = [candidates.new_row(series_id, each, memory) for each in placements if each.is_new]
            updates = []
            for each in placements:
                if not each.is_new:
                    changed = {name: value for name, value in each.fields.items() if by_id[each.unique_id].get(name) != value}
                    if changed:
                        updates.append((each.unique_id, changed))
            deletes = self._leftovers(existing, set(winners.values()), queued)
            rows = candidates.rows_after(existing, inserts, updates, deletes)
            statuses, superseded = planner.plan_with_supersession(rows, winners, seasons)
            for unique_id in self._removed_by_configuration(series, layout, rows, set(winners.values()), seasons,
                                                            statuses):
                statuses[unique_id] = "removed"
                superseded.pop(unique_id, None)
            records = [{**each.reference.model_dump(mode="json"), "unique_id": winners.get(each.slot)}
                       for each in chosen]
            shown_before = output.shown(stored, by_id, memory, seasons)
            shown_after = output.shown(records, {each["unique_id"]: each for each in rows}, memory, seasons)
            plan_changed = bool(inserts or updates or deletes) or any(
                (by_id[unique_id].get("plan_status"), by_id[unique_id].get("superseded_by"))
                != (statuses.get(unique_id, "candidate"), superseded.get(unique_id))
                for unique_id in by_id if unique_id not in deletes)
            self._alive(series_id)
            try:
                await self.repo.save_outcome(series_id, inserts=inserts, updates=updates, deletes=deletes,
                                             statuses=statuses, superseded=superseded, references=records)
            except Exception as exc:
                if "FOREIGN KEY" in str(exc):
                    raise ScanAborted(f"сериал {series_id} удалён во время скана") from exc
                raise
        self.log.info("сериал %d: раскладка %d, слотов %d, в плане %d, добавлено строк %d, убрано %d", series_id,
                      layout.rev, len(chosen), sum(1 for each in statuses.values() if each == "in_plan_single"),
                      len(inserts), len(deletes))
        if announce or plan_changed:
            self.publish_event("scan.plan.updated", {"series_id": series_id})
        for each in output.changes(shown_before, shown_after):
            self._announce(series_id, each)
        return Refreshed(sum(1 for each in statuses.values() if each == "in_plan_single"), shown_after, plan_changed)

    @staticmethod
    def _standing_sources(series: dict, layout: CompositionReply) -> list[int]:
        """The sources of the series in their order that can bring rows: one taken away, or one without a profile, holds
        no reference of its own (its rows are gone by the user's choice, not by a read)."""
        mine = {each.id for each in _sources_of(series)}
        return [each.source_id for each in layout.sources
                if each.status != SourceStatus.NO_PROFILE and (not mine or each.source_id in mine)]

    @staticmethod
    def _removed_by_configuration(series: dict, layout: CompositionReply, rows: list[dict], winners: set[str],
                                  seasons: set[int], statuses: dict[str, str]) -> list[str]:
        """The rows the configuration took out of the plan: those of a season the user switched off, of a source they took
        away, of a source whose profile they took away, and those whose row of the layout the configuration excluded (a
        rule, a switched-off player) while nothing else holds their slot. This is the one place the reason is told;
        downloads removes the files of the rows it names and no others, and only in a sync, so that a site that fails is
        never taken for a choice of the user and a rule being edited removes nothing. A displaced row whose slot has a
        winner waits for it: its file goes once the winner is on disk. With no source known the rows of a source are not
        told from a layout that has not been counted yet."""
        gone = [item["unique_id"] for item in rows if item.get("season") in seasons]
        left_out = {(row.source_id, row.row_key) for row in layout.rows if row.left_out}
        gone += [item["unique_id"] for item in rows
                 if item["unique_id"] not in gone and statuses.get(item["unique_id"]) == "redundant"
                 and (item.get("source_id"), item.get("row_key")) in left_out]
        mine = {each.id for each in _sources_of(series)}
        if not mine:
            return gone
        without_profile = {each.source_id for each in layout.sources if each.status == SourceStatus.NO_PROFILE}
        return gone + [item["unique_id"] for item in rows
                       if item["unique_id"] not in winners and item.get("source_id") is not None
                       and item["unique_id"] not in gone
                       and (item["source_id"] not in mine or item["source_id"] in without_profile)]

    async def _queued(self, series_id: int) -> set[str] | None:
        """The rows of the series that have a task in the queue of downloads; None when the queue cannot be told."""
        try:
            queue = await self.request("downloads.queue.get", {})
        except BusRequestError as exc:
            if NO_HANDLER not in str(exc):
                self.log.warning("сериал %d: очередь загрузок недоступна: %s", series_id, exc)
                return None
            return set()
        except asyncio.TimeoutError:
            self.log.warning("сериал %d: очередь загрузок не ответила", series_id)
            return None
        return {task["task_key"] for task in queue.get("tasks", []) if task.get("series_id") == series_id}

    @staticmethod
    def _leftovers(existing: list[dict], kept: set[str], queued: set[str] | None) -> list[str]:
        """The rows to delete: those that stand for nothing and have no task in the queue of downloads. When the
        queue cannot be told, nothing is deleted this time, so that no task is left without its row."""
        return [] if queued is None else candidates.obsolete(existing, kept, queued)

    @staticmethod
    def _ignored_episodes(series: dict) -> dict[int, set[int]]:
        """Episodes the user switched off in advance, as for torrents: their rows are created switched off,
        so neither the download nor the voice-overs take them."""
        try:
            return parse_memory(json.loads(series.get("ignored_episodes") or "{}"))
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _ignored_seasons(series: dict) -> set[int]:
        """Сезоны, помеченные пользователем к игнору (VU11): их эпизоды в
        план не попадают."""
        try:
            return {int(s) for s in
                    json.loads(series.get("ignored_seasons") or "[]")}
        except (json.JSONDecodeError, TypeError, ValueError):
            return set()

    # --- торрент-ветка -------------------------------------------------------------

    async def _resume(self, task: dict) -> None:
        """A task a restart found: a video synchronization is run again from its written command, a torrent scan
        goes on from its journal."""
        if not task["results_data"].get("video_sync"):
            await self._resume_task(task)
            return
        if task["id"] in self._owned:  # the scan of this very command is running in this process
            return
        series_id = task["series_id"]
        if task["results_data"].get("phase") == "working":  # the pass was over: the run waits for its works again
            self._runs[series_id] = task["id"]
            if task["results_data"].get("snapshot") is not None:
                self._snapshots[series_id] = RunSnapshot.model_validate(task["results_data"]["snapshot"])
            self._announce_run(series_id, True)
            self._contribute(series_id, scanning=True, error=False)
            try:
                await self._ask_working(series_id)
            except Exception:  # noqa: BLE001 - the run stays open; an event of an executor or the next start closes it
                self.log.exception("сериал %d: работы прогона не опрошены после перезапуска", series_id)
            return
        if series_id in self._running:  # a command that came after the restart is the scan of the series already
            await self.repo.delete_task(task["id"])
            return
        try:
            await self._run_series(series_id, download=bool(task["results_data"].get("download", True)),
                                   resume=task["id"], inventory=bool(task["results_data"].get("inventory", False)))
        except Exception:  # noqa: BLE001 - the failure is kept as the error task of the series
            self.log.exception("возобновление синхронизации сериала %d не удалось", series_id)

    async def _resume_task(self, task: dict) -> None:
        series_id = task["series_id"]
        if series_id in self._running:
            return
        self._running.add(series_id)
        self._contribute(series_id, scanning=True, error=False)
        try:
            series = await self.request("catalog.series.get",
                                        {"series_id": series_id})
            await self._torrents.execute(
                task["id"], series, task["task_data"],
                dict(task["results_data"]))
            self._contribute(series_id, scanning=False, error=False)
        except Exception:
            self.log.exception("возобновление задачи %d не удалось",
                               task["id"])
            self._contribute(series_id, scanning=False, error=True)
        finally:
            self._running.discard(series_id)

    # --- свёртка статусов (Р-11) ------------------------------------------------

    def _contribute(self, series_id: int, *, scanning: bool,
                    error: bool) -> None:
        self.publish_event("series.status.contribution", {
            "source": "scan", "series_id": series_id,
            "flags": {"scanning": scanning, "error": error}})

    # --- планировщик автосканирования ----------------------------------------------

    async def _scheduler_loop(self) -> None:
        await self._broadcast_status()
        while True:
            await asyncio.sleep(self._tick)
            try:
                await self._scheduler_tick()
            except Exception:  # noqa: BLE001 — цикл не должен умирать
                self.log.exception("ошибка такта планировщика")

    async def _scheduler_tick(self) -> None:
        if self._scan_all_running or self._awaiting_pipeline:
            return
        if await self._setting("scanner_agent_enabled", "false") != "true":
            return
        next_ts = await self._setting("next_scan_timestamp", None)
        if not next_ts:
            return
        next_time = datetime.fromisoformat(next_ts)
        if next_time.tzinfo is None:
            next_time = next_time.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= next_time:
            self.log.info("настало время планового сканирования")
            self._spawn(self._scan_all())

    async def _scan_all(self, force: bool = False) -> None:
        self._scan_all_running = True
        await self._broadcast_status()
        stopped = False
        try:
            all_series = await self.request("catalog.series.list", {})
            for s in all_series:
                if not s.get("auto_scan_enabled"):
                    continue
                if await self._pipeline_busy(s["id"]):
                    self.log.info("автоскан: сериал %d пропущен — у него "
                                  "активные задачи конвейера", s["id"])
                    continue
                if self._state(s["id"]) != availability.SERIES_IDLE:
                    self.log.info("автоскан: сериал %d пропущен — идёт его синхронизация", s["id"])
                    continue
                try:
                    await self._run_series(s["id"], force, inventory=True)
                except Exception as exc:  # noqa: BLE001 — проход не прерываем
                    self.log.error("автоскан сериала %d: %s", s["id"], exc)
        except asyncio.CancelledError:
            stopped = True  # the module is going away: its neighbours are not asked anything on the way out
            raise
        finally:
            self._scan_all_running = False
            if not stopped:
                self._awaiting_pipeline = True
                await self._broadcast_status()
                await self._check_pipeline_empty()

    async def _pipeline_busy(self, series_id: int) -> bool:
        """Поведение старого автоскана: сериал с активными задачами
        конвейера не сканируется (ручной скан — без этой проверки)."""
        try:
            queue = await self.request("torrents.queue.get",
                                       {"series_id": series_id})
            return queue.get("count", 0) > 0
        except BusRequestError as exc:
            if NO_HANDLER in str(exc):
                return False
            raise

    async def _check_pipeline_empty(self) -> None:
        """После прохода: если конвейер уже пуст — назначить следующий
        скан сразу, не дожидаясь события."""
        try:
            queue = await self.request("torrents.queue.get", {})
            count = queue.get("count", 0) if isinstance(queue, dict) \
                else len(queue)
        except BusRequestError as exc:
            if NO_HANDLER not in str(exc):
                raise
            self.log.warning("конвейер torrents ещё не подключён — "
                             "следующий скан назначается сразу")
            count = 0
        if count == 0:
            await self._schedule_next()

    async def on_queue_changed(self, env: Envelope) -> None:
        if self._awaiting_pipeline and env.payload.get("count", 0) == 0:
            self.log.info("конвейер опустел — назначаю следующий скан")
            await self._schedule_next()

    async def _schedule_next(self) -> None:
        self._awaiting_pipeline = False
        interval = int(await self._setting("scan_interval_minutes", "60"))
        next_time = datetime.now(timezone.utc) + timedelta(minutes=interval)
        await self.request("settings.value.set", {
            "key": "next_scan_timestamp", "value": next_time.isoformat()})
        self.log.info("следующее сканирование: %s", next_time.isoformat())
        await self._broadcast_status()

    async def _setting(self, key: str, default: str | None) -> str | None:
        reply = await self.request("settings.value.get", {"key": key})
        value = reply.get("value")
        return value if value is not None else default

    async def _status_payload(self) -> dict:
        """Форма старого scanner_status_update / GET /api/scanner/status."""
        return {
            "scanner_enabled":
                await self._setting("scanner_agent_enabled", "false") == "true",
            "scan_interval":
                int(await self._setting("scan_interval_minutes", "60")),
            "is_scanning": self._scan_all_running or bool(self._running),
            "is_awaiting_tasks": self._awaiting_pipeline,
            "next_scan_time": await self._setting("next_scan_timestamp", None),
        }

    async def _broadcast_status(self) -> None:
        """Контракт старого scanner_status_update (индикатор сканера)."""
        self.publish_event("scan.status.changed",
                           await self._status_payload())
