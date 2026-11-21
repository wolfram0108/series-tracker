"""Module downloads: the executor of downloads (yt-dlp, ffmpeg). Decision Р-13.

What is downloaded and where it goes is the plan of a video series (modules/planner, stage 12): it asks for one file at a
time with `downloads.fetch` and hears its end. This module resolves the stream behind a link (`sources.video.stream`),
fetches it under a fuse of silence, retries a failure of the moment and tells the window what is under way. The owner of the
work asks again after a stop; of a fetch that ended well the module keeps its file (download_done: the path, the link, the
cap, the size and the time of change of the file), and the same fetch asked again while that file lies there as it was is
answered at once, ended well — no ask of its source, no card, no place in the queue. A file that changed or went is
fetched as any.

What the cards, the queue and the limits mean, what a stream or a refused transfer says of its source, and when the next fetch
begins is counted in calc/downloads; this module holds the tasks, the places, the programs (yt-dlp, ffmpeg, the readers of
playlists) and the disk. Every reply and event is a model of core/contracts/downloads.py, handed over as it is (an event goes on
the bus as its JSON).

Queries / commands:
  downloads.fetch DownloadFetch {key, url, path, max_height?, series_id?, whole?, ytdlp?, need?, variant?, place?} - the stream behind the
      link into an absolute path. `need` (DownloadNeed) names what the fetch is for and `variant` (DownloadVariant) which row it
      takes, in the marks of the window «Выход»: fetches of one need are one card. `whole`: the video of a reference; `ytdlp`:
      the downloader may take over a link whose stream request failed (a VK video). A key already waiting or under way is not
      asked twice. `place`: the place of the episode in its series. The fetches wait in one queue: those of an earlier sync first,
      within a sync episode by episode (its video and voice-overs together, the next source of an episode before the later episodes),
      then in the order asked (calc/downloads/fetch.py `waiting_place`); how many go at once is by the way each file comes
      (calc/downloads/limits.py): a download by many connections takes `threads_per_file` of the `threads_total` (2 at once with
      16 and 32), one by a single connection a place of `single_at_once` (4), one a provider of the socket makes a place of
      `provider_at_once` (2).
  downloads.limits.get {} -> DownloadLimits {threads_total, threads_per_file, single_at_once, provider_at_once, multi_at_once} - the
      limits now.
  downloads.limits.set LimitsSet {threads_total?, threads_per_file?, single_at_once?, provider_at_once?} -> LimitsSaved {ok, limits} |
      {ok: false, reason} - the limits are changed and kept as settings; a value that is no whole number from 1 up is refused with
      its reason.
  downloads.withdraw SeriesRef {series_id} -> FetchesWithdrawn {stopped} - the owner stops the fetches of a series and waits for
      them: the answer comes once each has ended, its program stopped and what it wrote removed (work.withdrawn is the same, told).
  downloads.queue.get {} -> DownloadQueue {tasks, count} - `count`: the downloads that wait or go; `tasks`: the cards of the downloads
      (core/contracts/downloads.py DownloadCard): one to a need of a plan, through every fetch the plan gives it — the stages with
      their time, a line of attempts per variant, the plan rebuilt on another variant, the figures of the stage under way.
  downloads.queue.clear {} -> QueueCleared {deleted} - the waiting fetches are dropped (their owners hear them end, failed).
  downloads.cancel FetchCancel {task_id} -> FetchCancelled {cancelled} - one fetch (`fetch:<key>`) stops; its owner hears it end,
      failed.
  downloads.card.remove CardRemove {id} -> CardRemoved {removed} - the user removes a card that stayed after its sync.

Events out:
  downloads.fetch.started FetchStarted {key, series_id, path}, downloads.fetch.finished FetchFinished {key, ok, error, transient,
      path, series_id}. A fetch is given its attempts (failure.py): the last word of the source ends it at once, a way that broke is
      asked again (a playlist is continued from the segments it got), and a fetch that spent its attempts failed. `transient` is
      only a failure that said nothing of the source (a module of ours did not answer, a name was busy): the owner asks the same
      source again at its next round; any other failure is the source's, and the owner goes to the next. A file that came is no
      episode when it is a clip the host served in its place (core/notices.py: known by its head, a way that broke) or shorter than
      any episode (`EPISODE_MIN_S`, a minute: an upload the host broke, its last word).
  downloads.queue.changed DownloadQueue - the cards changed, on the beat of core/clock while a download goes, and when an interface
      connects (gateway.sse.clients): SSE download_queue_update. The cards live with the sync of their series (scan.run.changed):
      while it runs every card is kept; at its end only a failed card and a finished one whose plan was rebuilt stay, in the table
      download_cards, until the user removes them or the next sync of the series begins.
A link whose player downloads the file itself (`player_fetch` of its stream: Alloha) is taken by `sources.video.fetch` into the raw
file of the download, the progress by `sources.video.progress`; the rest is as for any playlist.
A link of a provider of the socket (`fetch_by: provider`) is no stream the downloader reads: the provider takes the whole file by
its own way into the path of the download (`sources.video.fetch`, contracts/provider_socket.md, section 7), the progress is its
telemetry (`sources.video.progress`), and the file that lies at the path goes the standard way: the checks of this module, the
card, the end of the fetch. The way of a download is its own place in the limits (`provider_at_once`).
Events in: sources.video.progress VideoProgress {target, ...figures}, scan.run.changed, settings.changed (download_threads_total,
  download_threads_per_file, download_single_at_once, download_provider_at_once), work.withdrawn {series_id, keys?}, series.deleted
  (the fetches of the series stop).
Resumability: the cards that stayed and the files fetches ended well with are in the store (`on_start` reads them); the fetches
themselves are the plan's, which asks again after a stop.
"""
from __future__ import annotations

import asyncio
import bisect
import os
import time
import traceback
from dataclasses import replace
from datetime import datetime, timezone

from calc.downloads import cards as cardbook
from calc.downloads.failure import Failed, Unasked, kind_of
from calc.downloads.fetch import (Job, Stream, finished, job_of, may_begin, need_of, path_refusal, refusal_words, removable, started,
                                  stops_notice, stops_series, stream_of, task_key, unreached, waiting_place)
from calc.downloads.limits import KEYS, MULTI, PROVIDER, SINGLE, after_setting, change_of, limits_of
from calc.downloads.queue import queue_of
from core import notices, storage
from core.clock import beat_while
from core.contracts import (CardRemove, CardRemoved, DownloadFetch, DownloadLimits, DownloadQueue, FetchCancel, FetchCancelled,
                            FetchesWithdrawn, LimitsSaved, LimitsSet, Progress, QueueCleared, RunChanged, SeriesDeleted, SeriesRef,
                            SettingValue, SseClients, VideoProgress, WorkWithdrawn)
from core.db import Database
from core.envelope import Envelope
from core.ffprobe import duration_of, film_stream, streams_of
from core.module import BaseModule
from core.reasons import describe, reason
from core.retry import UNTIMED, Budget, retrying
from core.topics import (DOWNLOADS_CANCEL, DOWNLOADS_CARD_REMOVE, DOWNLOADS_FETCH, DOWNLOADS_FETCH_FINISHED,
                         DOWNLOADS_FETCH_STARTED, DOWNLOADS_LIMITS_GET, DOWNLOADS_LIMITS_SET, DOWNLOADS_QUEUE_CHANGED,
                         DOWNLOADS_QUEUE_CLEAR, DOWNLOADS_QUEUE_GET, DOWNLOADS_WITHDRAW, GATEWAY_SSE_CLIENTS, SCAN_RUN_CHANGED,
                         SERIES_DELETED, SETTINGS_CHANGED, SETTINGS_VALUE_GET, SETTINGS_VALUE_SET, SOURCES_VIDEO_FETCH,
                         SOURCES_VIDEO_FETCH_STOP, SOURCES_VIDEO_PROGRESS, SOURCES_VIDEO_STREAM, WORK_WITHDRAWN)

from . import ytdlp
from .places import Places
from .repository import DownloadsRepository

# A download says it is alive by its progress, byte by byte; its silent steps (a probe of the length, a check of the file)
# end well within this. A download silent for longer waits for what will not come: it is taken off, the log shows what it
# waited for, and it is tried again like any way that broke.
SILENCE_FUSE_S = 60.0
MAX_DOWNLOAD_ATTEMPTS = 3
RETRY_DELAY_S = 5.0


def _now() -> float:
    return time.monotonic()


def _wall() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stat_of(path: str) -> tuple[int, int] | None:
    """The size and the time of change (nanoseconds) of a file; none when there is no file."""
    try:
        found = os.stat(path)
    except FileNotFoundError:
        return None
    return found.st_size, found.st_mtime_ns


def awaited(task: asyncio.Task) -> str:
    """Where a task waits: the chain of the coroutines it awaits, innermost last (a task's own stack shows only the first)."""
    frames, step = [], task.get_coro()
    while step is not None:
        frame = getattr(step, "cr_frame", None) or getattr(step, "gi_frame", None)
        if frame is None:
            break
        frames.append(frame)
        step = getattr(step, "cr_await", None) or getattr(step, "gi_yieldfrom", None)
    return "".join(traceback.format_list([(f.f_code.co_filename, f.f_lineno, f.f_code.co_name, "") for f in frames])) \
        or repr(task)


def remove_download(path: str) -> bool:
    """The file of a download with the pieces of its download: names built on its own name, nobody else's. True when none
    is left."""
    clean = True
    for each in (path, *ytdlp.download_artifacts(path)):
        if os.path.isfile(each) and not storage.remove_file(each):
            clean = False
    return clean


def _head_of(path: str, size: int) -> bytes:
    with open(path, "rb") as file:
        return file.read(size)


def _download_budget() -> Budget:
    """The one budget of a whole download: its attempts and the pause between them."""
    return Budget(attempts=MAX_DOWNLOAD_ATTEMPTS, pauses=(RETRY_DELAY_S,), limit_s=UNTIMED)


class DownloadsModule(BaseModule):
    name = "downloads"

    def __init__(self, bus, db: Database, *, downloader=ytdlp.download) -> None:
        self.repo = DownloadsRepository(db)
        self._download = downloader  # replaced in tests
        self.places = Places(DownloadLimits())  # how many fetches go at once, by the way a file comes
        self._placing: dict[str, str] = {}  # fetches that asked their stream and wait for a place of its way
        self._fetch_queue: list[Job] = []  # in the order the fetches go (calc/downloads/fetch.py `waiting_place`)
        self._asked = 0  # how many fetches were asked: the order asked, among those of one place
        self._fetch_active: dict[str, tuple[asyncio.Task, int | None]] = {}
        self._fetch_jobs: dict[str, Job] = {}  # the job of every fetch under way: the window names it by its path
        self._book = cardbook.Book()  # the telemetry of every need under way (core/contracts/downloads.py)
        self._going = asyncio.Event()  # a card is under way: the queue is told on the beat of core/clock
        self._cancelled: set[str] = set()  # fetches the user stopped: their owners hear them end
        self._player_progress: dict[str, object] = {}  # the files players download now -> where their progress goes
        super().__init__(bus)

    def register(self) -> None:
        self.handle(GATEWAY_SSE_CLIENTS, self.on_sse_clients)
        self.handle(SETTINGS_CHANGED, self.on_settings_changed)
        self.handle(DOWNLOADS_QUEUE_GET, self.on_queue_get)
        self.handle(DOWNLOADS_LIMITS_GET, self.on_limits_get)
        self.handle(DOWNLOADS_LIMITS_SET, self.on_limits_set)
        self.handle(DOWNLOADS_QUEUE_CLEAR, self.on_queue_clear)
        self.handle(DOWNLOADS_CANCEL, self.on_cancel)
        self.handle(DOWNLOADS_FETCH, self.on_fetch)
        self.handle(SOURCES_VIDEO_PROGRESS, self.on_player_progress)
        self.handle(WORK_WITHDRAWN, self.on_work_withdrawn)
        self.handle(DOWNLOADS_WITHDRAW, self.on_withdraw, concurrent=True)
        self.handle(SERIES_DELETED, self.on_series_deleted)
        self.handle(SCAN_RUN_CHANGED, self.on_run_changed)
        self.handle(DOWNLOADS_CARD_REMOVE, self.on_card_remove)

    async def on_start(self) -> None:
        for card in await self.repo.cards():
            self._book = cardbook.kept(self._book, card)
        # The files of a sync go with its folder when it ends: their records go with them.
        for path in await self.repo.done_paths():
            if await storage.call(_stat_of, path) is None:
                await self.repo.forget_done(path)
        self.spawn(self._ticker(), name="downloads:cards", lasting=True)
        await self.places.set(limits_of({name: await self._read_setting(key) for name, key in KEYS.items()}))
        self.log.info("пределы загрузок: %s", self.places.limits.model_dump(mode="json"))

    async def _read_setting(self, key: str) -> str | None:
        try:
            return (await self.reply_of(SETTINGS_VALUE_GET, {"key": key})).value
        except (Exception,):  # noqa: BLE001 - a setting that cannot be read takes its default
            return None

    async def on_limits_get(self, env: Envelope) -> DownloadLimits:
        return self.places.limits

    async def on_limits_set(self, env: Envelope) -> LimitsSaved:
        change = change_of(LimitsSet.model_validate(env.payload))
        if change.refusal is not None:
            return LimitsSaved(ok=False, reason=change.refusal)
        for setting, value in change.writes:
            await self.request(SETTINGS_VALUE_SET, {"key": setting, "value": value})
        await self._apply(change.applied_to(self.places.limits))
        return LimitsSaved(ok=True, limits=self.places.limits)

    async def _apply(self, limits: DownloadLimits) -> None:
        if limits != self.places.limits:
            await self.places.set(limits)
            self.log.info("пределы загрузок: %s", limits.model_dump(mode="json"))
            self._fetch_pump()

    # --- events -------------------------------------------------------------------

    async def on_settings_changed(self, env: Envelope) -> None:
        setting = SettingValue.model_validate(env.payload)
        changed = after_setting(self.places.limits, setting.key, setting.value)
        if changed is not None:
            await self._apply(changed)

    async def on_series_deleted(self, env: Envelope) -> None:
        await self._withdraw(stops_series(SeriesDeleted.model_validate(env.payload).series_id))

    async def on_work_withdrawn(self, env: Envelope) -> None:
        """The owner of the work does not need it: the jobs of the series, or of the keys it names, stop. This module
        stops them itself and removes what they wrote; nobody tells it how."""
        await self._withdraw(stops_notice(WorkWithdrawn.model_validate(env.payload)))

    async def on_withdraw(self, env: Envelope) -> FetchesWithdrawn:
        """The owner stops the fetches of a series and waits for them: the answer comes once every one has ended, its
        program stopped and what it wrote removed."""
        return FetchesWithdrawn(stopped=await self._withdraw(stops_series(SeriesRef.model_validate(env.payload).series_id)))

    async def _withdraw(self, wanted) -> int:
        """The fetches wanted stop: those waiting leave the queue, those under way are cancelled and waited for (their
        programs are stopped and what they wrote goes on their way out)."""
        for job in [*self._fetch_queue, *self._fetch_jobs.values()]:
            if wanted(job.key, job.series_id):
                self._book = cardbook.withdrawn(self._book, job.need_key)
        self._fetch_queue = [job for job in self._fetch_queue if not wanted(job.key, job.series_id)]
        runners = [runner for job_key, (runner, owner) in list(self._fetch_active.items()) if wanted(job_key, owner)]
        for runner in runners:
            runner.cancel()
        self._announce_queue()
        await asyncio.gather(*runners, return_exceptions=True)
        return len(runners)

    async def on_cancel(self, env: Envelope) -> FetchCancelled:
        """One fetch stopped by the user: its owner hears it end, failed, and decides what follows."""
        key = task_key(FetchCancel.model_validate(env.payload).task_id)
        waiting = [job for job in self._fetch_queue if job.key == key]
        if waiting:
            self._fetch_queue = [job for job in self._fetch_queue if job.key != key]
            for job in waiting:
                self._end_waiting(job, reason("cancelled_by_user"))
        elif key in self._fetch_active:
            self._cancelled.add(key)
            self._fetch_active[key][0].cancel()
        else:
            raise LookupError(reason("fetch_not_found", key=key))
        self._announce_queue()
        return FetchCancelled(cancelled=True)

    async def on_queue_clear(self, env: Envelope) -> QueueCleared:
        waiting, self._fetch_queue = self._fetch_queue, []
        for job in waiting:
            self._end_waiting(job, reason("queue_cleared_by_user"))
        self._announce_queue()
        return QueueCleared(deleted=len(waiting))

    def _end_waiting(self, job: Job, why: str) -> None:
        """A fetch that waited in the queue is ended by the user: its owner hears it end, failed."""
        self._book = cardbook.finished(self._book, job.need_key, False, _now(), _wall())
        self._finished(job, False, why, False)

    async def on_queue_get(self, env: Envelope) -> DownloadQueue:
        return self._queue()

    def _queue(self) -> DownloadQueue:
        return queue_of(cardbook.view(self._book, _now()),
                        need_of={job.key: job.need_key for job in [*self._fetch_jobs.values(), *self._fetch_queue]},
                        placing=self._placing, waiting=[job.need_key for job in self._fetch_queue],
                        going=len(self._fetch_active) - len(self._placing), busy=self.places.counts, limits=self.places.limits)

    async def on_run_changed(self, env: Envelope) -> None:
        """The cards live with the sync of their series: a new one takes away what the one before left; at its end only a
        failed card and a finished one whose plan was rebuilt stay, kept in the store."""
        run = RunChanged.model_validate(env.payload)
        if run.active:
            self._book, gone = cardbook.run_started(self._book, run.series_id)
            await self.repo.drop_cards(gone)
        else:
            self._book, stay, gone = cardbook.run_ended(self._book, run.series_id)
            await self.repo.keep_cards(stay)
            await self.repo.drop_cards(gone)
        self._announce_queue()

    async def on_card_remove(self, env: Envelope) -> CardRemoved:
        """The user removes a card that stayed after its sync."""
        key = CardRemove.model_validate(env.payload).id
        in_work = {job.need_key for job in [*self._fetch_queue, *self._fetch_jobs.values()]}
        if not removable(key, list(self._book.cards), in_work):
            raise LookupError(reason("card_not_removable", key=key))
        self._book = cardbook.forget(self._book, key)
        await self.repo.drop_cards([key])
        self._announce_queue()
        return CardRemoved(removed=True)

    async def _ticker(self) -> None:
        """While a download goes the queue is told on the beat of the clock of displays (core/clock): its cards show the
        time of each stage."""
        while True:
            await self._going.wait()
            await beat_while(lambda: cardbook.running(self._book), self._announce_queue)
            self._going.clear()

    async def on_sse_clients(self, env: Envelope) -> None:
        """An interface connected: it gets the queue as it is, with no question of its own."""
        if SseClients.model_validate(env.payload).count:
            self._announce_queue()

    def _announce_queue(self) -> None:
        """A fetch began, waits or ended: the window sees it."""
        self.publish_event(DOWNLOADS_QUEUE_CHANGED, self._queue().model_dump(mode="json"))

    # --- the fetches ------------------------------------------------------------------

    async def _resolve_stream(self, job: Job) -> Stream:
        """The stream of a link, via the provider that owns it (calc/downloads/fetch.py says what the answer comes to). A request that
        fails hands the link to the downloader only when the job allows it (`ytdlp`: a VK video), otherwise the failure is the error of
        the job."""
        payload = {"url": job.url, **({"max_height": job.max_height} if job.max_height else {})}
        try:
            reply = await self.reply_of(SOURCES_VIDEO_STREAM, payload)
        except Exception as exc:
            if job.ytdlp:
                self.log.debug("sources.video.stream не обработан для %s: %s", job.url, exc)
            return unreached(job.url, str(exc), bool(job.ytdlp))
        return stream_of(job.url, reply)

    async def on_fetch(self, env: Envelope) -> None:
        fetch = DownloadFetch.model_validate(env.payload)
        self._asked += 1
        job = replace(job_of(fetch), place=waiting_place(fetch, self._asked))
        if (refused := path_refusal(job)) is not None:
            self._finished(job, False, refused, False)
            return
        if job.key in self._fetch_active or any(queued.key == job.key for queued in self._fetch_queue):
            return
        if await self._kept(job):
            self.log.info("загрузка %s: файл уже скачан и проверен этой же загрузкой — взят без обращения к источнику",
                          job.key)
            self._finished(job, True, "", False)
            return
        need = need_of(job, fetch)
        job = replace(job, need_key=need.key)
        self._book = cardbook.queued(self._book, need, fetch.variant, _now(), _wall())
        self._going.set()
        bisect.insort(self._fetch_queue, job, key=lambda each: each.place)
        self._fetch_pump()
        self._announce_queue()

    async def _kept(self, job: Job) -> bool:
        """Whether the file at the path of the job is the one the same fetch (its link and its cap) ended well with: it
        lies there with the size and the time of change it had then. A record the file no longer answers goes."""
        done = await self.repo.done_of(job.path)
        if done is None or (done["url"], done["max_height"]) != (job.url, job.max_height):
            return False
        if await storage.call(_stat_of, job.path) == (done["size"], done["mtime_ns"]):
            return True
        await self.repo.forget_done(job.path)
        return False

    def background(self) -> set[asyncio.Task]:
        """The workers of the fetches belong to what a waiter for quiet waits for."""
        return super().background() | {runner for runner, _ in self._fetch_active.values()}

    def _fetch_pump(self) -> None:
        """What waits starts in the order of the queue (calc/downloads/fetch.py `may_begin` says how many): a fetch asks its stream,
        learns its way and takes the place of its way or waits for it."""
        while may_begin(len(self._fetch_queue), len(self._fetch_active), len(self._placing), self.places.total):
            job = self._fetch_queue.pop(0)
            runner = self.spawn(self._fetch_one(job, self.places.turn()))
            self._fetch_active[job.key] = (runner, job.series_id)
            self._fetch_jobs[job.key] = job
            self.publish_event(DOWNLOADS_FETCH_STARTED, started(job).model_dump(mode="json"))

    def _finished(self, job: Job, ok: bool, error: str, transient: bool) -> None:
        self.publish_event(DOWNLOADS_FETCH_FINISHED, finished(job, ok, error, transient).model_dump(mode="json"))

    async def _fetch_one(self, job: Job, turn: int) -> None:
        """One download with its attempts (modules/downloads/failure.py): the source's last word ends it at once; a way
        that broke is asked again, a playlist continued from the segments it got; a download that spent its attempts
        failed, and its owner goes to the next source. `transient` of the end is only a failure that said nothing of the
        source: the owner asks the same source again at its next round."""
        key, path = job.key, job.path
        ok, error, said = False, "", None

        need_key = job.need_key
        tries = 0

        async def on_progress(data: dict) -> None:
            self._book = cardbook.progress(self._book, need_key, Progress.model_validate(data), _now())

        async def attempt_once() -> tuple[bool, str, Failed | None]:
            nonlocal tries
            tries += 1
            self._book = cardbook.attempt(self._book, need_key, tries, budget.attempts, _now())
            self.places.asking(turn)
            try:
                stream = await self._resolve_stream(job)
                if stream.error is not None:
                    return (False, *stream.error)
                way = (PROVIDER if stream.by_provider else
                       MULTI if ytdlp.multithreaded(stream.url, stream.headers, stream.progressive, stream.playlist) else SINGLE)

                def wait() -> None:
                    # The way of the file is full: the fetch waits in the queue.
                    self._book = cardbook.waiting(self._book, need_key, _now())
                    self._placing[key] = way
                    self._fetch_pump()
                    self._announce_queue()

                try:
                    waited = await self.places.take(way, turn, wait)
                finally:
                    self._placing.pop(key, None)
            finally:
                await self.places.leave(turn)
            try:
                if waited:
                    # An address a host signed may not outlive the wait: the stream is asked anew.
                    self._book = cardbook.attempt(self._book, need_key, tries, budget.attempts, _now())
                    stream = await self._resolve_stream(job)
                    if stream.error is not None:
                        return (False, *stream.error)
                return await fetched(stream)
            finally:
                await self.places.give(way)

        async def fetched(stream: Stream) -> tuple[bool, str, Failed | None]:
            if stream.by_provider:
                # The provider takes the whole file by its own way: no address, no headers, no threads of ours.
                done, failure = await self._watched(key, stream.url, path, on_progress,
                                                    downloader=self._by_provider(job.max_height))
            else:
                kw: dict = {"threads": self.places.limits.threads_per_file}
                if stream.by_player:
                    kw["fetcher"] = self._by_player(stream.url, job.max_height, self.places.limits.threads_per_file)
                if job.max_height:
                    kw["max_height"] = job.max_height  # a page the downloader reads itself keeps the cap of the job
                if stream.headers:
                    kw["headers"] = stream.headers
                if stream.progressive:
                    kw["progressive"] = True
                if stream.playlist:
                    kw["playlist"] = kw["resumable"] = True  # what an attempt got is dropped at the end of the fetch
                done, failure = await self._watched(key, stream.url, path, on_progress, **kw)
            if done:
                notice = notices.notice_of(await storage.call(_head_of, path, notices.head_bytes()))
                if notice is not None:
                    # The file is a clip the host served instead of the episode (core/notices.py): no episode, and the
                    # way to it may open again (a quota of a day).
                    return False, reason("source_notice", notice=notice.id), Failed.BROKEN
                seconds = await duration_of(path)
                if notices.too_short(seconds):
                    # The host broke the upload of the episode: the file is all there is, an attempt again gets it again.
                    return False, reason("episode_file_too_short", seconds=f"{seconds:.1f}",
                                         minimum=f"{notices.EPISODE_MIN_S:.0f}"), Failed.FINAL
            return done, failure, None if done else kind_of(failure)

        budget = _download_budget()

        def report(result: tuple[bool, str, Failed | None], attempt: int, pause: float) -> None:
            self._book = cardbook.attempt_failed(self._book, need_key, _now())
            self.log.warning("загрузка %s: %s — повтор %d/%d через %.0f с", key, describe(result[1]), attempt,
                             budget.attempts, pause)

        stopped = False
        try:
            (ok, error, said), _attempts, _exhausted = await retrying(
                attempt_once, final=lambda result: result[0] or result[2] is Failed.FINAL, budget=budget, on_retry=report)
        except asyncio.CancelledError:
            stopped = key not in self._cancelled
            if not stopped:
                error, said = reason("cancelled_by_user"), Failed.FINAL
            else:
                raise
        except Exception as exc:  # noqa: BLE001 - one failed fetch must not take the module down
            self.log.exception("сбой загрузки %s", key)
            ok, error, said = False, reason("internal_error", cause=str(exc)), Failed.UNASKED
        finally:
            await self.places.leave(turn)  # a fetch stopped before it asked its stream holds no later one
            # The place of the fetch is given back whatever happens to what it wrote: a place kept by a fetch that ended
            # would show it under way for ever and stop its owner from asking for it again.
            if not ok:
                await self._drop_fetched(path)
            self._fetch_active.pop(key, None)
            self._fetch_jobs.pop(key, None)
            self._cancelled.discard(key)
            if not stopped:
                size = await storage.call(os.path.getsize, path) if ok and await storage.call(os.path.isfile, path) else None
                if size:
                    # Kept for the same fetch asked again (a sync taken up after a stop): the file is taken as it lies.
                    stat = await storage.call(_stat_of, path)
                    if stat is not None:
                        await self.repo.keep_done(path, job.url, job.max_height, *stat)
                # The height of what came, read from the header of the file (no frame is decoded).
                film = film_stream(await streams_of(path) or []) if size else None
                self._book = cardbook.finished(self._book, need_key, ok, _now(), _wall(),
                                               size_mb=round(size / 1024 ** 2, 1) if size else None,
                                               height=(film or {}).get("height"))
                self._finished(job, ok, error, not ok and said is Failed.UNASKED)
            self._fetch_pump()
            self._announce_queue()

    def _by_provider(self, max_height: int | None):
        """The download of a provider of the socket as a downloader of this module: `(link, path, told) -> (ok, words)`. The
        provider takes the whole file into the path itself; this module holds the name of the path meanwhile, as for any
        download, and counts what the provider tells as the signs of life of the fetch."""
        async def download(link: str, path: str, told) -> tuple[bool, str]:
            try:
                with storage.hold(path):
                    return await self._transfer(link, path, max_height, told)
            except storage.StorageBusy:  # an operation on this name has not ended: a file written now could be lost
                return False, Unasked(reason("path_busy"))
        return download

    async def _transfer(self, link: str, path: str, max_height: int | None, told) -> tuple[bool, str]:
        """(ok, words of a failure) of the file the player of the link downloads into `path` (`sources.video.fetch`), the
        progress told by the player's events. The words carry what they say of the source: its last word (`Final`), or nothing
        of it (`Unasked`); any other is a way that broke."""
        self._player_progress[path] = told
        payload = {"url": link, "target": path, **({"max_height": max_height} if max_height else {})}
        try:
            reply = await self.reply_of(SOURCES_VIDEO_FETCH, payload)
        except asyncio.CancelledError:
            # The file is given up: the provider is told by the closed connection, and the transfer stops.
            self.spawn(self.request(SOURCES_VIDEO_FETCH_STOP, {"target": path}))
            raise
        except Exception as exc:  # noqa: BLE001 - a provider that did not answer said nothing of the source
            return False, Unasked(reason("player_fetch_silent", cause=str(exc)))
        finally:
            self._player_progress.pop(path, None)
        return (True, "") if reply.ok else (False, refusal_words(reply))

    def _by_player(self, url: str, max_height: int | None, connections: int):
        """The transfer of a file its player downloads itself (`sources.video.fetch`): the segments into the raw file of
        the download, the progress told by the player's events. (ok, words of a failure, seconds, longest segment)."""
        async def fetch(target: str, on_progress) -> tuple[bool, str, float, float]:
            self._player_progress[target] = on_progress
            payload = {"url": url, "target": target, "connections": connections,
                       **({"max_height": max_height} if max_height else {})}
            try:
                reply = await self.reply_of(SOURCES_VIDEO_FETCH, payload)
            except asyncio.CancelledError:
                # The file is given up: the player stops taking it.
                self.spawn(self.request(SOURCES_VIDEO_FETCH_STOP, {"target": target}))
                raise
            except Exception as exc:  # noqa: BLE001 - a player that did not answer said nothing of the source
                return False, Unasked(reason("player_fetch_silent", cause=str(exc))), 0.0, 0.0
            finally:
                self._player_progress.pop(target, None)
            if not reply.ok:
                return False, refusal_words(reply), 0.0, 0.0
            return True, "", float(reply.seconds or 0), float(reply.longest or 0)
        return fetch

    async def on_player_progress(self, env: Envelope) -> None:
        progress = VideoProgress.model_validate(env.payload)
        told = self._player_progress.get(progress.target)
        if told is not None:
            await told(progress.model_dump(exclude={"target"}, exclude_unset=True))

    async def _drop_fetched(self, path: str) -> None:
        """What a fetch that did not end whole wrote goes; a name another operation still owns stays, and the log says so."""
        try:
            await storage.call(remove_download, path, timeout=storage.REMOVE_TIMEOUT_S, owns=path)
        except OSError as exc:
            self.log.warning("файлы незавершённой загрузки %s не удалены: %s", path, exc)

    async def _watched(self, key: str, url: str, path: str, on_progress, *, downloader=None, **kw) -> tuple[bool, str]:
        """A download under the fuse of silence: each report of progress is a sign of life, and a download that gives none
        for SILENCE_FUSE_S is taken off with what it waited for in the log; its failure is one the queue tries again.
        `downloader` takes the place of the downloader of this module for a file a provider takes itself."""
        alive = asyncio.Event()

        async def told(data: dict) -> None:
            alive.set()
            await on_progress(data)

        run = asyncio.ensure_future((downloader or self._download)(url, path, told, **kw))
        beat: asyncio.Future | None = None
        try:
            while True:
                alive.clear()
                beat = asyncio.ensure_future(alive.wait())
                done, _ = await asyncio.wait({run, beat}, timeout=SILENCE_FUSE_S, return_when=asyncio.FIRST_COMPLETED)
                beat.cancel()
                if run in done:
                    return run.result()
                if not done:
                    self.log.warning("загрузка %s: нет признаков жизни %.0f с — снята и повторится; ждала:\n%s",
                                     key, SILENCE_FUSE_S, awaited(run))
                    run.cancel()
                    await asyncio.gather(run, return_exceptions=True)
                    return False, reason("no_signs_of_life", seconds=f"{SILENCE_FUSE_S:.0f}")
        finally:
            if beat is not None:
                beat.cancel()  # a download cancelled in the middle of the wait leaves no watch behind
            if not run.done():
                run.cancel()
                await asyncio.gather(run, return_exceptions=True)
