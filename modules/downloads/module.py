"""Module downloads: the video of a series into its library (yt-dlp, ffmpeg). Decision Р-13.

Owns: the table download_tasks (the video part) and the columns media_items.status, final_filename and rename_to.

The model. A row of the plan is READY exactly when it holds a file name; the plan says which rows are wanted and what they
show; a task is the work order of one row. What lies in the library is what the plan wants: `Library` (library.py) keeps the
files against the rows (L1-L8, see its docstring), `Publication` (publication.py) puts a whole download under the name of its
video in a step that survives any stop. This module is the bus facade and the queue: it hears the events, decides when to
look, and runs the downloads.

Obligations that come from the state of the database (a file of a row the user switched off, a task of a row that left the
plan, a publication a stop interrupted) are carried out by `_reconcile`, which reads the state as it is now. What the plan
alone decides (a file displaced or taken out, a task that ends) is done in a sync only: dispatch and settle, which scan sends
after the plan of a sync. The look at the beginning of the process, a plan update and the disk check only finish what a sync
or a download began and read the disk: a rule saved or an episode switched off changes the plan, never the disk.

Events in:
  scan.plan.updated {series_id} - the plan was counted: the statuses of the series follow it and the files that lie under the
      names of the plan are adopted. Nothing on the disk changes and nothing is fetched (saving a rule or a setting is a plan).
  system.started - reconcile every video series (the neighbours it asks are up).
  settings.changed - max_parallel_downloads, max_parallel_dub_downloads, yt_dlp_concurrent_fragments.
  work.withdrawn {series_id, keys?} - the owner does not need the work: the downloads of a series, or the jobs of these keys,
      stop and what they wrote goes.
  series.deleted - the downloads of the series stop, what they wrote goes, its tasks go.

Queries / commands:
  downloads.settle {series_id} -> {removed, retired, dropped} - a sync without download: the disk follows the plan (the
      files another video displaced and those the configuration took out go, the tasks nobody needs end); nothing is fetched.
  downloads.dispatch {series_id} - the command of scan to fetch what is missing: reconcile, make the tasks of the rows the
      plan wants and that have no file, keep the paths of the live ones current, start the pool. The only place where a
      download starts.
  downloads.queue.get {} -> {tasks, count} - the live tasks (the window; the snapshot of scan): the tasks of the plan
      and the fetches of the voice-overs (`task_type` fetch: waiting `pending`, under way `downloading`, no progress).
  downloads.queue.clear {} -> {deleted} - waiting and failed tasks with their work files; a download under way stays.
  downloads.cancel {task_id} - one task that waits or runs (the table of what is allowed); a task whose files could not be
      removed stays as a failed one, the record of what is left.
  downloads.episodes.get {series_id} -> {items: [{unique_id, season, episode_start, part, source_url, status, final_filename,
      superseded_by}]} - the reference episodes of the plan (read by voices).
  downloads.fs.sync {series_id} -> {adopted, lost} - the disk check (scan, the status window). A series of kind `torrent`
      answers {0, 0}: nothing is read, published or logged for it.
  downloads.fetch {key, url, path, max_height?, series_id?} - the stream behind the link into an absolute path, with no
      record in media_items or download_tasks (the voice-overs of a series go to a temporary directory). The executor keeps no
      state: the owner of the queue (voices) sends the command again after a restart. It has a limit of its own, the setting
      max_parallel_dub_downloads (2 by default).
  downloads.items.rename {series_id, names: {key: name}} -> {renamed} - the names the plan gives the ready files of the series now
      (all of them: a key whose name is the one it has owes nothing). The obligation is recorded first (`media_items.rename_to`),
      then the files move to their names, a ring of files that swap names included, and each name is recorded when its file lies
      under it; a name held by what does not give it up is not taken and the row keeps its own. A stop at any step is finished
      from what the disk shows, at the next look at the series.
  downloads.item.set_filename, downloads.item.set_status - slicing reports a name and a status of a file it made or took over.

Events out:
  downloads.item.renamed {series_id, unique_id, filename} - the file name of an episode changed, after the file lies under it
      (voices follow it).
  downloads.queue.changed {tasks, count} - the queue changed (throttled on progress): SSE download_queue_update.
  downloads.fetch.started {key, series_id, path}, downloads.fetch.finished {key, ok, error, transient, path, series_id}.
  series.status.contribution {source: downloads, ...} - the fold of statuses (Р-11).
  series.downloaded.changed {series_id, count}.

One file per slot of an episode: a row with media_items.superseded_by (written by scan with the plan) loses its file, and
waits for one again, as soon as the file of the winner lies in the library. Nothing is removed while the winner is not there.

The dispatcher lives on events (a task made, a download ended, the limit changed), with no ticks. Parallelism is the number of
live workers against the limit from the settings.

The stream of a candidate is resolved by sources.video.stream only; this module knows no site. When that request fails,
yt-dlp may take the link over only if the candidate belongs to a vk_video source of its series (media_items.source_id ->
catalog.series.get sources); a web_video candidate fails with the error, since yt-dlp would scrape a foreign player. A row
without source_id (earlier versions) takes the type of the only source of the series, and is a web_video one when the series
has several.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict, dataclass

from core import BaseModule, BusRequestError, storage
from core.contracts import WORK_WITHDRAWN, SeriesKind, SourceType, WorkWithdrawn, availability
from core.db import Database
from core.envelope import Envelope
from core.work_report import WorkReport
from core.retry import UNTIMED, Budget, retrying

from . import ytdlp
from .library import Library
from .publication import PUBLISHED, REFUSED, WAITING, Publication, PublicationUnfinished
from .packing import FfmpegPacker, Packed, Packer, Packing
from .repository import LIVE_STATES, DownloadsRepository

PROGRESS_DB_THROTTLE = 2.0     # seconds between writes of progress to the database: the window needs a picture, not every tick
PROGRESS_EVENT_THROTTLE = 1.0  # seconds between queue events on progress
MAX_DOWNLOAD_ATTEMPTS = 3
RETRY_DELAY_S = 5.0
FETCH_LIMIT_KEY = "max_parallel_dub_downloads"
_TRANSIENT_MARKERS = ("timed out", "timeout", "connection", "network", "обрыв", "input/output error",
                      "temporarily", "server returned 5", "server returned 429",
                      "server returned 403", "http error 403", "http error 408",
                      "http error 429", "http error 5")


async def _never() -> bool:
    """The answer of a job that has no candidate behind it: yt-dlp is never the way out of a failed request."""
    return False


def source_of_candidate(sources: list[dict], source_id: int | None) -> dict | None:
    """The source a candidate was found by; None when it is not known and not the only one of the series.

    A row of an earlier version has no source_id: the only source of its series is its source. A source_id that
    no source of the series has any more belongs to a removed source, so it is not guessed."""
    if source_id is not None:
        return next((source for source in sources if source["id"] == source_id), None)
    return sources[0] if len(sources) == 1 else None


def _transient_download_error(error: str) -> bool:
    lowered = (error or "").lower()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)


def _download_budget() -> Budget:
    """The one budget of a whole download, whether its attempts live in a loop or in the queue."""
    return Budget(attempts=MAX_DOWNLOAD_ATTEMPTS, pauses=(RETRY_DELAY_S,), limit_s=UNTIMED)


@dataclass(frozen=True)
class Reconciled:
    """What a look at a series changed, by kind: the callers decide from it whether the window needs to be told."""

    finished: int = 0   # publications a stop had left, finished
    released: int = 0   # rows that were ready without a name, now waiting
    adopted: int = 0    # files that lay under the name of a row, taken as its file
    dropped: int = 0    # tasks the plan does not want, ended
    retired: int = 0    # files another video displaced, removed
    removed: int = 0    # files of what the configuration took out, removed
    swept: int = 0      # work files that had no task, removed
    renamed: int = 0    # files that reached the names their numbers give

    @property
    def changed(self) -> bool:
        return any((self.finished, self.released, self.adopted, self.dropped, self.retired, self.removed, self.swept,
                    self.renamed))


class DownloadsModule(BaseModule):
    name = "downloads"

    def __init__(self, bus, db: Database, *, downloader=ytdlp.download, packer: Packer | None = None) -> None:
        self.repo = DownloadsRepository(db)
        self._download = downloader  # replaced in tests
        self._packer = packer or FfmpegPacker()
        self._limit = 2               # parallel files (max_parallel_downloads)
        self._fetch_limit = 2         # parallel general downloads (downloads.fetch)
        self._fetch_queue: list[dict] = []
        self._fetch_active: dict[str, tuple[asyncio.Task, int | None]] = {}
        self._fetch_jobs: dict[str, dict] = {}  # the job of every fetch under way: the window names it by its path
        self._threads = 6             # fragments per file (yt-dlp -N)
        self._active: dict[int, asyncio.Task] = {}
        self._last_db_write: dict[int, float] = {}
        self._last_event = 0.0
        self._stopping = False        # a module that is stopping starts no work
        self._locks: dict[int, asyncio.Lock] = {}  # one look at a series at a time
        self._work = WorkReport(self.name, self._has_work, self.publish_event)
        super().__init__(bus)
        self.library = Library(self.repo, self._naming, self.log, self._neighbours)
        self._packing = Packing(self.repo, self._packer, self.request, self.publish_event, self.log, self.library)
        self._publication = Publication(self.repo, self.library, self.log, running=lambda task_id: task_id in self._active,
                                        resume=self._look_again, end_task=self._end_task)

    def register(self) -> None:
        self.handle("scan.plan.updated", self.on_plan_updated)
        self.handle("downloads.dispatch", self.on_dispatch)
        self.handle("downloads.settle", self.on_settle)
        self.handle("downloads.work.get", self.on_work_get, concurrent=True)  # reads only: never behind a look
        self.handle("downloads.pack", self.on_pack)
        self.handle("settings.changed", self.on_settings_changed)
        self.handle("downloads.queue.get", self.on_queue_get)
        self.handle("downloads.queue.clear", self.on_queue_clear)
        self.handle("downloads.cancel", self.on_cancel)
        self.handle("downloads.episodes.get", self.on_episodes_get)
        self.handle("downloads.fetch", self.on_fetch)
        self.handle(WORK_WITHDRAWN, self.on_work_withdrawn)
        self.handle("downloads.fs.sync", self.on_fs_sync)
        self.handle("downloads.items.rename", self.on_items_rename)
        self.handle("downloads.item.set_filename", self.on_set_filename)
        self.handle("downloads.item.set_status", self.on_set_status)
        self.handle("series.deleted", self.on_series_deleted)
        self.handle("system.started", self.on_system_started)

    async def on_start(self) -> None:
        self._limit = await self._read_limit()
        self._threads = await self._read_threads()
        self._fetch_limit = await self._read_fetch_limit()
        requeued = await self.repo.requeue_interrupted()
        if requeued:
            self.log.info("оборванных загрузок возвращено в очередь: %d", requeued)
        try:  # a supplier of the catalog's fold of statuses states its contribution at the start
            for series in await self.request("catalog.series.list", {}):
                if series.get("kind") == SeriesKind.VIDEO:
                    await self._contribute(series["id"])
        except BusRequestError as exc:
            self.log.warning("каталог недоступен при старте: %s", exc)
        await self._pump()

    async def on_stop(self) -> None:
        self._stopping = True

    async def on_system_started(self, env: Envelope) -> None:
        """Every module is up: look at every video series, so that what a stop interrupted is carried on."""
        try:
            all_series = await self.request("catalog.series.list", {})
        except BusRequestError as exc:
            self.log.warning("каталог недоступен: серии не просмотрены: %s", exc)
            return
        for series in all_series:
            if series.get("kind") == SeriesKind.VIDEO:
                await self._look_at(series)
        await self._end_orphans({each["id"] for each in all_series})
        await self._pump()

    def _lock(self, series_id: int) -> asyncio.Lock:
        """The looks at a series (reconcile, dispatch, the disk check) read the state and change it: two at once would each
        decide from a state the other is changing."""
        return self._locks.setdefault(series_id, asyncio.Lock())

    async def _look_at(self, series: dict) -> None:
        """Reconcile a series and tell the window if anything changed; a series the neighbours cannot answer for waits."""
        try:
            async with self._lock(series["id"]):
                done = await self._reconcile(series)
        except (BusRequestError, OSError, storage.StorageTimeout) as exc:
            self.log.warning("серия %s: сверка не удалась, повторим: %s", series.get("id"), exc)
            return
        if done.changed:
            await self._contribute(series["id"])
            await self._broadcast_queue()

    def _look_again(self, series_id: int) -> None:
        """A path an operation held is free: look at the series (a publication may be finished). Called by the storage."""
        task = asyncio.get_running_loop().create_task(self._look_at_id(series_id))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _look_at_id(self, series_id: int) -> None:
        try:
            series = await self.request("catalog.series.get", {"series_id": series_id})
        except BusRequestError as exc:
            self.log.warning("серия %d: не просмотрена после освобождения пути: %s", series_id, exc)
            await self._end_orphans()  # the series may be gone: what its deletion could not remove is retried
            return
        await self._look_at(series)
        await self._pump()

    async def _read_limit(self) -> int:
        try:
            reply = await self.request("settings.value.get",
                                       {"key": "max_parallel_downloads"})
            return max(1, int(reply.get("value") or 2))
        except (BusRequestError, ValueError, TypeError):
            return 2

    async def _read_fetch_limit(self) -> int:
        try:
            reply = await self.request("settings.value.get", {"key": FETCH_LIMIT_KEY})
            return max(1, int(reply.get("value") or 2))
        except (BusRequestError, ValueError, TypeError):
            return 2

    async def _read_threads(self) -> int:
        """The number of parallel fragments of yt-dlp (-N); the gain comes only with hls+-N (measured on the stand)."""
        try:
            reply = await self.request("settings.value.get",
                                       {"key": "yt_dlp_concurrent_fragments"})
            return max(1, int(reply.get("value") or 6))
        except (BusRequestError, ValueError, TypeError):
            return 6

    async def _naming(self, series: dict, item: dict) -> str:
        reply = await self.request("rules.format_filename", {"series": series, "media_item": item})
        return reply["filename"]

    async def _neighbours(self, series: dict) -> list[dict]:
        """The other video series whose folder is this one, holds it or lies inside it: a path of one is a path of the other."""
        base = os.path.normpath(series["save_path"])
        near = []
        for each in await self.request("catalog.series.list", {}):
            other = each.get("save_path") or ""
            if each["id"] == series["id"] or each.get("kind") != SeriesKind.VIDEO or not os.path.isabs(other):
                continue
            other = os.path.normpath(other)
            if os.path.commonpath([base, other]) in (base, other):
                near.append(each)
        return near

    # --- events -------------------------------------------------------------------

    async def on_settings_changed(self, env: Envelope) -> None:
        key = env.payload.get("key")
        if key == "yt_dlp_concurrent_fragments":
            try:
                self._threads = max(1, int(env.payload.get("value")))
                self.log.info("параллельных фрагментов (-N): %d", self._threads)
            except (ValueError, TypeError):
                pass
            return
        if key == FETCH_LIMIT_KEY:
            try:
                self._fetch_limit = max(1, int(env.payload.get("value")))
            except (ValueError, TypeError):
                return
            self._fetch_pump()
            return
        if key != "max_parallel_downloads":
            return
        try:
            self._limit = max(1, int(env.payload.get("value")))
        except (ValueError, TypeError):
            return
        self.log.info("лимит параллельных загрузок: %d", self._limit)
        await self._pump()

    async def on_plan_updated(self, env: Envelope) -> None:
        series_id = env.payload["series_id"]
        series = await self.request("catalog.series.get", {"series_id": series_id})
        async with self._lock(series_id):
            done = await self._reconcile(series)
        self.log.info("план %d: сверка — %s", series_id, done)
        if done.changed:
            await self._contribute(series_id)
        if done.dropped or done.finished:
            await self._broadcast_queue()

    async def on_dispatch(self, env: Envelope) -> None:
        series_id = env.payload["series_id"]
        series = await self.request("catalog.series.get", {"series_id": series_id})
        if not os.path.isabs(series.get("save_path") or ""):
            self.log.error("серия %d: путь сохранения не абсолютный (%r) — загрузка не запущена", series_id, series.get("save_path"))
            raise ValueError(f"путь сохранения не абсолютный: {series.get('save_path')!r}")
        async with self._lock(series_id):
            done = await self._reconcile(series, sync=True)
            created = await self._make_tasks(series)
        self.log.info("диспетч %d: сверка — %s; задач создано %d", series_id, done, created)
        if done.changed or created:
            await self._contribute(series_id)
        if done.dropped or done.finished or created:
            await self._broadcast_queue()
        await self._pump()

    async def on_pack(self, env: Envelope) -> dict:
        """The last step of a run of a sync: the container of every episode becomes what the plan asks (ЭТАП_11)."""
        series_id = env.payload["series_id"]
        series = await self.request("catalog.series.get", {"series_id": series_id})
        if series.get("kind") != SeriesKind.VIDEO:
            return asdict(Packed())
        async with self._lock(series_id):
            done = await self._packing.pack(series)
        if done.packed:
            await self._contribute(series_id)
        if done.packed or done.failed or done.waiting:
            self.log.info("серия %d: контейнеров собрано %d, не собрано %d, ждут загрузки %d", series_id, done.packed,
                          done.failed, done.waiting)
        return asdict(done)

    async def on_settle(self, env: Envelope) -> dict:
        """A sync without download: the disk follows the plan (displaced and unwanted files go, tasks nobody needs end),
        and nothing is fetched."""
        series_id = env.payload["series_id"]
        series = await self.request("catalog.series.get", {"series_id": series_id})
        async with self._lock(series_id):
            done = await self._reconcile(series, sync=True)
        self.log.info("синхронизация %d без загрузки: сверка — %s", series_id, done)
        if done.changed:
            await self._contribute(series_id)
        if done.dropped or done.finished:
            await self._broadcast_queue()
            await self._pump()
        return {"removed": done.removed, "retired": done.retired, "dropped": done.dropped}

    async def _make_tasks(self, series: dict) -> int:
        """A task for every row the plan wants that waits for a file and has none alive; the paths of the live ones follow the
        plan; a failed task whose whole video waits in its work file goes on from it. Returns how many tasks begin."""
        created = 0
        for item in await self.repo.planned_pending_items(series["id"]):
            uid, url = item["unique_id"], item.get("download_url") or item["source_url"]
            target = await self.library.target(series, item)
            if await self.repo.task_exists_active(uid):
                await self.repo.retarget_task(uid, target)  # the plan may have renumbered the episode meanwhile
                continue
            failed = await self.repo.failed_task_of(uid)
            if failed is not None:
                if await self.library.holds_work(failed, url):
                    await self.repo.requeue_task(failed["id"])
                elif not await self._end_task(failed):
                    continue  # what the failed run left is not yet gone: the next look removes it, then the task is made again
                else:
                    await self.repo.replace_or_create_task(uid, series["id"], url, target, self.library.work_path_for(series, uid))
            else:
                await self.repo.replace_or_create_task(uid, series["id"], url, target, self.library.work_path_for(series, uid))
            created += 1
            if item["status"] == "error":  # a scan that retries clears the error
                await self.repo.set_item_status(uid, "pending")
        return created

    # --- the state of the series is the truth: one look ----------------------------------

    async def _reconcile(self, series: dict, *, sync: bool = False) -> Reconciled:
        """Bring the files, the rows and the tasks of a series to what the plan says, reading the state as it is now. The
        order matters: a stopped publication is finished first (it makes rows ready), the files on their way to other names
        arrive (they free names), ready rows adopt the files that lie, the tasks that nobody needs end, then the files that
        are not wanted go.

        What the plan alone decides (a task that ends, a file displaced or taken out) is done in a sync only (`sync`, from
        dispatch and settle): a recount of the plan on an event changes nothing on the disk. Any look finishes what a sync
        or a download began (a publication, a recorded rename, a work file without its task) and reads the disk."""
        if series.get("kind") != SeriesKind.VIDEO or not os.path.isabs(series.get("save_path") or ""):
            return Reconciled()
        finished = await self._publication.finish_unfinished(series)
        renamed = await self._settle(series)
        released = await self.library.release_unbacked(series)
        adopted = await self.library.adopt(series)
        dropped = retired = removed = 0
        if sync:  # what the plan alone decides reaches the disk in a sync only
            dropped = await self._drop_tasks_without_need(series["id"])
            retired = 0  # a displaced file goes when the container of its winner is made (packing, ЭТАП_11)
            removed = await self.library.remove_unwanted(series)
        swept = await self.library.sweep_work(series)
        return Reconciled(finished, released, adopted, dropped, retired, removed, swept, renamed)

    async def _settle(self, series: dict) -> int:
        """The files on their way to other names go on; each name that is recorded is told to those who follow it."""
        recorded = await self.library.settle_names(series)
        for unique_id, filename in recorded:
            self.publish_event("downloads.item.renamed", {"series_id": series["id"], "unique_id": unique_id, "filename": filename})
        if recorded:
            self._look_again(series["id"])  # a name that is free now may be the one a downloaded file waits for
        return len(recorded)

    async def _drop_tasks_without_need(self, series_id: int) -> int:
        """A task lives while the plan wants its video and the row has no file: one whose row is gone, whose row left the plan
        (the user switched the episode off, another video took its place) or is ready already ends, whatever its state, with
        what it wrote. A file that is whole stays: it is a file of the library, not a piece of work."""
        ended = 0
        for tasks in (await self.repo.tasks_without_row(series_id), await self.repo.active_tasks_outside_plan(series_id),
                      await self.repo.unneeded_waiting_tasks(series_id)):
            ended += await self._discard_tasks(tasks)
        return ended

    # --- the tasks go one way ------------------------------------------------------

    async def _discard_tasks(self, tasks: list[dict]) -> int:
        """The one way a task that is not wanted goes, whatever its state: the worker stops, what it wrote goes, the record
        goes. A task that could not clear after itself stays, failed, as the record of what is left. Returns how many ended."""
        ended = 0
        for task in tasks:
            await self._cancel_runner(task["id"])
            ended += await self._end_task(task)
        if tasks:
            await self._pump()  # a place a stopped worker held goes to the next task
        return ended

    async def _end_task(self, task: dict) -> bool:
        """The record of a task goes once what it wrote has gone; a row that was being fetched waits for a task again."""
        if await self.library.clear_task_files(task):
            await self.repo.remove_task(task["id"], task["task_key"])
            return True
        await self.repo.keep_failed(task["id"], task["task_key"], "рабочие файлы задачи не удалены")
        return False

    async def _cancel_runner(self, task_id: int) -> None:
        """Stops the running download of a task and waits for it; ytdlp kills its processes in a finally."""
        runner = self._active.get(task_id)
        if runner is not None and not runner.done() and runner is not asyncio.current_task():
            runner.cancel()
            try:
                await runner
            except BaseException:  # noqa: BLE001 - wait for the cancellation to finish
                pass

    async def on_cancel(self, env: Envelope) -> dict:
        """One download cancelled by the user: the process stops, what it wrote goes, the task goes. It does not touch the
        plan or the switches: a scan may put the episode in the queue again (finding 45)."""
        task_id = env.payload["task_id"]
        task = await self.repo.get_task(task_id)
        if task is None:
            raise LookupError(f"задача загрузки {task_id} не найдена")
        availability.require(availability.Action.CANCEL_DOWNLOAD, task["status"])
        await self._cancel_runner(task_id)
        cleaned = await self._end_task(task)
        self.log.info("загрузка %s отменена пользователем (задача %d)%s", task["task_key"], task_id,
                      "" if cleaned else ", рабочие файлы не удалены")
        await self._contribute(task["series_id"])
        await self._broadcast_queue()
        await self._pump()
        return {"cancelled": True}

    async def on_queue_clear(self, env: Envelope) -> dict:
        deleted = await self._discard_tasks(await self.repo.clearable_tasks())
        await self._broadcast_queue()
        return {"deleted": deleted}

    async def on_series_deleted(self, env: Envelope) -> None:
        """Cascade Р-19 and Д2: the downloads of the series stop, what they wrote goes (a finished file stays: deleting a
        series deletes no files), and each task goes with it. A task that could not remove what it wrote stays, failed, as the
        record of what is left: it is tried again when the queue is cleared, when a path is free and at the start."""
        series_id = env.payload["series_id"]
        for task in await self.repo.tasks_for_series(series_id):
            await self._cancel_runner(task["id"])
            await self._end_task(task)
        await self.repo.drop_publications(series_id)
        await self._broadcast_queue()
        self.publish_event("series.purged", {"series_id": series_id, "owner": self.name})

    async def _end_orphans(self, existing: set[int] | None = None) -> None:
        """The tasks of series that are gone and could not clear after themselves are tried again."""
        try:
            ids = existing if existing is not None else {each["id"] for each in await self.request("catalog.series.list", {})}
        except BusRequestError as exc:
            self.log.warning("каталог недоступен: задачи удалённых сериалов не проверены: %s", exc)
            return
        await self._discard_tasks(await self.repo.tasks_without_series(ids))

    async def on_items_rename(self, env: Envelope) -> dict:
        """The names the plan gives the ready files of a series now: the obligation is written down, then the files move."""
        series_id = env.payload["series_id"]
        series = await self.request("catalog.series.get", {"series_id": series_id})
        if series.get("kind") != SeriesKind.VIDEO or not os.path.isabs(series.get("save_path") or ""):
            return {"renamed": 0}
        async with self._lock(series_id):
            await self.repo.record_renames(series_id, env.payload["names"])
            renamed = await self._settle(series)
        return {"renamed": renamed}

    # --- the disk check ------------------------------------------------------------

    async def on_fs_sync(self, env: Envelope) -> dict:
        """The disk check on request (scan, opening the status window): a file that is gone makes its row wait again, a file
        that lies is adopted."""
        series_id = env.payload["series_id"]
        series = await self.request("catalog.series.get", {"series_id": series_id})
        # The files of a torrent series are the torrents module's: nothing to check, publish or warn about here.
        if series["kind"] == SeriesKind.TORRENT:
            return {"adopted": 0, "lost": 0}
        # A path that does not answer (a downed NAS) is not a reason to make every row wait.
        if not await storage.call(os.path.exists, series["save_path"]):
            self.log.warning("серия %d: путь %s недоступен — сверка диска пропущена", series_id, series["save_path"])
            await self._contribute(series_id)
            return {"adopted": 0, "lost": 0}
        async with self._lock(series_id):
            lost = await self.library.release_lost(series)
            done = await self._reconcile(series)
        # The walk of the disk is the authoritative point: readiness is published always, even if nothing changed (otherwise
        # after a restart the contribution would not be sent until the first real change).
        await self._contribute(series_id)
        if lost or done.adopted or done.dropped:
            await self._broadcast_queue()
            await self._pump()
        return {"adopted": done.adopted, "lost": lost}

    async def on_set_filename(self, env: Envelope) -> None:
        """final_filename is our column; renaming reports the new name after reprocessing (Р-15). A name it frees may be
        the one a downloaded file waits for."""
        await self.repo.set_item_filename(env.payload["unique_id"], env.payload["filename"])
        series_id = await self.repo.series_of_item(env.payload["unique_id"])
        if series_id is not None:
            self.publish_event("downloads.item.renamed", {
                "series_id": series_id, "unique_id": env.payload["unique_id"],
                "filename": env.payload["filename"]})
            self._look_again(series_id)

    async def on_set_status(self, env: Envelope) -> None:
        """status is our column; slicing marks an adopted compilation as downloaded (deep adoption)."""
        await self.repo.set_item_status(env.payload["unique_id"], env.payload["status"])

    async def on_queue_get(self, env: Envelope) -> dict:
        tasks = await self._queue()
        return {"tasks": tasks, "count": len(tasks)}

    async def on_episodes_get(self, env: Envelope) -> dict:
        """Reference episodes of the series with their download state (for voices)."""
        return {"items": await self.repo.reference_items(env.payload["series_id"])}

    async def _queue(self) -> list[dict]:
        """What the window sees of the downloads: the live tasks of the plan, and the fetches of the voice-overs, which
        have no record in `download_tasks` (the executor keeps no state of them) and would never be seen otherwise."""
        fetches = [(self._fetch_jobs[key], "downloading") for key in self._fetch_active if key in self._fetch_jobs]
        fetches += [(job, "pending") for job in self._fetch_queue]
        return [*await self.repo.active_tasks(), *({
            "id": f"fetch:{job['key']}", "task_key": job["key"], "task_type": "fetch", "series_id": job["series_id"],
            "save_path": job["path"], "status": status, "progress": None} for job, status in fetches)]

    async def _broadcast_queue(self) -> None:
        tasks = await self._queue()
        self.publish_event("downloads.queue.changed", {"tasks": tasks, "count": len(tasks)})
        # The queue changed: the run of every series that has, or had, work here hears where it stands now.
        for series_id in {task["series_id"] for task in tasks if task.get("series_id")} | self._work.series:
            await self._tell_work(series_id)

    def _announce_queue(self) -> None:
        """A fetch began, waits or ended: the window sees it, though no task of the plan changed."""
        task = asyncio.create_task(self._broadcast_queue())
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    # --- the dispatcher and the download -------------------------------------------

    async def _pump(self) -> None:
        free = self._limit - len(self._active)
        if free <= 0 or self._stopping:
            return
        for task in await self.repo.next_pending(free):
            if task["id"] in self._active:
                continue
            # The place is reserved BEFORE the first await: a concurrent _pump (the end of another download) does not take the same task.
            placeholder: asyncio.Future = asyncio.get_running_loop().create_future()
            self._active[task["id"]] = placeholder
            claimed = await self.repo.claim(task["id"])
            if claimed is None:  # the task went while the place was being taken
                self._active.pop(task["id"], None)
                placeholder.cancel()
                continue
            runner = asyncio.create_task(self._run_download(claimed))
            self._active[task["id"]] = runner
            placeholder.cancel()
            self._tasks.append(runner)
            self.log.info("задача %d (%s) отправлена на загрузку", task["id"], task["task_key"])
        await self._broadcast_queue()

    async def _publish(self, task: dict, series: dict) -> str:
        """The file is whole: it takes the name of its video (see publication.py)."""
        return await self._publication.run(task, series)

    async def _run_download(self, task: dict) -> None:
        task_id, uid = task["id"], task["task_key"]
        series_id = task["series_id"]
        stopped = False  # a worker that was stopped (the plan dropped it, the module stops) frees its place for no one
        try:
            await self._contribute(series_id)

            async def on_progress(data: dict) -> None:
                now = time.monotonic()
                if (now - self._last_db_write.get(task_id, 0)
                        > PROGRESS_DB_THROTTLE) or data.get("progress") == 100:
                    await self.repo.update_progress(task_id, data)
                    self._last_db_write[task_id] = now
                if now - self._last_event > PROGRESS_EVENT_THROTTLE:
                    self._last_event = now
                    await self._broadcast_queue()

            series = await self.request("catalog.series.get", {"series_id": series_id})
            work = self.library.work_path(task)
            if task["previous_url"] != task["video_url"] and not await self.library.clear_task_files(task):
                # A work file made from another address would be taken for this video: nothing is fetched over it.
                await self.repo.mark_error(task_id, "рабочий файл прежнего адреса не удалён")
                await self.repo.set_item_status(uid, "error")
                return

            stream_url, stream_headers, stream_error, progressive = await self._resolve_stream(
                task["video_url"], ytdlp_fallback=lambda: self._candidate_may_use_ytdlp(series_id, uid))

            if stream_error is not None:
                ok, (error, transient) = False, stream_error
            else:
                kw = {"threads": self._threads, "whole_video": True}  # a reference with a hole would skew every voice-over aligned to it
                if stream_headers:
                    kw["headers"] = stream_headers
                if progressive:
                    kw["progressive"] = True
                ok, error = await self._download(stream_url, work, on_progress, **kw)
                transient = not ok and _transient_download_error(error)
            if ok:
                await self._finish_download(task, series)
            else:
                attempt = int(task.get("attempts") or 0)  # the claim has counted this run already
                budget = _download_budget()
                if budget.allows(attempt) and transient:
                    pause = budget.pause(attempt)
                    self.log.warning("загрузка %s: %s — повтор %d/%d через %.0f с", uid, error, attempt, budget.attempts, pause)
                    await asyncio.sleep(pause)
                    await self.repo.requeue_task(task_id)
                else:
                    await self.repo.mark_error(task_id, error)
                    await self.repo.set_item_status(uid, "error")
                    self.log.error("загрузка %s не удалась: %s", uid, error)
        except asyncio.CancelledError:
            stopped = True
            raise
        except PublicationUnfinished as exc:
            self.log.error("загрузка %s: %s — задача закончит публикацию, когда путь освободится, или при следующей сверке", uid, exc)
        except Exception as exc:  # noqa: BLE001 - an error does not take the module down
            self.log.exception("сбой воркера загрузки %d", task_id)
            await self.repo.mark_error(task_id, f"внутренняя ошибка: {exc}")
            await self.repo.set_item_status(uid, "error")
        finally:
            self._active.pop(task_id, None)
            self._last_db_write.pop(task_id, None)
            await self._contribute(series_id)
            await self._broadcast_queue()
            if not stopped:
                await self._pump()

    async def _finish_download(self, task: dict, series: dict) -> None:
        """A download is whole: put it under the name of its video, and do what the outcome asks."""
        outcome = await self._publish(task, series)
        if outcome == PUBLISHED:
            self.send_command("catalog.series.touch_scan_time", {"series_id": series["id"]})
            self.log.info("загрузка завершена: %s", task["task_key"])
        elif outcome == WAITING:
            self.log.info("загрузка %s готова и ждёт имени", task["task_key"])
        elif outcome == REFUSED:
            self.log.error("загрузка %s не поставлена под имя: %s", task["task_key"], (await self.repo.get_task(task["id"]) or {}).get("error_message"))

    async def _resolve_stream(self, url: str, *, max_height: int | None = None,
                              ytdlp_fallback) -> tuple[str, dict | None, tuple[str, bool] | None, bool]:
        """(stream url, headers, (error, transient), progressive) of a link, via the provider that owns it.

        `ytdlp_fallback` is an async callable asked only when the request fails: True hands the link to
        the downloader as it is, False makes the failure the error of the job, since a page of a web
        player is no stream and yt-dlp would scrape a foreign player."""
        payload = {"url": url, **({"max_height": max_height} if max_height else {})}
        try:
            reply = await self.request("sources.video.stream", payload)
            if reply.get("error"):
                return url, None, (reply["error"], bool(reply.get("transient"))), False
            if reply.get("handled"):
                return reply["stream_url"], reply.get("headers"), None, bool(reply.get("progressive"))
        except Exception as exc:
            if not await ytdlp_fallback():
                return url, None, (f"поток не получен: {exc}", True), False
            self.log.debug("sources.video.stream не обработан для %s: %s", url, exc)
        return url, None, None, False

    async def on_fetch(self, env: Envelope) -> None:
        job = {k: env.payload.get(k) for k in ("key", "url", "path", "max_height", "series_id")}
        if not os.path.isabs(job["path"] or ""):
            self.publish_event("downloads.fetch.finished", {
                **job, "ok": False, "transient": False,
                "error": "путь сохранения должен быть абсолютным"})
            return
        if job["key"] in self._fetch_active or any(
                queued["key"] == job["key"] for queued in self._fetch_queue):
            return
        self._fetch_queue.append(job)
        self._fetch_pump()
        self._announce_queue()

    async def on_work_withdrawn(self, env: Envelope) -> None:
        """The owner of the work does not need it: the jobs of the series, or of the keys it names, stop. This module
        stops them itself and removes what they wrote; nobody tells it how."""
        notice = WorkWithdrawn.model_validate(env.payload)

        def wanted(job_key: str, owner: int) -> bool:
            return owner == notice.series_id and (notice.keys is None or job_key in notice.keys)

        self._fetch_queue = [job for job in self._fetch_queue if not wanted(job["key"], job["series_id"])]
        for job_key, (runner, owner) in list(self._fetch_active.items()):
            if wanted(job_key, owner):
                runner.cancel()
        self._announce_queue()

    def background(self) -> set[asyncio.Task]:
        """The workers of the downloads and of the fetches belong to what a waiter for quiet waits for."""
        workers = {each for each in self._active.values() if isinstance(each, asyncio.Task)}
        return super().background() | workers | {runner for runner, _ in self._fetch_active.values()}

    def _fetch_pump(self) -> None:
        while self._fetch_queue and len(self._fetch_active) < self._fetch_limit:
            job = self._fetch_queue.pop(0)
            runner = asyncio.create_task(self._fetch_one(job))
            self._fetch_active[job["key"]] = (runner, job["series_id"])
            self._fetch_jobs[job["key"]] = job
            self._tasks.append(runner)
            self.publish_event("downloads.fetch.started", {
                "key": job["key"], "series_id": job["series_id"], "path": job["path"]})

    async def _fetch_one(self, job: dict) -> None:
        key, path = job["key"], job["path"]
        ok, error, transient = False, "", False

        async def ignore_progress(_data: dict) -> None:
            return None

        async def attempt_once() -> tuple[bool, str, bool]:
            stream_url, headers, stream_error, progressive = await self._resolve_stream(
                job["url"], max_height=job.get("max_height"), ytdlp_fallback=_never)
            if stream_error is not None:
                return (False, *stream_error)
            kw: dict = {"threads": self._threads}
            if headers:
                kw["headers"] = headers
            if progressive:
                kw["progressive"] = True
            done, failure = await self._download(stream_url, path, ignore_progress, **kw)
            return done, failure, not done and _transient_download_error(failure)

        budget = _download_budget()

        def report(result: tuple[bool, str, bool], attempt: int, pause: float) -> None:
            self.log.warning("загрузка %s: %s — повтор %d/%d через %.0f с", key, result[1], attempt, budget.attempts, pause)

        try:
            (ok, error, transient), _attempts, _exhausted = await retrying(
                attempt_once, final=lambda result: result[0] or not result[2], budget=budget, on_retry=report)
        except asyncio.CancelledError:
            await storage.call(Library.remove_download, path, timeout=storage.REMOVE_TIMEOUT_S, owns=path)
            self._fetch_active.pop(key, None)
            self._fetch_jobs.pop(key, None)
            self._fetch_pump()
            self._announce_queue()
            raise
        except Exception as exc:  # noqa: BLE001 - one failed fetch must not take the module down
            self.log.exception("сбой общей загрузки %s", key)
            ok, error, transient = False, f"внутренняя ошибка: {exc}", False
        if not ok:
            await storage.call(Library.remove_download, path, timeout=storage.REMOVE_TIMEOUT_S, owns=path)
        self._fetch_active.pop(key, None)
        self._fetch_jobs.pop(key, None)
        self.publish_event("downloads.fetch.finished", {
            "key": key, "ok": ok, "error": "" if ok else error, "transient": transient,
            "path": path, "series_id": job.get("series_id")})
        self._fetch_pump()
        self._announce_queue()

    async def _candidate_may_use_ytdlp(self, series_id: int, unique_id: str) -> bool:
        """Whether the downloader may take over a candidate whose stream request failed: only a VK one."""
        try:
            series = await self.request("catalog.series.get",
                                        {"series_id": series_id})
        except BusRequestError:
            return False  # the source is unknown: an error that is retried, not a scrape of a foreign player
        source = source_of_candidate(series.get("sources") or [],
                                     await self.repo.source_of_item(unique_id))
        return source is not None and source["source_type"] == SourceType.VK_VIDEO

    # --- свёртка статусов (Р-11) -----------------------------------------------------

    async def _has_work(self, series_id: int) -> bool:
        """A download of the series waits, runs or is being put under its name: work of the run of the series."""
        return any(task["status"] in LIVE_STATES for task in await self.repo.tasks_for_series(series_id))

    async def on_work_get(self, env: Envelope) -> dict:
        return (await self._work.tell(int(env.payload["series_id"]))).model_dump()

    async def _tell_work(self, series_id: int) -> None:
        """The run of the series hears when this module's work of it begins or ends (the run owner is scan)."""
        await self._work.tell(series_id)

    async def _contribute(self, series_id: int) -> None:
        await self._tell_work(series_id)
        flags = await self.repo.series_flags(series_id)
        self.publish_event("series.status.contribution", {
            "source": "downloads", "series_id": series_id, "flags": flags})
        # Д1: состав скачанного мог измениться (усыновление/докачка/сброс) —
        # толкаем счётчик в SSE, карточка обновляет «скачано» без перезагрузки.
        count = await self.repo.downloaded_count(series_id)
        self.publish_event("series.downloaded.changed",
                           {"series_id": series_id, "count": count})
