"""Module downloads: the executor of downloads (yt-dlp, ffmpeg). Decision Р-13.

What is downloaded and where it goes is the plan of a video series (modules/planner, stage 12): it asks for one file at a
time with `downloads.fetch` and hears its end. This module resolves the stream behind a link (`sources.video.stream`),
fetches it under a fuse of silence, retries a failure of the moment and tells the window what is under way. It keeps no
record of a fetch: the owner of the work asks again after a stop.

Owns the columns media_items.status and final_filename that the torrent pipeline writes through it (slicing).

Queries / commands:
  downloads.fetch {key, url, path, max_height?, series_id?, whole?, ytdlp?, need?, variant?} - the stream behind the link
      into an absolute path. `need` (DownloadNeed) names what the fetch is for and `variant` (DownloadVariant) which row it
      takes, in the marks of the window «Выход»: fetches of one need are one card. `whole`: the video of a reference; `ytdlp`: the downloader may take over a link whose stream
      request failed (a VK video). A key already waiting or under way is not asked twice. The fetches wait in one queue in
      the order asked; how many go at once is by the way each file comes (limits.py): a download by many connections takes
      `threads_per_file` of the `threads_total` (2 at once with 16 and 32), one by a single connection a place of
      `single_at_once` (4).
  downloads.limits.get {} -> {threads_total, threads_per_file, single_at_once, multi_at_once} - the limits now.
  downloads.limits.set {threads_total?, threads_per_file?, single_at_once?} -> {ok, limits} | {ok: false, reason} - the
      limits are changed and kept as settings; a value that is no whole number from 1 up is refused with its reason.
  downloads.withdraw {series_id} -> {stopped} - the owner stops the fetches of a series and waits for them: the answer
      comes once each has ended, its program stopped and what it wrote removed (work.withdrawn is the same, told).
  downloads.queue.get {} -> {tasks, count} - `count`: the downloads that wait or go; `tasks`: the cards of the downloads (core/contracts/downloads.py DownloadCard): one to
      a need of a plan, through every fetch the plan gives it — the stages with their time, a line of attempts per variant,
      the plan rebuilt on another variant, the figures of the stage under way.
  downloads.queue.clear {} -> {deleted} - the waiting fetches are dropped (their owners hear them end, failed).
  downloads.cancel {task_id} -> {cancelled} - one fetch (`fetch:<key>`) stops; its owner hears it end, failed.
  downloads.card.remove {id} -> {removed} - the user removes a card that stayed after its sync.
  downloads.item.set_filename, downloads.item.set_status - slicing reports a name and a status of a file it made or took over.

Events out:
  downloads.fetch.started {key, series_id, path}, downloads.fetch.finished {key, ok, error, transient, path, series_id}.
      A fetch is given its attempts (failure.py): the last word of the source ends it at once, a way that broke is asked
      again (a playlist is continued from the segments it got), and a fetch that spent its attempts failed. `transient`
      is only a failure that said nothing of the source (a module of ours did not answer, a name was busy): the owner
      asks the same source again at its next round; any other failure is the source's, and the owner goes to the next.
  downloads.queue.changed {tasks, count} - the cards changed, on the beat of core/clock while a download goes, and when an
      interface connects (gateway.sse.clients): SSE download_queue_update. The cards live with the sync of their series
      (scan.run.changed): while it runs every card is kept; at its end only a failed card and a finished one whose plan
      was rebuilt stay, in the table download_cards, until the user removes them or the next sync of the series begins.
Events in: scan.run.changed, settings.changed (download_threads_total, download_threads_per_file, download_single_at_once),
  work.withdrawn {series_id, keys?},
  series.deleted (the fetches of the series stop).
"""
from __future__ import annotations

import asyncio
import os
import traceback

from core import BaseModule, notices, storage
from core.clock import beat_while
from core.ffprobe import film_stream, streams_of
from core.contracts import RUN_CHANGED, WORK_WITHDRAWN, DownloadNeed, DownloadVariant, WorkWithdrawn
from core.db import Database
from core.envelope import Envelope
from core.reasons import reason
from core.retry import UNTIMED, Budget, retrying

from . import ytdlp
from .failure import Failed, kind_of
from .limits import KEYS, Limits, Places, whole
from .cards import CardBook
from .repository import DownloadsRepository

# A download says it is alive by its progress; its silent steps (a probe of the length, a check of the file) end well within
# this. A download silent for longer waits for what will not come: it is taken off, the log shows what it waited for, and
# it is tried again.
SILENCE_FUSE_S = 900.0
MAX_DOWNLOAD_ATTEMPTS = 3
RETRY_DELAY_S = 5.0
async def _always() -> bool:
    return True


async def _never() -> bool:
    """A link whose stream request failed is not handed to yt-dlp: a page of a web player is no stream."""
    return False


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


def _under_way(cards: list[dict]) -> int:
    """The downloads that wait or go: a card of an ended one is no work."""
    return sum(card["status"] not in ("done", "failed") for card in cards)


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
        self.places = Places(Limits())  # how many fetches go at once, by the way a file comes
        self._placing: dict[str, bool] = {}  # fetches that asked their stream and wait for a place of its way (multi)
        self._fetch_queue: list[dict] = []
        self._fetch_active: dict[str, tuple[asyncio.Task, int | None]] = {}
        self._fetch_jobs: dict[str, dict] = {}  # the job of every fetch under way: the window names it by its path
        self.cards = CardBook()  # the telemetry of every need under way (core/contracts/downloads.py)
        self._going = asyncio.Event()  # a card is under way: the queue is told on the beat of core/clock
        self._cancelled: set[str] = set()  # fetches the user stopped: their owners hear them end
        super().__init__(bus)

    def register(self) -> None:
        self.handle("gateway.sse.clients", self.on_sse_clients)
        self.handle("settings.changed", self.on_settings_changed)
        self.handle("downloads.queue.get", self.on_queue_get)
        self.handle("downloads.limits.get", self.on_limits_get)
        self.handle("downloads.limits.set", self.on_limits_set)
        self.handle("downloads.queue.clear", self.on_queue_clear)
        self.handle("downloads.cancel", self.on_cancel)
        self.handle("downloads.fetch", self.on_fetch)
        self.handle(WORK_WITHDRAWN, self.on_work_withdrawn)
        self.handle("downloads.withdraw", self.on_withdraw, concurrent=True)
        self.handle("downloads.item.set_filename", self.on_set_filename)
        self.handle("downloads.item.set_status", self.on_set_status)
        self.handle("series.deleted", self.on_series_deleted)
        self.handle(RUN_CHANGED, self.on_run_changed)
        self.handle("downloads.card.remove", self.on_card_remove)

    async def on_start(self) -> None:
        for card in await self.repo.cards():
            self.cards.kept(card)
        self.spawn(self._ticker(), name="downloads:cards", lasting=True)
        default = Limits()
        await self.places.set(Limits(**{name: await self._read_setting(key, getattr(default, name)) for name, key in KEYS.items()}))
        self.log.info("пределы загрузок: %s", self.places.limits.as_dict())

    async def _read_setting(self, key: str, default: int) -> int:
        try:
            reply = await self.request("settings.value.get", {"key": key})
        except (Exception,):  # noqa: BLE001 - a setting that cannot be read takes its default
            return default
        return whole(reply.get("value")) or default

    async def on_limits_get(self, env: Envelope) -> dict:
        return self.places.limits.as_dict()

    async def on_limits_set(self, env: Envelope) -> dict:
        asked = {name: env.payload[name] for name in KEYS if name in env.payload}
        wrong = next((name for name, value in asked.items() if whole(value) is None), None)
        if wrong is not None:
            return {"ok": False, "reason": reason("download_limit_invalid", name=wrong, value=str(asked[wrong]))}
        for name, value in asked.items():
            await self.request("settings.value.set", {"key": KEYS[name], "value": str(whole(value))})
        await self._apply(Limits(**{**vars(self.places.limits), **{name: whole(value) for name, value in asked.items()}}))
        return {"ok": True, "limits": self.places.limits.as_dict()}

    async def _apply(self, limits: Limits) -> None:
        if limits != self.places.limits:
            await self.places.set(limits)
            self.log.info("пределы загрузок: %s", limits.as_dict())
            self._fetch_pump()

    # --- events -------------------------------------------------------------------

    async def on_settings_changed(self, env: Envelope) -> None:
        name = next((name for name, key in KEYS.items() if key == env.payload.get("key")), None)
        value = whole(env.payload.get("value"))
        if name is not None and value is not None:
            await self._apply(Limits(**{**vars(self.places.limits), name: value}))

    async def on_series_deleted(self, env: Envelope) -> None:
        await self._withdraw(lambda job_key, owner: owner == env.payload["series_id"])

    async def on_work_withdrawn(self, env: Envelope) -> None:
        """The owner of the work does not need it: the jobs of the series, or of the keys it names, stop. This module
        stops them itself and removes what they wrote; nobody tells it how."""
        notice = WorkWithdrawn.model_validate(env.payload)
        await self._withdraw(lambda job_key, owner: owner == notice.series_id and (notice.keys is None or job_key in notice.keys))

    async def on_withdraw(self, env: Envelope) -> dict:
        """The owner stops the fetches of a series and waits for them: the answer comes once every one has ended, its
        program stopped and what it wrote removed."""
        series_id = int(env.payload["series_id"])
        return {"stopped": await self._withdraw(lambda job_key, owner: owner == series_id)}

    async def _withdraw(self, wanted) -> int:
        """The fetches wanted stop: those waiting leave the queue, those under way are cancelled and waited for (their
        programs are stopped and what they wrote goes on their way out)."""
        for job in [*self._fetch_queue, *self._fetch_jobs.values()]:
            if wanted(job["key"], job["series_id"]):
                self.cards.withdrawn(job["need_key"])
        self._fetch_queue = [job for job in self._fetch_queue if not wanted(job["key"], job["series_id"])]
        runners = [runner for job_key, (runner, owner) in list(self._fetch_active.items()) if wanted(job_key, owner)]
        for runner in runners:
            runner.cancel()
        self._announce_queue()
        await asyncio.gather(*runners, return_exceptions=True)
        return len(runners)

    async def on_cancel(self, env: Envelope) -> dict:
        """One fetch stopped by the user: its owner hears it end, failed, and decides what follows."""
        key = str(env.payload["task_id"]).removeprefix("fetch:")
        waiting = [job for job in self._fetch_queue if job["key"] == key]
        if waiting:
            self._fetch_queue = [job for job in self._fetch_queue if job["key"] != key]
            for job in waiting:
                self.cards.finished(job["need_key"], False)
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
            self.cards.finished(job["need_key"], False)
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
        return {"tasks": tasks, "count": _under_way(tasks)}

    def _queue(self) -> list[dict]:
        """The cards, with the place of each waiting one: those waiting for a place of their way first, with how many of
        their way go of how many may; then the queue, with how many fetches go of how many may whatever their way."""
        need_of = {job["key"]: job["need_key"] for job in [*self._fetch_jobs.values(), *self._fetch_queue]}
        places: dict[str, tuple[int, int, int]] = {}
        for number, (key, multi) in enumerate(self._placing.items(), start=1):
            places[need_of[key]] = (number, self.places.busy(multi), self.places.room(multi))
        going = len(self._fetch_active) - len(self._placing)
        for number, job in enumerate(self._fetch_queue, start=len(places) + 1):
            places[job["need_key"]] = (number, going, self.places.total)
        out = []
        for card in self.cards.view():
            if card.id in places:
                card.position, card.running, card.limit = places[card.id]
            out.append(card.model_dump(mode="json"))
        return out

    async def on_run_changed(self, env: Envelope) -> None:
        """The cards live with the sync of their series: a new one takes away what the one before left; at its end only a
        failed card and a finished one whose plan was rebuilt stay, kept in the store."""
        series_id, active = int(env.payload["series_id"]), bool(env.payload.get("active"))
        if active:
            await self.repo.drop_cards(self.cards.run_started(series_id))
        else:
            stay, gone = self.cards.run_ended(series_id)
            await self.repo.keep_cards(stay)
            await self.repo.drop_cards(gone)
        self._announce_queue()

    async def on_card_remove(self, env: Envelope) -> dict:
        """The user removes a card that stayed after its sync."""
        key = str(env.payload["id"])
        if key not in self.cards.cards or key in {job["need_key"] for job in [*self._fetch_queue, *self._fetch_jobs.values()]}:
            raise LookupError(f"карточка загрузки {key} не найдена или загрузка идёт")
        self.cards.forget(key)
        await self.repo.drop_cards([key])
        self._announce_queue()
        return {"removed": True}

    async def _ticker(self) -> None:
        """While a download goes the queue is told on the beat of the clock of displays (core/clock): its cards show the
        time of each stage."""
        while True:
            await self._going.wait()
            await beat_while(self.cards.running, self._announce_queue)
            self._going.clear()

    async def on_sse_clients(self, env: Envelope) -> None:
        """An interface connected: it gets the queue as it is, with no question of its own."""
        if (env.payload or {}).get("count"):
            self._broadcast_queue()

    def _broadcast_queue(self) -> None:
        tasks = self._queue()
        self.publish_event("downloads.queue.changed", {"tasks": tasks, "count": _under_way(tasks)})

    def _announce_queue(self) -> None:
        """A fetch began, waits or ended: the window sees it."""
        self._broadcast_queue()

    # --- the fetches ------------------------------------------------------------------

    async def _resolve_stream(self, url: str, *, max_height: int | None = None,
                              ytdlp_fallback) -> tuple[str, dict | None, tuple[str, Failed] | None, bool, bool]:
        """(stream url, headers, (error, what it says of the source), progressive, playlist) of a link, via the provider
        that owns it. The provider says itself whether its refusal is its last word; a provider that did not answer said
        nothing of the source.

        `ytdlp_fallback` is an async callable asked only when the request fails: True hands the link to
        the downloader as it is, False makes the failure the error of the job, since a page of a web
        player is no stream and yt-dlp would scrape a foreign player."""
        payload = {"url": url, **({"max_height": max_height} if max_height else {})}
        try:
            reply = await self.request("sources.video.stream", payload)
            if reply.get("error"):
                return (url, None, (reply["error"], Failed.BROKEN if reply.get("transient") else Failed.FINAL),
                        False, False)
            if reply.get("handled"):
                return (reply["stream_url"], reply.get("headers"), None, bool(reply.get("progressive")),
                        bool(reply.get("playlist")))
        except Exception as exc:
            if not await ytdlp_fallback():
                return url, None, (f"поток не получен: {exc}", Failed.UNASKED), False, False
            self.log.debug("sources.video.stream не обработан для %s: %s", url, exc)
        return url, None, None, False, False

    async def on_fetch(self, env: Envelope) -> None:
        job = {k: env.payload.get(k) for k in ("key", "url", "path", "max_height", "series_id", "whole", "ytdlp")}
        if not (job["path"] or "").startswith("/"):
            self._finished(job, False, "путь сохранения должен быть абсолютным", False)
            return
        if job["key"] in self._fetch_active or any(queued["key"] == job["key"] for queued in self._fetch_queue):
            return
        # What the fetch is for and which variant it takes, as its owner tells it; a fetch that names no need is one.
        need = DownloadNeed.model_validate(env.payload["need"]) if env.payload.get("need") else DownloadNeed(
            key=job["key"], series_id=job["series_id"] or 0, kind="video" if job.get("whole") else "dub",
            cap=job.get("max_height"))
        variant = DownloadVariant.model_validate(env.payload["variant"]) if env.payload.get("variant") else None
        job["need_key"] = need.key
        self.cards.queued(need, variant)
        self._going.set()
        self._fetch_queue.append(job)
        self._fetch_pump()
        self._announce_queue()

    def background(self) -> set[asyncio.Task]:
        """The workers of the fetches belong to what a waiter for quiet waits for."""
        return super().background() | {runner for runner, _ in self._fetch_active.values()}

    def _fetch_pump(self) -> None:
        """What waits starts in the order asked: a fetch asks its stream, learns its way and takes the place of its way or
        waits for it. As many start as may go at once whatever their way (`Places.total`), not counting those waiting for a
        place, and no more wait than that: a full way does not hold the queue from the other, and the links of the whole
        queue are not asked at once."""
        while (self._fetch_queue and len(self._fetch_active) - len(self._placing) < self.places.total
               and len(self._placing) < self.places.total):
            job = self._fetch_queue.pop(0)
            job["turn"] = self.places.turn()
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
        """One download with its attempts (modules/downloads/failure.py): the source's last word ends it at once; a way
        that broke is asked again, a playlist continued from the segments it got; a download that spent its attempts
        failed, and its owner goes to the next source. `transient` of the end is only a failure that said nothing of the
        source: the owner asks the same source again at its next round."""
        key, path = job["key"], job["path"]
        ok, error, said = False, "", None

        need_key = job["need_key"]
        tries = 0

        async def on_progress(data: dict) -> None:
            self.cards.progress(need_key, data)

        def resolve():
            return self._resolve_stream(job["url"], max_height=job.get("max_height"),
                                        ytdlp_fallback=_always if job.get("ytdlp") else _never)

        async def attempt_once() -> tuple[bool, str, Failed | None]:
            nonlocal tries
            tries += 1
            self.cards.attempt(need_key, tries, budget.attempts)
            self.places.asking(turn)
            try:
                stream_url, headers, stream_error, progressive, playlist = await resolve()
                if stream_error is not None:
                    return (False, *stream_error)
                multi = ytdlp.multithreaded(stream_url, headers, progressive, playlist)

                def wait() -> None:
                    # The way of the file is full: the fetch waits in the queue.
                    self.cards.waiting(need_key)
                    self._placing[key] = multi
                    self._fetch_pump()
                    self._announce_queue()

                try:
                    waited = await self.places.take(multi, turn, wait)
                finally:
                    self._placing.pop(key, None)
            finally:
                await self.places.leave(turn)
            try:
                if waited:
                    # An address a host signed may not outlive the wait: the stream is asked anew.
                    self.cards.attempt(need_key, tries, budget.attempts)
                    stream_url, headers, stream_error, progressive, playlist = await resolve()
                    if stream_error is not None:
                        return (False, *stream_error)
                return await fetched(stream_url, headers, progressive, playlist)
            finally:
                await self.places.give(multi)

        async def fetched(stream_url: str, headers, progressive: bool, playlist: bool) -> tuple[bool, str, Failed | None]:
            kw: dict = {"threads": self.places.limits.threads_per_file}
            if job.get("max_height"):
                kw["max_height"] = job["max_height"]  # a page the downloader reads itself keeps the cap of the job
            if headers:
                kw["headers"] = headers
            if progressive:
                kw["progressive"] = True
            if playlist:
                kw["playlist"] = kw["resumable"] = True  # what an attempt got is dropped at the end of the fetch
            done, failure = await self._watched(key, stream_url, path, on_progress, **kw)
            if done:
                notice = notices.notice_of(await storage.call(_head_of, path, notices.head_bytes()))
                if notice is not None:
                    # The file is a clip the host served instead of the episode (core/notices.py): no episode, and the
                    # way to it may open again (a quota of a day).
                    return False, f"источник отдал уведомление «{notice.id}» вместо серии", Failed.BROKEN
            return done, failure, None if done else kind_of(failure)

        budget = _download_budget()
        turn = job["turn"]

        def report(result: tuple[bool, str, Failed | None], attempt: int, pause: float) -> None:
            self.cards.attempt_failed(need_key)
            self.log.warning("загрузка %s: %s — повтор %d/%d через %.0f с", key, result[1], attempt, budget.attempts, pause)

        stopped = False
        try:
            (ok, error, said), _attempts, _exhausted = await retrying(
                attempt_once, final=lambda result: result[0] or result[2] is Failed.FINAL, budget=budget, on_retry=report)
        except asyncio.CancelledError:
            stopped = key not in self._cancelled
            if not stopped:
                error, said = "отменено пользователем", Failed.FINAL
            else:
                raise
        except Exception as exc:  # noqa: BLE001 - one failed fetch must not take the module down
            self.log.exception("сбой загрузки %s", key)
            ok, error, said = False, f"внутренняя ошибка: {exc}", Failed.UNASKED
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
                # The height of what came, read from the header of the file (no frame is decoded).
                film = film_stream(await streams_of(path) or []) if size else None
                self.cards.finished(need_key, ok, size_mb=round(size / 1024 ** 2, 1) if size else None,
                                    height=(film or {}).get("height"))
                self._finished(job, ok, error, not ok and said is Failed.UNASKED)
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
                    return False, f"нет признаков жизни {SILENCE_FUSE_S:.0f} с"
        finally:
            if beat is not None:
                beat.cancel()  # a download cancelled in the middle of the wait leaves no watch behind
            if not run.done():
                run.cancel()
                await asyncio.gather(run, return_exceptions=True)
