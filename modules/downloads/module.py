"""Module downloads: the executor of downloads (yt-dlp, ffmpeg). Decision Р-13.

What is downloaded and where it goes is the plan of a video series (modules/planner, stage 12): it asks for one file at a
time with `downloads.fetch` and hears its end. This module resolves the stream behind a link (`sources.video.stream`),
fetches it under a fuse of silence, retries a failure of the moment and tells the window what is under way. It keeps no
record of a fetch: the owner of the work asks again after a stop.

Owns the columns media_items.status and final_filename that the torrent pipeline writes through it (slicing).

Queries / commands:
  downloads.fetch {key, url, path, max_height?, series_id?, whole?, ytdlp?} - the stream behind the link into an absolute
      path. `whole`: a video that must come whole (a reference); `ytdlp`: the downloader may take over a link whose stream
      request failed (a VK video). A key already waiting or under way is not asked twice. The videos of the references
      (`whole`) and the rest (voice-overs, compilations) wait in lines of their own, each with its limit of parallel
      fetches: max_parallel_downloads and max_parallel_dub_downloads (2 by default each).
  downloads.queue.get {} -> {tasks, count} - the fetches waiting (`pending`) and under way (`downloading`, with progress,
      speed, time left and size).
  downloads.queue.clear {} -> {deleted} - the waiting fetches are dropped (their owners hear them end, failed).
  downloads.cancel {task_id} -> {cancelled} - one fetch (`fetch:<key>`) stops; its owner hears it end, failed.
  downloads.item.set_filename, downloads.item.set_status - slicing reports a name and a status of a file it made or took over.

Events out:
  downloads.fetch.started {key, series_id, path}, downloads.fetch.finished {key, ok, error, transient, path, series_id}.
  downloads.queue.changed {tasks, count} - the queue changed (throttled on progress), and when an interface connects
      (gateway.sse.clients): SSE download_queue_update.
Events in: settings.changed (yt_dlp_concurrent_fragments, max_parallel_downloads, max_parallel_dub_downloads),
  work.withdrawn {series_id, keys?},
  series.deleted (the fetches of the series stop).
"""
from __future__ import annotations

import asyncio
from collections import Counter
import os
import time
import traceback

from core import BaseModule, storage
from core.contracts import WORK_WITHDRAWN, WorkWithdrawn
from core.db import Database
from core.envelope import Envelope
from core.retry import UNTIMED, Budget, retrying

from . import ytdlp
from .repository import DownloadsRepository

PROGRESS_EVENT_THROTTLE = 1.0  # seconds between queue events on progress
# A download says it is alive by its progress; its silent steps (a probe of the length, a check of the file) end well within
# this. A download silent for longer waits for what will not come: it is taken off, the log shows what it waited for, and
# it is tried again.
SILENCE_FUSE_S = 900.0
# What the queue tells of a fetch as it goes.
FETCH_PROGRESS = ("progress", "dlspeed", "eta", "total_size_mb")
MAX_DOWNLOAD_ATTEMPTS = 3
RETRY_DELAY_S = 5.0
# The setting of the limit of each line of fetches: the videos of the references, and the rest.
LIMIT_KEYS = {"video": "max_parallel_downloads", "other": "max_parallel_dub_downloads"}
# A download a signal interrupted (ytdlp.INTERRUPTED) is asked again: at a stop of the service the repeat is cancelled with
# the module, and the work is taken up at the next start.
_TRANSIENT_MARKERS = ("прервано сигналом", "timed out", "timeout", "connection", "network", "обрыв", "input/output error",
                      "temporarily", "server returned 5", "server returned 429",
                      "server returned 403", "http error 403", "http error 408",
                      "http error 429", "http error 5")


async def _always() -> bool:
    return True


async def _never() -> bool:
    """A link whose stream request failed is not handed to yt-dlp: a page of a web player is no stream."""
    return False


def _transient_download_error(error: str) -> bool:
    lowered = (error or "").lower()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)


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


def _download_budget() -> Budget:
    """The one budget of a whole download: its attempts and the pause between them."""
    return Budget(attempts=MAX_DOWNLOAD_ATTEMPTS, pauses=(RETRY_DELAY_S,), limit_s=UNTIMED)


class DownloadsModule(BaseModule):
    name = "downloads"

    def __init__(self, bus, db: Database, *, downloader=ytdlp.download) -> None:
        self.repo = DownloadsRepository(db)
        self._download = downloader  # replaced in tests
        self._fetch_limits = {"video": 2, "other": 2}  # parallel fetches of each line (LIMIT_KEYS)
        self._fetch_queue: list[dict] = []
        self._fetch_active: dict[str, tuple[asyncio.Task, int | None]] = {}
        self._fetch_jobs: dict[str, dict] = {}  # the job of every fetch under way: the window names it by its path
        self._threads = 6             # fragments per file (yt-dlp -N)
        self._last_event = 0.0
        self._cancelled: set[str] = set()  # fetches the user stopped: their owners hear them end
        super().__init__(bus)

    def register(self) -> None:
        self.handle("gateway.sse.clients", self.on_sse_clients)
        self.handle("settings.changed", self.on_settings_changed)
        self.handle("downloads.queue.get", self.on_queue_get)
        self.handle("downloads.queue.clear", self.on_queue_clear)
        self.handle("downloads.cancel", self.on_cancel)
        self.handle("downloads.fetch", self.on_fetch)
        self.handle(WORK_WITHDRAWN, self.on_work_withdrawn)
        self.handle("downloads.item.set_filename", self.on_set_filename)
        self.handle("downloads.item.set_status", self.on_set_status)
        self.handle("series.deleted", self.on_series_deleted)

    async def on_start(self) -> None:
        self._threads = await self._read_setting("yt_dlp_concurrent_fragments", 6)
        for line, key in LIMIT_KEYS.items():
            self._fetch_limits[line] = await self._read_setting(key, 2)

    async def _read_setting(self, key: str, default: int) -> int:
        try:
            reply = await self.request("settings.value.get", {"key": key})
            return max(1, int(reply.get("value") or default))
        except (Exception,):  # noqa: BLE001 - a setting that cannot be read takes its default
            return default

    # --- events -------------------------------------------------------------------

    async def on_settings_changed(self, env: Envelope) -> None:
        key = env.payload.get("key")
        try:
            value = max(1, int(env.payload.get("value")))
        except (ValueError, TypeError):
            return
        if key == "yt_dlp_concurrent_fragments":
            self._threads = value
            self.log.info("параллельных фрагментов (-N): %d", self._threads)
        elif key in LIMIT_KEYS.values():
            self._fetch_limits[next(line for line, each in LIMIT_KEYS.items() if each == key)] = value
            self._fetch_pump()

    async def on_series_deleted(self, env: Envelope) -> None:
        await self._withdraw(lambda job_key, owner: owner == env.payload["series_id"])

    async def on_work_withdrawn(self, env: Envelope) -> None:
        """The owner of the work does not need it: the jobs of the series, or of the keys it names, stop. This module
        stops them itself and removes what they wrote; nobody tells it how."""
        notice = WorkWithdrawn.model_validate(env.payload)
        await self._withdraw(lambda job_key, owner: owner == notice.series_id and (notice.keys is None or job_key in notice.keys))

    async def _withdraw(self, wanted) -> None:
        self._fetch_queue = [job for job in self._fetch_queue if not wanted(job["key"], job["series_id"])]
        for job_key, (runner, owner) in list(self._fetch_active.items()):
            if wanted(job_key, owner):
                runner.cancel()
        self._announce_queue()

    async def on_cancel(self, env: Envelope) -> dict:
        """One fetch stopped by the user: its owner hears it end, failed, and decides what follows."""
        key = str(env.payload["task_id"]).removeprefix("fetch:")
        waiting = [job for job in self._fetch_queue if job["key"] == key]
        if waiting:
            self._fetch_queue = [job for job in self._fetch_queue if job["key"] != key]
            for job in waiting:
                self._finished(job, False, "отменено пользователем", False)
        elif key in self._fetch_active:
            self._cancelled.add(key)
            self._fetch_active[key][0].cancel()
        else:
            raise LookupError(f"загрузка {key} не найдена")
        self._announce_queue()
        return {"cancelled": True}

    async def on_queue_clear(self, env: Envelope) -> dict:
        waiting, self._fetch_queue = self._fetch_queue, []
        for job in waiting:
            self._finished(job, False, "очередь очищена пользователем", False)
        self._announce_queue()
        return {"deleted": len(waiting)}

    async def on_set_filename(self, env: Envelope) -> None:
        """final_filename is our column; renaming reports the new name after reprocessing (Р-15)."""
        await self.repo.set_item_filename(env.payload["unique_id"], env.payload["filename"])

    async def on_set_status(self, env: Envelope) -> None:
        """status is our column; slicing marks an adopted compilation as downloaded (deep adoption)."""
        await self.repo.set_item_status(env.payload["unique_id"], env.payload["status"])

    async def on_queue_get(self, env: Envelope) -> dict:
        tasks = self._queue()
        return {"tasks": tasks, "count": len(tasks)}

    def _queue(self) -> list[dict]:
        fetches = [(self._fetch_jobs[key], "downloading") for key in self._fetch_active if key in self._fetch_jobs]
        fetches += [(job, "pending") for job in self._fetch_queue]
        return [{"id": f"fetch:{job['key']}", "task_key": job["key"], "task_type": "fetch", "series_id": job["series_id"],
                 "save_path": job["path"], "status": status,
                 **{name: (job.get("progress") or {}).get(name) for name in FETCH_PROGRESS}} for job, status in fetches]

    async def on_sse_clients(self, env: Envelope) -> None:
        """An interface connected: it gets the queue as it is, with no question of its own."""
        if (env.payload or {}).get("count"):
            self._broadcast_queue()

    def _broadcast_queue(self) -> None:
        tasks = self._queue()
        self.publish_event("downloads.queue.changed", {"tasks": tasks, "count": len(tasks)})

    def _announce_queue(self) -> None:
        """A fetch began, waits or ended: the window sees it."""
        self._broadcast_queue()

    # --- the fetches ------------------------------------------------------------------

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
        job = {k: env.payload.get(k) for k in ("key", "url", "path", "max_height", "series_id", "whole", "ytdlp")}
        if not (job["path"] or "").startswith("/"):
            self._finished(job, False, "путь сохранения должен быть абсолютным", False)
            return
        if job["key"] in self._fetch_active or any(queued["key"] == job["key"] for queued in self._fetch_queue):
            return
        self._fetch_queue.append(job)
        self._fetch_pump()
        self._announce_queue()

    def background(self) -> set[asyncio.Task]:
        """The workers of the fetches belong to what a waiter for quiet waits for."""
        return super().background() | {runner for runner, _ in self._fetch_active.values()}

    @staticmethod
    def _line(job: dict) -> str:
        return "video" if job.get("whole") else "other"

    def _fetch_pump(self) -> None:
        """Each line starts what waits in it, in the order asked, up to its limit."""
        busy = Counter(self._line(self._fetch_jobs[key]) for key in self._fetch_active if key in self._fetch_jobs)
        for job in list(self._fetch_queue):
            line = self._line(job)
            if busy[line] >= self._fetch_limits[line]:
                continue
            busy[line] += 1
            self._fetch_queue.remove(job)
            runner = self.spawn(self._fetch_one(job))
            self._fetch_active[job["key"]] = (runner, job["series_id"])
            self._fetch_jobs[job["key"]] = job
            self.publish_event("downloads.fetch.started", {
                "key": job["key"], "series_id": job["series_id"], "path": job["path"]})

    def _finished(self, job: dict, ok: bool, error: str, transient: bool) -> None:
        self.publish_event("downloads.fetch.finished", {
            "key": job["key"], "ok": ok, "error": "" if ok else error, "transient": transient,
            "path": job["path"], "series_id": job.get("series_id")})

    async def _fetch_one(self, job: dict) -> None:
        key, path = job["key"], job["path"]
        ok, error, transient = False, "", False

        async def on_progress(data: dict) -> None:
            # A fetch has no record of its own: what the window shows of it is kept with the job.
            job["progress"] = {name: data.get(name) for name in FETCH_PROGRESS if data.get(name) is not None}
            now = time.monotonic()
            if now - self._last_event > PROGRESS_EVENT_THROTTLE:
                self._last_event = now
                self._announce_queue()

        async def attempt_once() -> tuple[bool, str, bool]:
            stream_url, headers, stream_error, progressive = await self._resolve_stream(
                job["url"], max_height=job.get("max_height"), ytdlp_fallback=_always if job.get("ytdlp") else _never)
            if stream_error is not None:
                return (False, *stream_error)
            kw: dict = {"threads": self._threads}
            if job.get("whole"):
                kw["whole_video"] = True  # a reference with a hole would skew every voice-over aligned to it
            if job.get("max_height"):
                kw["max_height"] = job["max_height"]  # a page the downloader reads itself keeps the cap of the job
            if headers:
                kw["headers"] = headers
            if progressive:
                kw["progressive"] = True
            done, failure = await self._watched(key, stream_url, path, on_progress, **kw)
            return done, failure, not done and _transient_download_error(failure)

        budget = _download_budget()

        def report(result: tuple[bool, str, bool], attempt: int, pause: float) -> None:
            self.log.warning("загрузка %s: %s — повтор %d/%d через %.0f с", key, result[1], attempt, budget.attempts, pause)

        stopped = False
        try:
            (ok, error, transient), _attempts, _exhausted = await retrying(
                attempt_once, final=lambda result: result[0] or not result[2], budget=budget, on_retry=report)
        except asyncio.CancelledError:
            stopped = key not in self._cancelled
            if not stopped:
                error, transient = "отменено пользователем", False
            else:
                raise
        except Exception as exc:  # noqa: BLE001 - one failed fetch must not take the module down
            self.log.exception("сбой загрузки %s", key)
            ok, error, transient = False, f"внутренняя ошибка: {exc}", False
        finally:
            # The place of the fetch is given back whatever happens to what it wrote: a place kept by a fetch that ended
            # would show it under way for ever and stop its owner from asking for it again.
            if not ok:
                await self._drop_fetched(path)
            self._fetch_active.pop(key, None)
            self._fetch_jobs.pop(key, None)
            self._cancelled.discard(key)
            if not stopped:
                self._finished(job, ok, error, transient)
            self._fetch_pump()
            self._announce_queue()

    async def _drop_fetched(self, path: str) -> None:
        """What a fetch that did not end whole wrote goes; a name another operation still owns stays, and the log says so."""
        try:
            await storage.call(remove_download, path, timeout=storage.REMOVE_TIMEOUT_S, owns=path)
        except OSError as exc:
            self.log.warning("файлы незавершённой загрузки %s не удалены: %s", path, exc)

    async def _watched(self, key: str, url: str, path: str, on_progress, **kw) -> tuple[bool, str]:
        """A download under the fuse of silence: each report of progress is a sign of life, and a download that gives none
        for SILENCE_FUSE_S is taken off with what it waited for in the log; its failure is one the queue tries again."""
        alive = asyncio.Event()

        async def told(data: dict) -> None:
            alive.set()
            await on_progress(data)

        run = asyncio.ensure_future(self._download(url, path, told, **kw))
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
                    return False, f"нет признаков жизни {SILENCE_FUSE_S:.0f} с (timeout)"
        finally:
            if beat is not None:
                beat.cancel()  # a download cancelled in the middle of the wait leaves no watch behind
            if not run.done():
                run.cancel()
                await asyncio.gather(run, return_exceptions=True)
