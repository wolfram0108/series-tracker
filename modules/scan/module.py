"""Module scan — the orchestrator of scans: the schedule, the torrent scan, and, for a series of kind `video`, the run
of its synchronization.

A video series is not read here. The tables of its pages are read by `sources`, the numbers come from the constructor
through `composition`; scan waits for the tables, asks for the layout to be counted from them and hands the series to
the planner (modules/planner), which chooses the reference of every episode, does the work and applies it. The run stays
open until the planner has no work of the series left.

Queries:
  scan.runs.get → {runs: [{series_id, snapshot}], as_of} — the open runs of syncs as their commands are written (an
      executor that starts again takes them before its works: И-60), as of the generation `as_of` of the announcements of
      runs (RunChanged.seq): a series whose announcement is newer than that is told by the announcement, not by the reading.
  scan.series.run {series_id, force_replace?, download?} → {changed, ...}. changed tells whether anything was
      actually changed: torrent — tasks were created; video — the references or the rows of the plan differ from
      what they were before the run. A video series answers {kind: "video", changed, sources: [{source_id, url,
      state: ready | stale | skipped, reason}], references: {by_level, own, other_player, none, unfit}, in_plan}.
      `stale` — the read of the page failed and the last complete table stays in force; `skipped` — it failed and there is no table.
      A scan of a series that is being scanned is refused by the table of what is allowed (`not_allowed`, the state
      `scanning`; finding 27) with the code every module refuses by. The steps of a video series
      are tied by signals: sources.table.get for every source at once and the wait for sources.table.ready / .failed
      of its page, composition.recompute (its answer means the layout is counted from these tables), then
      planner.sync {download}: the planner counts the plan of the series and carries it out. A failed step is the one
      signal of failure:
      the error carrier (scan_tasks.status='error') holds its reason. The wait for a table has a safeguard
      (`TABLE_WAIT_S`): when it runs out the source counts as failed, reason `table_wait_timeout`.
  scan.run.cancel {series_id} → {cancelled}: the user stops the sync of the series: the pass of the scan stops (the
      reads of its pages too, sources.table.cancel), then every work of its run (planner.sync.cancel). Nothing of it is
      applied, the run closes and is not taken up again at the start. False when no sync of the series went.
  scan.series.prepare {series_id} → the summary of a torrent scan without a download: the torrent is added to
      qBittorrent, split into its files (torrent_files) and left paused — the user picks the episodes in the
      composition, the next scan starts the download.
  scan.output.switch {series_id, season, episodes?, off} → {success}: switches episodes of a season, one or many at once
      in one change (without `episodes`, the whole season), off or on in the memory of the series (ignored_seasons,
      ignored_episodes), for the rows that exist and for those a new release brings; switching a season on switches on
      all of it. The plan of the series reads the memory.
      A series the catalog does not have is refused with the catalog's own answer.

Commands:
  scan.all.start {} — a pass over all series with auto_scan_enabled (what the internal scheduler does by the
      schedule from the settings).

Events (outgoing):
  scan.plan.updated {series_id} — a row of a torrent series was switched off or on by the user.
  scan.status.changed {scanner_enabled, scan_interval, is_scanning, is_awaiting_tasks, next_scan_time} — the state
      of the scheduler (the contract of the old scanner_status_update).
  series.status.contribution — the fold of scanning/error (R-11); the carrier of the error is scan_tasks.status='error'.
  series.purged {series_id, owner} — report: the tables of the series are cleaned.
  scan.run.changed (RunChanged) — a sync of the series opened or closed its run: open from the start of the sync until
      the planner has no work of the series left (events work.series.state, query planner.work.get). While it is open
      the series is busy (Action.EDIT and SYNC are refused, series.busy.contribution {source: sync}) and its plan stays
      as the sync found it: a recount an event asks for is done when the run closes. An open run is kept in scan_tasks
      (phase=working) and taken up again after a restart.
  scan.series.progress (SeriesReadProgress) — how far the read of a page is that a scan of the series waits for, per
      source, with the rate; `active` false ends it (read, failed or given up). The card of the series shows it.

Events (incoming):
  sources.table.ready / .failed {url, ...} — the verdicts a synchronization waits for.
  sources.table.progress (TableProgress) — how far a read is; told again as scan.series.progress for the series waiting.
  torrents.queue.changed {count} — the pipeline is empty: after a full pass the next scan is set when count=0.

Neighbours (all over the bus): catalog.series.get / .list (kind, sources), composition.recompute, sources.table.get,
planner.sync / .work.get, library.relocation.apply, renaming.reprocess (a torrent series). Scan asks nothing of composition at start: its state is its own database.

Resumability: the plan of replacements of a torrent scan is in scan_tasks BEFORE it is carried out; additions are
idempotent (local infohash, R-2: adding again = existed); on_start goes on with the unfinished tasks. A video series
keeps its references and rows in the database; what a restart interrupted is brought back by the events of
composition and by the next scan.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from core import NO_HANDLER, BaseModule, BusRequestError, torrent_source
from core.deadlines import EDIT_STUCK_S, TABLE_WAIT_S
from core.contracts import (RUN_CHANGED, WORK_STATE, RunSnapshot, availability, OutputSwitch, RunChanged, SeriesReadProgress, SeriesSource, TableProgress, Slot, WorkState)
from core.db import Database
from core.envelope import Envelope
from core.ignored import parse_memory
from core.reasons import reason
from . import sync
from .errors import ScanAborted, ScanError, SyncFailed
from .repository import ScanRepository
from .torrent import TorrentScan
from .sync import TableSignals

# The executor whose work a run of a video series waits for (`<owner>.work.get`, events `work.series.state`): the planner
# downloads, cuts, aligns, builds and applies the plan of the series (modules/planner).
RUN_EXECUTORS = ("planner",)


@dataclass
class _RunWork:
    """What the run of a series knows of the work of its executors. An executor is working until a state of it says it
    is not (one not heard yet is working); of two states of one executor the later counted (`seq`) is in force, so a
    reply and an event that cross on the bus cannot undo each other. `polling` is set while the run asks every
    executor: the run is not closed from an event before every answer is in. `busy` holds the episodes each executor
    works on: an episode no executor holds any more is ready for its container."""

    working: set[str]
    seen: dict[str, int]
    polling: bool = True
    busy: dict[str, set[Slot]] = field(default_factory=dict)

    def apply(self, state: WorkState) -> set[Slot]:
        """Takes the state in; the episodes it leaves with no work of any executor."""
        if state.seq <= self.seen.get(state.owner, -1):
            return set()
        before = self.slots
        self.seen[state.owner] = state.seq
        self.busy[state.owner] = {tuple(slot) for slot in state.busy}
        if state.active:
            self.working.add(state.owner)
        else:
            self.working.discard(state.owner)
        return before - self.slots

    @property
    def slots(self) -> set[Slot]:
        return set().union(*self.busy.values())

    @property
    def done(self) -> bool:
        return not self.polling and not self.working



def _sources_of(series: dict) -> list[SeriesSource]:
    return [SeriesSource.model_validate(each) for each in series.get("sources", [])]


def _torrent_source(series: dict) -> SeriesSource:
    """The source of a torrent series: the address and the profile of a torrent live in the source (Д-38)."""
    found = torrent_source(series)
    if found is None:
        raise ScanError(reason("torrent_source_missing", name=series.get("name")))
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
        self._passes: dict[int, asyncio.Task] = {}  # the pass of the scan of each series, a task of its own
        self._cancelling: set[int] = set()  # the series whose sync the user stops
        # The open runs: series -> its command in scan_tasks, the executors that still have work of it, and the
        # series whose recount an event asked for while its run was open (counted when the run ends).
        self._runs: dict[int, int] = {}
        self._working: dict[int, _RunWork] = {}
        # The containers a run makes as its episodes get ready, before the run ends (each episode is a process of its own).
        self._closing: set[int] = set()
        self._owned: set[int] = set()  # the tasks of scan_tasks that the scans running here keep
        # The changes of a series by the user under way (scan.edit.begin/end) and the signal that none is left: a run
        # opens only after them, and none is admitted while a run is open or opening.
        self._edits: dict[int, set[str]] = {}
        # The numbers whose end came before their beginning (the caller stopped waiting for the grant and gave it back): the
        # beginning that comes later opens nothing.
        self._ended_early: dict[int, set[str]] = {}
        # The configuration each open run works by (RunSnapshot), kept with its command in scan_tasks.
        self._snapshots: dict[int, RunSnapshot] = {}
        # The generation of the announcements of runs (RunChanged.seq), in the order they are published.
        self._run_seq = 0
        # The rows whose failed download an open run has already passed over (it took the next source for them).
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
        self.handle("scan.run.cancel", self.on_run_cancel, concurrent=True)
        self.handle("scan.series.prepare", self.on_prepare, concurrent=True)
        self.handle("scan.all.start", self.on_scan_all)
        self.handle("scan.media.list", self.on_media_list)
        self.handle("scan.item.set_ignored", self.on_set_ignored)
        self.handle("scan.output.switch", self.on_output_switch)
        self.handle("scan.edit.begin", self.on_edit_begin)
        self.handle("scan.edit.end", self.on_edit_end)
        self.handle("scan.status.get", self.on_status_get)
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
        as_of = self._run_seq
        runs = []
        for task in await self.repo.incomplete_tasks():
            results = task["results_data"] or {}
            if results.get("video_sync"):
                runs.append({"series_id": task["series_id"], "snapshot": results.get("snapshot")})
        return {"runs": runs, "as_of": as_of}

    async def on_system_started(self, env: Envelope) -> None:
        """Every module is up: the scans that were under way are taken up again. The work needs the catalog, the
        sources and the neighbours of a scan, which do not all answer while this module starts."""
        for task in await self.repo.incomplete_tasks():
            self.log.info("возобновление прерванного скана: задача %d, "
                          "сериал %d", task["id"], task["series_id"])
            self._spawn(self._resume(task))

    def _spawn(self, coro) -> None:
        self.spawn(coro)

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
        """is_ignored_by_user is our column; the UI and slicing set it (a row of a torrent series)."""
        item = await self.repo.get_item(env.payload["unique_id"])
        if item is None:
            return
        await self.repo.set_ignored(item["unique_id"], bool(env.payload["is_ignored"]))
        self.publish_event("scan.plan.updated", {"series_id": item["series_id"]})

    # --- the admission of a change by the user (I-60) ------------------------------------------------------------

    async def on_edit_begin(self, env: Envelope) -> dict:
        """A change of the series by the user begins: refused for a series the catalog does not have and while a sync of it
        runs or opens; admitted under its
        number, it holds off the opening of a sync until scan.edit.end with that number, which its admission sends once
        every request of the change is answered (core/admission.py). The number is the caller's, known before the grant:
        a caller that stops waiting for the grant gives it back by that number, and an end that came first is kept, so
        the grant that follows it opens nothing."""
        series_id = int(env.payload["series_id"])
        token = env.payload.get("token") or uuid.uuid4().hex
        # A series the catalog does not have is refused with the catalog's answer, for every change at once. It is
        # asked first: what follows runs without a pause, so an end that comes meanwhile is not missed.
        try:
            await self.request("catalog.series.get", {"series_id": series_id})
        except BusRequestError as exc:
            if NO_HANDLER not in str(exc):
                raise
        early = self._ended_early.get(series_id, set())
        if token in early:
            early.discard(token)
            if not early:
                self._ended_early.pop(series_id, None)
            return {"ok": True, "token": token, "ended": True}
        self._require_idle(series_id, availability.Action.EDIT)
        self._edits.setdefault(series_id, set()).add(token)
        self._edits_over.setdefault(series_id, asyncio.Event()).clear()
        return {"ok": True, "token": token}

    async def on_edit_end(self, env: Envelope) -> None:
        """The change of that number is written whole; an end told twice is the same as once. An end of a number not
        admitted yet is the end of a grant its caller gave up waiting for: it is kept for that grant."""
        series_id = int(env.payload["series_id"])
        token = env.payload.get("token") or ""
        open_ = self._edits.get(series_id)
        if open_ is None or token not in open_:
            if token:
                self._ended_early.setdefault(series_id, set()).add(token)
            return
        open_.discard(token)
        if open_:
            return
        self._edits.pop(series_id, None)
        over = self._edits_over.pop(series_id, None)
        if over is not None:
            over.set()

    async def _edits_finished(self, series_id: int) -> None:
        """A run opens after the changes admitted before it are written whole; none is admitted meanwhile (the series
        is in `_running`). A change ends when every request of it is answered (core/admission.py). One that does not end
        within the longest answer a change may wait for is a failure, told in the log; the run goes on waiting for it."""
        over = self._edits_over.get(series_id)
        if over is None or not self._edits.get(series_id):
            return
        try:
            await asyncio.wait_for(over.wait(), EDIT_STUCK_S)
        except asyncio.TimeoutError:
            self.log.error("сериал %d: синхронизация ждёт правку, допущенную до неё, дольше %.0f с — запрос правки не "
                           "ответил (открытых допусков: %d); синхронизация ждёт дальше", series_id, EDIT_STUCK_S,
                           len(self._edits.get(series_id, ())))
            await over.wait()

    async def on_output_switch(self, env: Envelope) -> dict:
        """Episodes (one or many at once), or a whole season, are switched off or on by the user: the memory of the series
        holds it, for the rows that are there and for those a new release brings; the plan of the series reads it.
        Switching a season on switches on all of it."""
        request = OutputSwitch.model_validate(env.payload)
        series_id = request.series_id
        self._require_idle(series_id, availability.Action.EDIT, env)

        async def apply() -> None:
            series = await self.request("catalog.series.get", {"series_id": series_id})
            seasons, memory = self._ignored_seasons(series), self._ignored_episodes(series)
            if request.episodes is None:
                seasons = seasons | {request.season} if request.off else seasons - {request.season}
                if not request.off:
                    memory.pop(request.season, None)
            else:
                numbers, asked = memory.get(request.season, set()), set(request.episodes)
                memory[request.season] = numbers | asked if request.off else numbers - asked
            # The memory of the series is the switch: the plan of the series reads it (modules/planner).
            await self.request("catalog.series.update", {"series_id": series_id, "fields": {
                "ignored_seasons": json.dumps(sorted(seasons)),
                "ignored_episodes": json.dumps({str(season): sorted(numbers) for season, numbers in memory.items() if numbers}),
            }})

        await apply()
        return {"success": True}

    async def on_series_deleted(self, env: Envelope) -> None:
        """Каскад Р-19: владелец чистит media_items и scan_tasks; идущий
        скан серии прерывается (Д2 — как у загрузки, нарезки и переноса)."""
        series_id = env.payload["series_id"]
        self._aborted.add(series_id)
        # A count of the plan that is writing finishes first; one that comes after sees the series aborted.
        async with self._lock(series_id):
            await self.repo.delete_for_series(series_id)
        self._locks.pop(series_id, None)
        self._ended_early.pop(series_id, None)
        if self._runs.pop(series_id, None) is not None:  # its works go with the series: the run is over
            self._working.pop(series_id, None)
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

    def _lock(self, series_id: int) -> asyncio.Lock:
        return self._locks.setdefault(series_id, asyncio.Lock())

    def _alive(self, series_id: int) -> None:
        if series_id in self._aborted:
            raise ScanAborted(reason("series_deleted_in_scan", series_id=series_id))

    async def _step(self, topic: str, payload: dict, *, units: int = 1):
        """A request of a synchronization: any failure of it is the one signal that the step failed."""
        try:
            return await self.request(topic, payload, units=units)
        except (BusRequestError, asyncio.TimeoutError) as exc:
            if "LookupError" in str(exc):
                raise ScanAborted(reason("series_deleted_in_scan", series_id=payload.get("series_id"))) from exc
            self.log.error("синхронизация: запрос %s не выполнен: %s: %s", topic, type(exc).__name__, exc)
            raise SyncFailed(reason("scan_step_failed", step=topic)) from exc

    async def _run_series(self, series_id: int, force: bool = False,
                          download: bool = True, resume: int | None = None) -> dict:
        """A scan of one series, a task of its own: a stop of it by the user (scan.run.cancel) stops it alone, not the
        pass over all series it may be a part of."""
        # Refused or taken at once, in the handler: a second scan of the series is refused from this moment on.
        self._require_idle(series_id)
        self._running.add(series_id)
        passing = self.spawn(self._pass_series(series_id, force, download, resume), name=f"scan.pass:{series_id}")
        self._passes[series_id] = passing
        try:
            return await passing
        finally:
            if self._passes.get(series_id) is passing:
                self._passes.pop(series_id, None)
            self._running.discard(series_id)

    async def on_run_cancel(self, env: Envelope) -> dict:
        """The user stops the sync of a series: the pass of the scan, the reads of its pages (failed, and not begun again
        at the start; a read the start took up again stops too, though no pass waits for it), then every work of its run (planner.sync.cancel: downloads, jobs of the service,
        the build). The run closes as every run closes, and nothing of the stopped sync is applied."""
        series_id = int(env.payload["series_id"])
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
            series = await self.request("catalog.series.get", {"series_id": series_id})
            for source in _sources_of(series):
                try:
                    reply = await self.request("sources.table.cancel", {"url": source.url})
                    stopped = bool((reply or {}).get("cancelled")) or stopped
                except (BusRequestError, asyncio.TimeoutError) as exc:
                    self.log.warning("сериал %d: чтение %s не остановлено: %s", series_id, source.url, exc)
            try:
                reply = await self.request("planner.sync.cancel", {"series_id": series_id})
                stopped = bool(reply.get("cancelled")) or stopped
            except BusRequestError as exc:
                if NO_HANDLER not in str(exc):
                    raise
        finally:
            self._cancelling.discard(series_id)
        if stopped:
            self.log.info("сериал %d: синхронизация остановлена пользователем", series_id)
        return {"cancelled": stopped}

    async def _pass_series(self, series_id: int, force: bool, download: bool, resume: int | None) -> dict:
        """The command is written down before anything is waited for, so that a restart finds it: a video synchronization
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
                intent = await self.repo.create_task(series_id, [], {"video_sync": True, "download": download})
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
                raise ScanError(reason("scan_no_profile"))
            # Прошлая ошибка сбрасывается новым сканом (Р-11).
            await self.repo.delete_error_tasks(series_id)
            series = await self._relocate(series)
            if video:
                result = await self._sync_video(series, download)
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
        except asyncio.CancelledError:
            if series_id not in self._cancelling:
                raise  # the process stops: the written command takes the sync up again at the start
            asyncio.current_task().uncancel()
            if intent is not None:
                await self.repo.delete_task(intent)
            if series_id not in self._runs:
                self._announce_run(series_id, False)
            self._contribute(series_id, scanning=series_id in self._runs, error=False)
            return {"changed": False, "cancelled": True}
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
                raise ScanError(reason("prepare_torrent_only"))
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

    async def _sync_video(self, series: dict, download: bool) -> dict:
        """Tables of all sources -> layout -> references and plan -> (with download) the download and the voice-overs.

        Each step starts on the signal that the one before it ended, and any step that fails fails the scan."""
        series_id = series["id"]
        self._alive(series_id)
        tables = await sync.sync_tables(_sources_of(series), self.request, self._signals, self._table_wait_s)
        for each in tables:
            if each.reason:
                self.log.warning("сериал %d, источник %d: страница %s — %s, причина %s", series_id, each.source_id,
                                 each.url, each.state, each.reason)
        self._alive(series_id)
        snapshot = self._snapshots.get(series_id)
        await self._step("composition.recompute", {"series_id": series_id, **(
            {"snapshot": snapshot.model_dump(mode="json")} if snapshot is not None else {})}, units=len(_sources_of(series)))
        # The disk follows the plan here and nowhere else: the planner counts the plan of the series from its asset and
        # these tables, does its work and applies it once, at its end (modules/planner). Without download it applies only
        # what needs no work.
        self._alive(series_id)
        accepted = await self._step("planner.sync", {"series_id": series_id, "download": download})
        return {"kind": "video", "changed": bool((accepted or {}).get("accepted")),
                "sources": [each.as_dict() for each in tables]}

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
        self._run_seq += 1
        self.publish_event(RUN_CHANGED, RunChanged(series_id=series_id, active=active,
                                                   snapshot=self._snapshots.get(series_id) if active else None,
                                                   seq=self._run_seq).model_dump(mode="json"))
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
        """Every work of the run has ended: the command goes, the series is free, and a recount an event asked for while
        the run was open is done now (a plan; the disk follows it at the next sync)."""
        if series_id not in self._runs or series_id in self._closing:
            return  # the run is closing already: one end
        failure = None
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
                                   resume=task["id"])
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
                    await self._run_series(s["id"], force)
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
