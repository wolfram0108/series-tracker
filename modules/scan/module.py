"""Module scan — the orchestrator of syncs: the schedule and the run of the synchronization of a series.

A series is not read here. The tables of its pages are read by `sources`, the numbers come from the constructor
through `composition`; scan waits for the tables, asks for the layout to be counted from them and hands the series to
the planner (modules/planner), which chooses the reference of every episode, does the work and applies it. The run stays
open until the planner has no work of the series left.

What the schedule, the states of the executors, the changes of a series by the user, the tables of the sources and the commands a restart
found come to is counted in calc/scan; this module asks the neighbours, holds the tasks and writes its table. Every reply and event is a
model of core/contracts/scan.py (and work.py), handed over as it is; an event goes on the bus as its JSON.

Queries:
  scan.runs.get {} → OpenRuns {runs: [{series_id, snapshot}], as_of} — the open runs of syncs as their commands are written (an
      executor that starts again takes them before its works: И-60), as of the generation `as_of` of the announcements of
      runs (RunChanged.seq): a series whose announcement is newer than that is told by the announcement, not by the reading.
  scan.series.run SeriesRun {series_id, download?, season?, episode?} → SyncResult {changed, ...}. With a season (and an
      episode of it) the sync covers it alone (SyncScope): the tables of every source are read and the
      layout is counted as for the whole series, and the planner carries out the groups of its plan that hold the scope (a
      compilation with its cuts goes whole); the series is busy for all of it. The scope is written with the command: a restart takes the sync up as it was.
      changed tells whether anything was
      actually changed: the references or the rows of the plan differ from
      what they were before the run. The answer is {changed, sources: [{source_id, url,
      state: ready | stale | skipped, reason}]}; a sync the user stopped answers {changed: false, cancelled: true}, one cut by the deletion of
      its series {changed: false, aborted: true}.
      `stale` — the read of the page failed and the last complete table stays in force; `skipped` — it failed and there is no table.
      A scan of a series that is being scanned is refused by the table of what is allowed (`not_allowed`, the state
      `scanning`; finding 27) with the code every module refuses by. The steps of a sync
      are tied by signals: sources.table.refresh for every source at once and the wait for sources.table.ready / .failed
      of its page, composition.recompute (its answer means the layout is counted from these tables), then
      planner.sync {download}: the planner counts the plan of the series and carries it out. A failed step is the one
      signal of failure:
      the error carrier (scan_tasks.status='error') holds its reason. The wait for a table has a safeguard
      (`TABLE_WAIT_S`): when it runs out the source counts as failed, reason `table_wait_timeout`.
  scan.run.cancel SeriesStop {series_id} → RunCancelled {cancelled}: the user stops the sync of the series: the pass of the scan stops (the
      reads of its pages too, sources.table.cancel), then every work of its run (planner.sync.cancel). Nothing of it is
      applied, the run closes and is not taken up again at the start. False when no sync of the series went.
  scan.output.switch OutputSwitch {series_id, season, episodes?, off} → OutputSwitched {success}: switches episodes of a season, one or
      many at once in one change (without `episodes`, the whole season), off or on in the memory of the series (ignored_seasons,
      ignored_episodes), for the rows that exist and for those a new release brings; switching a season on switches on
      all of it. The plan of the series reads the memory.
      A series the catalog does not have is refused with the catalog's own answer.
  scan.status.get {} → ScanStatus — the state of the scheduler (GET /api/scanner/status).
  scan.edit.begin EditBegin {series_id, token?} → EditAdmitted {ok, token, ended?}, scan.edit.end EditEnd {series_id, token}: the admission
      of a change of the series by the user (И-60): refused for a series the catalog does not have and while a sync of it runs or opens;
      admitted under its number, it holds off the opening of a sync until scan.edit.end with that number.

Commands:
  scan.all.start {} → ScanStarted {started} — a pass over all series with auto_scan_enabled (what the internal scheduler does at the times of
      day of the schedule, calc/schedule.py).
  scan.schedule.set ScheduleSet {schedule} → ScheduleSaved {ok, schedule?, reason?}: the times of day of
      the syncs and of the checks of the torrent releases (the setting `schedule`), refused (`schedule_invalid`) when
      a time or the mode is not one; the scheduler and the torrents module take the new one from settings.changed.

Events (outgoing):
  scan.status.changed ScanStatus {scanner_enabled, is_scanning, next_scan_time, next_check_time, schedule, checks} — the state of
      the scheduler: the next sync and the next check of the releases by the schedule, and the times of the checks.
  series.status.contribution — the fold of scanning/error (Р-11); the carrier of the error is scan_tasks.status='error'.
  series.purged {series_id, owner} — report: the tables of the series are cleaned.
  scan.run.changed (RunChanged) — a sync of the series opened or closed its run: open from the start of the sync until
      the planner has no work of the series left (events work.series.state, query planner.work.get). While it is open
      the series is busy (Action.EDIT and SYNC are refused, series.busy.contribution {source: sync}) and its plan stays
      as the sync found it: a recount an event asks for is done when the run closes. An open run is kept in scan_tasks
      (phase=working) and taken up again after a restart.

Events (incoming):
  sources.table.ready / .failed {url, ...} — the verdicts a synchronization waits for.
  work.series.state (WorkState) — what an executor has of the work of a series.

Neighbours (all over the bus): catalog.series.get / .list (sources), composition.recompute, sources.table.refresh,
planner.sync / .work.get, library.relocation.apply. Scan asks nothing of composition at start: its state is its own database.

Resumability: the command of a sync is in scan_tasks BEFORE anything is waited for; a restart runs it again (`on_system_started`, once every
module is up). A series keeps its references and rows in the database; what a restart interrupted is brought back by the events of
composition and by the next scan.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from calc.scan import edits, runs
from calc.scan.output import switched
from calc.scan.status import delay_until, is_due, next_pass, schedule_set, status_of
from calc.scan.sync import open_runs, resume_step
from calc.schedule import SCHEDULE_KEY
from core.contracts import (BusyContribution, EditAdmitted, EditBegin, EditEnd, OpenRuns, OutputSwitch, OutputSwitched, RunCancelled,
                            RunChanged, RunSnapshot, ScanStarted, ScanStatus, ScheduleSaved, ScheduleSet, SeriesCard, SeriesDeleted,
                            SeriesPurged, SeriesRecount, SeriesRun, SeriesStop, SettingValue, SseClients, StatusContribution,
                            SyncResult, SyncScope, TableFailed, TableReady, WorkState, availability)
from core.db import Database
from core.envelope import Envelope
from core.module import NO_HANDLER, BaseModule, BusRequestError
from core.reasons import describe, reason
from core.topics import (CATALOG_SERIES_GET, CATALOG_SERIES_LIST, CATALOG_SERIES_UPDATE, COMPOSITION_RECOMPUTE, EDIT_STUCK_S,
                         GATEWAY_SSE_CLIENTS, LIBRARY_RELOCATION_APPLY, METADATA_SEASONS_RECOMPUTE, PLANNER_SYNC, PLANNER_SYNC_CANCEL,
                         PLANNER_WORK_GET, RULES_PROFILES_EXPORT, SCAN_ALL_START, SCAN_EDIT_BEGIN, SCAN_EDIT_END, SCAN_OUTPUT_SWITCH,
                         SCAN_RUN_CANCEL, SCAN_RUN_CHANGED, SCAN_RUNS_GET, SCAN_SCHEDULE_SET, SCAN_SERIES_RUN, SCAN_STATUS_CHANGED,
                         SCAN_STATUS_GET, SERIES_BUSY_CONTRIBUTION, SERIES_DELETED, SERIES_PURGED, SERIES_STATUS_CONTRIBUTION,
                         SETTINGS_CHANGED, SETTINGS_VALUE_GET, SETTINGS_VALUE_SET, SOURCES_PAGES_CHECK, SOURCES_TABLE_CANCEL,
                         SOURCES_TABLE_FAILED, SOURCES_TABLE_READY, SYSTEM_STARTED, TABLE_WAIT_S, VOICES_SETTINGS_EXPORT, WORK_SERIES_STATE,
                         Topic)

from . import sync
from .errors import ScanAborted, SyncFailed
from .repository import ScanRepository
from .sync import TableSignals

# The executors whose work a run of a series waits for, with the query of what each has of a series (and the events `work.series.state`):
# the planner downloads, cuts, aligns, builds and applies the plan of the series (modules/planner).
RUN_EXECUTORS = {"planner": PLANNER_WORK_GET}


class ScanModule(BaseModule):
    name = "scan"

    def __init__(self, bus, db: Database, *, scheduler: bool = True, table_wait_s: float = TABLE_WAIT_S) -> None:
        self.repo = ScanRepository(db)
        self._scheduler = scheduler  # False: no scheduled passes (tests)
        # A change of the schedule or of the switch wakes the scheduler, which sleeps until the next sync of the day.
        self._wake = asyncio.Event()
        self._table_wait_s = table_wait_s
        self._signals = TableSignals()
        self._locks: dict[int, asyncio.Lock] = {}
        self._running: set[int] = set()
        self._passes: dict[int, asyncio.Task] = {}  # the pass of the scan of each series, a task of its own
        self._cancelling: set[int] = set()  # the series whose sync the user stops
        # The open runs: series -> its command in scan_tasks, and what each knows of the work of its executors.
        self._runs: dict[int, int] = {}
        self._working: dict[int, runs.Work] = {}
        self._owned: set[int] = set()  # the tasks of scan_tasks that the scans running here keep
        # The changes of a series by the user under way (scan.edit.begin/end) and the signal that none is left: a run
        # opens only after them, and none is admitted while a run is open or opening. An end that came before its
        # beginning (the caller stopped waiting for the grant and gave it back) is kept in the edits: the beginning that
        # comes later opens nothing.
        self._edits = edits.Edits()
        self._edits_over: dict[int, asyncio.Event] = {}
        # The configuration each open run works by (RunSnapshot), kept with its command in scan_tasks.
        self._snapshots: dict[int, RunSnapshot] = {}
        # The generation of the announcements of runs (RunChanged.seq), in the order they are published.
        self._run_seq = 0
        self._failed: set[int] = set()  # the series whose last run ended failed (its error stays until the next scan)
        self._aborted: set[int] = set()
        self._scan_all_running = False
        super().__init__(bus)

    def register(self) -> None:
        # concurrent: a long scan of one series must not block the queue of requests to the module (the race is
        # guarded by the set _running).
        self.handle(SCAN_SERIES_RUN, self.on_run, concurrent=True)
        self.handle(SCAN_RUN_CANCEL, self.on_run_cancel, concurrent=True)
        self.handle(SCAN_ALL_START, self.on_scan_all)
        self.handle(SCAN_OUTPUT_SWITCH, self.on_output_switch)
        self.handle(SCAN_EDIT_BEGIN, self.on_edit_begin)
        self.handle(SCAN_EDIT_END, self.on_edit_end)
        self.handle(SCAN_STATUS_GET, self.on_status_get)
        self.handle(SCAN_SCHEDULE_SET, self.on_schedule_set)
        self.handle(SYSTEM_STARTED, self.on_system_started)
        self.handle(SOURCES_TABLE_READY, self.on_table_ready)
        self.handle(SOURCES_TABLE_FAILED, self.on_table_failed)
        self.handle(WORK_SERIES_STATE, self.on_work_state)
        self.handle(SERIES_DELETED, self.on_series_deleted)
        self.handle(SETTINGS_CHANGED, self.on_settings_changed)
        self.handle(GATEWAY_SSE_CLIENTS, self.on_sse_clients)
        self.handle(SCAN_RUNS_GET, self.on_runs_get)

    async def on_start(self) -> None:
        # The carriers of past errors go to the fold (Р-11: the provider publishes its contribution at the start, after its reconcile).
        for task in await self.repo.error_tasks():
            self._contribute(task["series_id"], scanning=False, error=True)
        if self._scheduler:
            self.spawn(self._scheduler_loop(), name="scan:scheduler", lasting=True)

    async def on_runs_get(self, env: Envelope) -> OpenRuns:
        """The runs of syncs that are open, as their commands are written: each with its snapshot. An executor that starts
        again takes them before it takes up its works, so that a pass of a run works by the snapshot (И-60)."""
        as_of = self._run_seq
        return open_runs(await self.repo.incomplete_tasks(), as_of)

    async def on_system_started(self, env: Envelope) -> None:
        """Every module is up: the scans that were under way are taken up again. The work needs the catalog, the
        sources and the neighbours of a scan, which do not all answer while this module starts."""
        for task in await self.repo.incomplete_tasks():
            self.log.info("возобновление прерванного скана: задача %d, "
                          "сериал %d", task["id"], task["series_id"])
            self.spawn(self._resume(task))

    # --- the start of the scans ---------------------------------------------------------

    async def on_run(self, env: Envelope) -> SyncResult:
        request = SeriesRun.model_validate(env.payload)
        # A season, or an episode of it: the sync covers it alone.
        scope = SyncScope.of({"season": request.season, "episode": request.episode})
        return await self._run_series(request.series_id, download=request.download, scope=scope)

    # --- the admission of a change by the user (И-60) ------------------------------------------------------------

    async def on_edit_begin(self, env: Envelope) -> EditAdmitted:
        """A change of the series by the user begins: refused for a series the catalog does not have and while a sync of it
        runs or opens; admitted under its
        number, it holds off the opening of a sync until scan.edit.end with that number, which its admission sends once
        every request of the change is answered (core/admission.py). The number is the caller's, known before the grant:
        a caller that stops waiting for the grant gives it back by that number, and an end that came first is kept, so
        the grant that follows it opens nothing."""
        request = EditBegin.model_validate(env.payload)
        series_id = request.series_id
        token = request.token or uuid.uuid4().hex
        # A series the catalog does not have is refused with the catalog's answer, for every change at once. It is
        # asked first: what follows runs without a pause, so an end that comes meanwhile is not missed.
        try:
            await self.request(CATALOG_SERIES_GET, {"series_id": series_id})
        except BusRequestError as exc:
            if NO_HANDLER not in str(exc):
                raise
        self._edits, early = edits.ended_early(self._edits, series_id, token)
        if early:
            return EditAdmitted(ok=True, token=token, ended=True)
        self._require_idle(series_id, availability.Action.EDIT)
        self._edits = edits.admitted(self._edits, series_id, token)
        self._edits_over.setdefault(series_id, asyncio.Event()).clear()
        return EditAdmitted(ok=True, token=token)

    async def on_edit_end(self, env: Envelope) -> None:
        """The change of that number is written whole; an end told twice is the same as once. An end of a number not
        admitted yet is the end of a grant its caller gave up waiting for: it is kept for that grant."""
        request = EditEnd.model_validate(env.payload)
        self._edits, none_left = edits.ended(self._edits, request.series_id, request.token or "")
        if none_left:
            over = self._edits_over.pop(request.series_id, None)
            if over is not None:
                over.set()

    async def _edits_finished(self, series_id: int) -> None:
        """A run opens after the changes admitted before it are written whole; none is admitted meanwhile (the series
        is in `_running`). A change ends when every request of it is answered (core/admission.py). One that does not end
        within the longest answer a change may wait for is a failure, told in the log; the run goes on waiting for it."""
        over = self._edits_over.get(series_id)
        if over is None or not edits.under_way(self._edits, series_id):
            return
        try:
            await asyncio.wait_for(over.wait(), EDIT_STUCK_S)
        except asyncio.TimeoutError:
            self.log.error("сериал %d: синхронизация ждёт правку, допущенную до неё, дольше %.0f с — запрос правки не "
                           "ответил (открытых допусков: %d); синхронизация ждёт дальше", series_id, EDIT_STUCK_S,
                           len(edits.under_way(self._edits, series_id)))
            await over.wait()

    async def on_output_switch(self, env: Envelope) -> OutputSwitched:
        """Episodes (one or many at once), or a whole season, are switched off or on by the user: the memory of the series
        holds it, for the rows that are there and for those a new release brings; the plan of the series reads it.
        Switching a season on switches on all of it."""
        request = OutputSwitch.model_validate(env.payload)
        self._require_idle(request.series_id, availability.Action.EDIT, env)
        series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": request.series_id})
        # The memory of the series is the switch: the plan of the series reads it (modules/planner).
        await self.request(CATALOG_SERIES_UPDATE, {"series_id": request.series_id, "fields": switched(series, request)})
        return OutputSwitched(success=True)

    async def on_series_deleted(self, env: Envelope) -> None:
        """The cascade of Р-19: the owner cleans scan_tasks; a scan of the series under way is stopped (Д-2, as a download
        and a relocation are)."""
        series_id = SeriesDeleted.model_validate(env.payload).series_id
        self._aborted.add(series_id)
        # A count of the plan that is writing finishes first; one that comes after sees the series aborted.
        async with self._lock(series_id):
            await self.repo.delete_for_series(series_id)
        self._locks.pop(series_id, None)
        self._edits = edits.forgotten(self._edits, series_id)
        if self._runs.pop(series_id, None) is not None:  # its works go with the series: the run is over
            self._working.pop(series_id, None)
            self._announce_run(series_id, False)
        self.publish_event(SERIES_PURGED, SeriesPurged(series_id=series_id, owner=self.name).model_dump(mode="json"))

    async def on_scan_all(self, env: Envelope) -> ScanStarted:
        if self._scan_all_running:
            self.log.warning("полный проход уже идёт — повтор отклонён")
            return ScanStarted(started=False)
        self._scan_all_running = True
        self.spawn(self._scan_all())
        return ScanStarted(started=True)

    async def on_schedule_set(self, env: Envelope) -> ScheduleSaved:
        saved, stored = schedule_set(ScheduleSet.model_validate(env.payload).schedule)
        if stored is not None:
            await self.request(SETTINGS_VALUE_SET, {"key": SCHEDULE_KEY, "value": stored})
        return saved

    async def on_status_get(self, env: Envelope) -> ScanStatus:
        """The state of the scheduler (the contract of GET /api/scanner/status)."""
        return await self._status()

    async def on_settings_changed(self, env: Envelope) -> None:
        """Р-20: a change of the settings of the scanner counts the schedule again from the present moment (instead of the
        immediate full scan of the old system: an extra load on the trackers)."""
        key = SettingValue.model_validate(env.payload).key
        if key not in ("scanner_agent_enabled", SCHEDULE_KEY):
            return
        await self._schedule_next()
        self._wake.set()

    async def on_sse_clients(self, env: Envelope) -> None:
        """A new SSE client: the current state is published, so that the settings tab does not live on the defaults
        until the first change (Р-20)."""
        if SseClients.model_validate(env.payload).count > 0:
            await self._broadcast_status()

    # --- signals of the neighbours ---------------------------------------------------------------

    def on_table_ready(self, env: Envelope) -> None:
        self._signals.ready(TableReady.model_validate(env.payload).url)

    def on_table_failed(self, env: Envelope) -> None:
        failed = TableFailed.model_validate(env.payload)
        self._signals.failed(failed.url, failed.reason)

    def _lock(self, series_id: int) -> asyncio.Lock:
        return self._locks.setdefault(series_id, asyncio.Lock())

    def _alive(self, series_id: int) -> None:
        if series_id in self._aborted:
            raise ScanAborted(reason("series_deleted_in_scan", series_id=series_id))

    async def _step(self, topic: Topic, payload: dict, *, units: int = 1):
        """A request of a synchronization: any failure of it is the one signal that the step failed."""
        try:
            return await self.reply_of(topic, payload, units=units)
        except (BusRequestError, asyncio.TimeoutError) as exc:
            if "LookupError" in str(exc):
                raise ScanAborted(reason("series_deleted_in_scan", series_id=payload.get("series_id"))) from exc
            self.log.error("синхронизация: запрос %s не выполнен: %s: %s", topic.name, type(exc).__name__, exc)
            raise SyncFailed(reason("scan_step_failed", step=topic.name)) from exc

    async def _run_series(self, series_id: int, download: bool = True, resume: int | None = None,
                          scope: SyncScope | None = None) -> SyncResult:
        """A scan of one series, a task of its own: a stop of it by the user (scan.run.cancel) stops it alone, not the
        pass over all series it may be a part of."""
        # A new sync of a series whose source is read by a provider that is not up does not open (a sync the start took up
        # again goes on as it was). Asked before the series is taken: nothing is written, the user is told why.
        if resume is None and (why := await self._provider_blocks(series_id)) is not None:
            raise availability.NotAllowed(why)
        # Refused or taken at once, in the handler: a second scan of the series is refused from this moment on.
        self._require_idle(series_id)
        self._running.add(series_id)
        passing = self.spawn(self._pass_series(series_id, download, resume, scope), name=f"scan.pass:{series_id}")
        self._passes[series_id] = passing
        try:
            return await passing
        finally:
            if self._passes.get(series_id) is passing:
                self._passes.pop(series_id, None)
            self._running.discard(series_id)

    async def _provider_blocks(self, series_id: int, series: SeriesCard | None = None) -> str | None:
        """The reason a series cannot be synchronized now: one of its pages is read by a provider of external sources
        that is not up (`sources.pages.check`). None when nothing stops it, and when the question cannot be put — the sync
        then says for itself what is wrong with the series or the page."""
        try:
            if series is None:
                series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
            urls = [each.url for each in series.sources]
            if not urls:
                return None
            blocked = (await self.reply_of(SOURCES_PAGES_CHECK, {"urls": urls})).blocked
        except (BusRequestError, asyncio.TimeoutError):
            return None
        return blocked[0].reason if blocked else None

    async def on_run_cancel(self, env: Envelope) -> RunCancelled:
        """The user stops the sync of a series: the pass of the scan, the reads of its pages (failed, and not begun again
        at the start; a read the start took up again stops too, though no pass waits for it), then every work of its run (planner.sync.cancel: downloads, jobs of the service,
        the build). The run closes as every run closes, and nothing of the stopped sync is applied."""
        series_id = SeriesStop.model_validate(env.payload).series_id
        stopped = False
        self._cancelling.add(series_id)
        try:
            passing = self._passes.get(series_id)
            if passing is not None and not passing.done():
                passing.cancel()
                await asyncio.wait({passing})
                stopped = True
            # A read of a page of the series stops whether a pass waits for it or not: one taken up again at the start
            # of the process belongs to no pass, and the button stops it all the same.
            series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
            for source in series.sources:
                try:
                    stopped = (await self.reply_of(SOURCES_TABLE_CANCEL, {"url": source.url})).cancelled or stopped
                except (BusRequestError, asyncio.TimeoutError) as exc:
                    self.log.warning("сериал %d: чтение %s не остановлено: %s", series_id, source.url, exc)
            try:
                stopped = (await self.reply_of(PLANNER_SYNC_CANCEL, {"series_id": series_id})).cancelled or stopped
            except BusRequestError as exc:
                if NO_HANDLER not in str(exc):
                    raise
        finally:
            self._cancelling.discard(series_id)
        if stopped:
            self.log.info("сериал %d: синхронизация остановлена пользователем", series_id)
        return RunCancelled(cancelled=stopped)

    async def _pass_series(self, series_id: int, download: bool, resume: int | None,
                           scope: SyncScope | None = None) -> SyncResult:
        """The command is written down before anything is waited for, so that a restart finds it: a synchronization
        is a task of `scan_tasks` until it ends (`resume` is the task a restart found)."""
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
                intent = await self.repo.create_task(series_id, [], {
                    "video_sync": True, "download": download, **({} if scope is None else {"scope": scope.named()})})
            self._owned.add(intent)
            series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
            if not any(each.parser_profile_id for each in series.sources):
                self._announce_run(series_id, True)
                raise SyncFailed(reason("scan_no_profile"))
            # The run is told once, with its snapshot: no step of it has begun before (И-60).
            await self._take_snapshot(series, intent)
            self._announce_run(series_id, True)
            # The error of the scan before is cleared by the new one (Р-11).
            await self.repo.delete_error_tasks(series_id)
            series = await self._relocate(series)
            result = await self._sync(series, download, scope)
            await self._hold_run(series_id, intent)
            intent = None  # the run keeps the command now: it ends when the works of the run end
            self._contribute(series_id, scanning=series_id in self._runs, error=series_id in self._failed)
            # The aggregate of TMDB for a series of several seasons: the real seasons may have changed (a new season,
            # new files), so the count of the episodes by season is counted again.
            self.send_command(METADATA_SEASONS_RECOMPUTE, {"series_id": series_id})
            return result
        except asyncio.CancelledError:
            if series_id not in self._cancelling:
                raise  # the process stops: the written command takes the sync up again at the start
            asyncio.current_task().uncancel()
            if intent is not None:
                await self.repo.delete_task(intent)
            if series_id not in self._runs:
                self._announce_run(series_id, False)
            self._contribute(series_id, scanning=series_id in self._runs, error=False)
            return SyncResult(changed=False, cancelled=True)
        except ScanAborted as exc:
            self.log.info("скан сериала %d прерван: %s", series_id, exc)
            if intent is not None:
                await self.repo.delete_task(intent)
            self._announce_run(series_id, False)
            self._contribute(series_id, scanning=False, error=False)
            return SyncResult(changed=False, aborted=True)
        except Exception as exc:
            # The carrier of the error: an error record in scan_tasks, if the torrent phase left none. The series is gone:
            # no carrier is written.
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

    async def _has_error_task(self, series_id: int) -> bool:
        return any(t["series_id"] == series_id
                   for t in await self.repo.error_tasks())

    async def _series_alive(self, series_id: int) -> bool:
        """Whether the catalog has the series: the carrier of an error is written for an existing series only — the foreign key
        would not let it be written otherwise."""
        try:
            await self.request(CATALOG_SERIES_GET, {"series_id": series_id})
            return True
        except BusRequestError:
            return False

    async def _relocate(self, series: SeriesCard) -> SeriesCard:
        """The first step of a sync: the move of the folder a save planned (И-59); everything after it works in the new
        folder. A failed move fails the sync, and its plan waits for the next one."""
        try:
            reply = await self.reply_of(LIBRARY_RELOCATION_APPLY, {"series_id": series.id})
        except BusRequestError as exc:
            if NO_HANDLER in str(exc):
                return series
            raise
        if not reply.moved:
            return series
        return await self.reply_of(CATALOG_SERIES_GET, {"series_id": series.id})

    # --- synchronisation and the plan of references ----------------------------------

    async def _sync(self, series: SeriesCard, download: bool, scope: SyncScope | None = None) -> SyncResult:
        """Tables of all sources -> layout -> references and plan -> (with download) the download and the voice-overs. A sync
        of a season or of an episode reads the tables of every source and counts the layout as the sync of the series does;
        only what the planner carries out is the scope's (planner.sync).

        Each step starts on the signal that the one before it ended, and any step that fails fails the scan."""
        series_id = series.id
        self._alive(series_id)
        tables = await sync.sync_tables(series.sources, self.reply_of, self._signals, self._table_wait_s)
        for each in tables:
            if each.reason:
                self.log.warning("сериал %d, источник %d: страница %s — %s, причина %s", series_id, each.source_id,
                                 each.url, each.state, each.reason)
        self._alive(series_id)
        recount = SeriesRecount(series_id=series_id, snapshot=self._snapshots.get(series_id))
        await self._step(COMPOSITION_RECOMPUTE, recount.model_dump(mode="json"), units=len(series.sources))
        # The disk follows the plan here and nowhere else: the planner counts the plan of the series from its asset and
        # these tables, does its work and applies it once, at its end (modules/planner). Without download it applies only
        # what needs no work.
        self._alive(series_id)
        accepted = await self._step(PLANNER_SYNC, {"series_id": series_id, "download": download,
                                                   **({} if scope is None else scope.named())})
        return SyncResult(changed=accepted.accepted, sources=tuple(tables))

    def _state(self, series_id: int) -> str:
        return availability.SERIES_SCANNING if series_id in self._running or series_id in self._runs \
            else availability.SERIES_IDLE

    def _require_idle(self, series_id: int, action: availability.Action = availability.Action.SYNC,
                      env: Envelope | None = None) -> None:
        """An action on a series that is being synchronized is refused by the table of what is allowed, as every module
        refuses: a sync is busy from its start until every work of its run has ended. A change admitted before the sync
        (its request carries the number of an admission still open) is not a new one: the opening sync waits for it."""
        if env is not None and env.admission and env.admission in edits.under_way(self._edits, series_id):
            return
        availability.require(action, self._state(series_id))

    # --- the run: a sync and every work it gave out ---------------------------------------------------------------

    def _announce_run(self, series_id: int, active: bool) -> None:
        """The modules hear the run (the snapshot, the refusals); the card of the series is busy for all of it."""
        if not active:
            self._snapshots.pop(series_id, None)
        self._run_seq += 1
        self.publish_event(SCAN_RUN_CHANGED, RunChanged(series_id=series_id, active=active,
                                                        snapshot=self._snapshots.get(series_id) if active else None,
                                                        seq=self._run_seq).model_dump(mode="json"))
        self.publish_event(SERIES_BUSY_CONTRIBUTION,
                           BusyContribution(source="sync", series_id=series_id, busy=active).model_dump(mode="json"))

    async def _hold_run(self, series_id: int, intent: int) -> None:
        """The pass of the sync is over and its works are given out: the run stays open, with its command written down,
        until each executor says it has no work of the series left. An executor that has none at once closes nothing."""
        task = await self.repo.get_task(intent)
        await self.repo.update_results(intent, {**(task["results_data"] if task else {}), "phase": "working"})
        self._runs[series_id] = intent
        await self._ask_working(series_id)

    async def _take_snapshot(self, series: SeriesCard, intent: int) -> None:
        """The rules of the profiles of the sources and the settings of the voice-overs as they are when the sync starts,
        written with its command: every step of the run works by them, and a run a restart takes up again too (И-60)."""
        series_id = series.id
        task = await self.repo.get_task(intent)
        results = task["results_data"] if task else {}
        if results.get("snapshot") is not None:
            self._snapshots[series_id] = RunSnapshot.model_validate(results["snapshot"])
            return
        profiles = sorted({each.parser_profile_id for each in series.sources if each.parser_profile_id})
        snapshot = RunSnapshot(profiles=(await self._exported(RULES_PROFILES_EXPORT, {"profile_ids": profiles}))
                               .get("profiles", {}),
                               voices=(await self._exported(VOICES_SETTINGS_EXPORT, {})).get("values", {}))
        await self.repo.update_results(intent, {**results, "snapshot": snapshot.model_dump(mode="json")})
        self._snapshots[series_id] = snapshot

    async def _exported(self, topic: Topic, payload: dict) -> dict:
        try:
            return await self.request(topic, payload)
        except (BusRequestError, asyncio.TimeoutError) as exc:
            if NO_HANDLER in str(exc):
                return {}  # no such owner in this process: nothing of it to keep
            self.log.error("синхронизация: запрос %s не выполнен: %s: %s", topic.name, type(exc).__name__, exc)
            raise SyncFailed(reason("scan_step_failed", step=topic.name)) from exc

    async def _ask_working(self, series_id: int) -> None:
        """What each executor has of the series now; the run waits for those that have work (their events end it).
        Until its answer is in, an executor counts as working, and the run is closed only after every answer. A run whose series was
        deleted meanwhile has nothing left to ask."""
        self._working[series_id] = runs.begin(RUN_EXECUTORS)
        try:
            for owner, topic in RUN_EXECUTORS.items():
                try:
                    state = await self.reply_of(topic, {"series_id": series_id})
                    if series_id in self._working:
                        self._working[series_id] = runs.apply(self._working[series_id], state)
                except BusRequestError as exc:
                    if NO_HANDLER not in str(exc):
                        raise
                    if series_id in self._working:  # nobody does that kind of work in this process
                        self._working[series_id] = runs.without(self._working[series_id], owner)
        finally:
            if series_id in self._working:
                self._working[series_id] = runs.polled(self._working[series_id])
        if series_id in self._working and runs.done(self._working[series_id]):
            await self._close_run(series_id)

    async def on_work_state(self, env: Envelope) -> None:
        state = WorkState.model_validate(env.payload)
        work = self._working.get(state.series_id)
        if state.series_id not in self._runs or work is None:
            return
        self._working[state.series_id] = work = runs.apply(work, state)
        if runs.done(work):
            await self._close_run(state.series_id)

    async def _close_run(self, series_id: int) -> None:
        """Every work of the run has ended: the command goes, the series is free, and a recount an event asked for while
        the run was open is done now (a plan; the disk follows it at the next sync)."""
        if series_id not in self._runs:
            return  # the run is closed already: one end
        intent = self._runs.pop(series_id, None)
        work = self._working.pop(series_id, None)
        failure = runs.failure(work) if work is not None else None
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

    # --- resumption -----------------------------------------------------------------------------------

    async def _resume(self, task: dict) -> None:
        """A task a restart found: a synchronization is run again from its written command. A task of the torrent scan
        that left with stage 15 has nothing to run it: it goes."""
        series_id, results = task["series_id"], task["results_data"]
        match resume_step(task, self._owned, self._running):
            case "legacy":
                self.log.warning("сериал %d: задача %d прежнего торрент-скана снята при старте", series_id, task["id"])
                await self.repo.delete_task(task["id"])
            case "owned":
                return  # the scan of this very command is running in this process
            case "working":  # the pass was over: the run waits for its works again
                self._runs[series_id] = task["id"]
                if results.get("snapshot") is not None:
                    self._snapshots[series_id] = RunSnapshot.model_validate(results["snapshot"])
                self._announce_run(series_id, True)
                self._contribute(series_id, scanning=True, error=False)
                try:
                    await self._ask_working(series_id)
                except Exception:  # noqa: BLE001 - the run stays open; an event of an executor or the next start closes it
                    self.log.exception("сериал %d: работы прогона не опрошены после перезапуска", series_id)
            case "again":  # a command that came after the restart is the scan of the series already
                await self.repo.delete_task(task["id"])
            case _:
                try:
                    await self._run_series(series_id, download=bool(results.get("download", True)),
                                           resume=task["id"], scope=SyncScope.of(results.get("scope")))
                except Exception:  # noqa: BLE001 - the failure is kept as the error task of the series
                    self.log.exception("возобновление синхронизации сериала %d не удалось", series_id)

    # --- the fold of the statuses (Р-11) ------------------------------------------------

    def _contribute(self, series_id: int, *, scanning: bool, error: bool) -> None:
        self.publish_event(SERIES_STATUS_CONTRIBUTION, StatusContribution(
            source="scan", series_id=series_id, flags={"scanning": scanning, "error": error}).model_dump(mode="json"))

    # --- the scheduler of the automatic scan ------------------------------------------------

    async def _scheduler_loop(self) -> None:
        """Sleeps until the next sync of the schedule (the time of day is the subject of the wait); a change of the schedule
        or of the switch wakes it to count again."""
        await self._broadcast_status()
        while True:
            self._wake.clear()
            delay = await self._until_next()
            try:
                await asyncio.wait_for(self._wake.wait(), delay)
                continue
            except TimeoutError:
                pass
            try:
                await self._scheduler_tick()
            except Exception:  # noqa: BLE001 — the loop must not die
                self.log.exception("ошибка такта планировщика")

    async def _until_next(self) -> float | None:
        """Seconds to the next sync of the schedule; None (no limit) while a pass goes, the switch is off or nothing is
        scheduled: the end of the pass or a change of the settings wakes the loop."""
        if self._scan_all_running or await self._setting("scanner_agent_enabled", "false") != "true":
            return None
        return delay_until(await self._setting("next_scan_timestamp", None), datetime.now(timezone.utc))

    async def _scheduler_tick(self) -> None:
        if self._scan_all_running:
            return
        if await self._setting("scanner_agent_enabled", "false") != "true":
            return
        if is_due(await self._setting("next_scan_timestamp", None), datetime.now(timezone.utc)):
            self.log.info("настало время планового сканирования")
            self._scan_all_running = True
            self.spawn(self._scan_all())

    async def _scan_all(self) -> None:
        self._scan_all_running = True
        await self._broadcast_status()
        stopped = False
        try:
            all_series = (await self.reply_of(CATALOG_SERIES_LIST, {})).series
            for s in all_series:
                if not s.auto_scan_enabled:
                    continue
                if self._state(s.id) != availability.SERIES_IDLE:
                    self.log.info("автоскан: сериал %d пропущен — идёт его синхронизация", s.id)
                    continue
                if (why := await self._provider_blocks(s.id, s)) is not None:
                    self.log.info("автоскан: сериал %d пропущен — %s", s.id, describe(why))
                    continue
                try:
                    await self._run_series(s.id)
                except Exception as exc:  # noqa: BLE001 — the pass is not interrupted
                    self.log.error("автоскан сериала %d: %s", s.id, exc)
        except asyncio.CancelledError:
            stopped = True  # the module is going away: its neighbours are not asked anything on the way out
            raise
        finally:
            self._scan_all_running = False
            if not stopped:
                await self._schedule_next()
                # The next sync may be the one it was: the end of the pass wakes the scheduler by itself.
                self._wake.set()

    async def _schedule_next(self) -> None:
        """The next sync is the first time of day of the schedule after now; a schedule of none leaves no next."""
        value = next_pass(await self._setting(SCHEDULE_KEY, None), datetime.now(timezone.utc))
        if value != (await self._setting("next_scan_timestamp", None) or ""):
            await self.request(SETTINGS_VALUE_SET, {"key": "next_scan_timestamp", "value": value})
            self.log.info("следующее сканирование: %s", value or "нет в расписании")
        await self._broadcast_status()

    async def _setting(self, key: str, default: str | None) -> str | None:
        value = (await self.reply_of(SETTINGS_VALUE_GET, {"key": key})).value
        return value if value is not None else default

    async def _status(self) -> ScanStatus:
        """The state of the scheduler (GET /api/scanner/status): the switch, the schedule, the next sync and the next check
        of the torrent releases."""
        schedule = await self._setting(SCHEDULE_KEY, None)
        enabled = await self._setting("scanner_agent_enabled", "false") == "true"
        next_scan = await self._setting("next_scan_timestamp", None)
        return status_of(schedule, enabled, self._scan_all_running or bool(self._running), next_scan, datetime.now(timezone.utc))

    async def _broadcast_status(self) -> None:
        """The contract of the old scanner_status_update (the indicator of the scanner)."""
        self.publish_event(SCAN_STATUS_CHANGED, (await self._status()).model_dump(mode="json"))
