"""Module voices: the voice-overs of a series (mission voice-alignment, stages 4 and 6).

Owns voice_dubs: one row per (series, episode, voice) — the additional voice-overs that are
downloaded next to the reference video and later aligned to it.

Settings: voices.source_priority (JSON list of player kinds), voices.aliases and voices.player_aliases
(JSON objects {alias: canonical}: which voice-over names and which player kinds mean the same),
voices.temp_root, voices.max_temp_episodes. They are read together (settings.py): a wrong value is logged
once and its default is used.

Query:
  voices.layout {sources: [{url, player, studio, kind, episodes, links?}], mode?, reference?}
      -> {mode, reference, voices: [{key, label, role, source, fallbacks}], skipped, priority}
      The source priority is the setting `voices.source_priority` (JSON list of player kinds);
      layout.DEFAULT_PRIORITY when unset.
  voices.dubs.get {series_id} -> {items: [{season, episode, voice_key, voice_label, status,
      attempt, error, temp_path, job_id, aligned_path, sidecar_path, outcome, ref_uid, ref_path,
      detail, state}]}  (detail = {code, params} of the outcome or error, see reasons.py; the
      fields `error` and `outcome` hold the stored reason as it is)
Command:
  voices.dispatch {series_id} — sent by scan after an explicit scan: read the catalog of the site,
      lay the voices out around the selected reference, record a voice_dubs row per planned
      reference episode and voice, and download what is not on disk yet into the temporary
      directory (setting `voices.temp_root`, default /nas/torrents/series-tracker).
Commands out: downloads.fetch, downloads.fetch.cancel. Queries to conform: submit (with a key that finds
            the job again), job.find, cancel (a job whose reference was replaced or whose series was deleted;
            what could not be cancelled is retried from the cleanup journal).

Events in:  downloads.fetch.started and .finished (our keys `dub:<id>`), conform.queue.changed (waiting or running
            jobs of the service), series.downloaded.changed,
            downloads.item.renamed, library.relocation.finished, conform.job.finished,
            conform.daemon.up, series.deleted.
Events out: voices.dub.changed {series_id, season, episode, voice_key, voice_label, status, state, detail, rev} —
            rev grows with every event, so the interface keeps the newest of two it receives out of order;
            every write to a row is announced, including the creation of a planned row and its removal;
            voices.dubs.snapshot {rev, items: [{series_id, season, episode, voice_key, voice_label, status,
            state, detail}]} — every row, published when an interface connects (gateway.sse.clients);
            state is what the user sees (states.py: planned, download_queued, downloading, align_queued, running,
            done, error), status the
            step behind it; detail is the reason of a failure, a rejection or a stale track as
            {code, params} (code `external`: the words of another system in params.text), else null;
            status is one of
            ready, aligning, aligned, placed, rejected, failed, moved (the track followed its video),
            stale (its reference was replaced) and removed (the episode and its video are gone);
            series.purged,
            voices.episode.settled {series_id, season, episode} — every voice-over of the episode is
            placed, rejected or failed and its temporary files are gone.

A voice keeps the key under which its rows were recorded when changed rules would give it another one.
Paths of the working files are taken from the rows (folder of `temp_path`), never from the settings of the
moment or from the service's path map.

Downloading is delegated to downloads.fetch. A dub tries its main source first, then the other
sources of the same voice by priority; when none gives the file the row is `failed`, a warning is
logged, and the next dispatch offers it again from the main source. A dub is `ready` once its file
is on disk; rows left `downloading` by a restart are sent again on start.

Alignment (stage 7). An episode goes to the alignment service as one job once its reference video
is on disk and none of its voice-overs is still being downloaded or aligned: the `ready` ones are
sent (conform.submit). A voice-over that was not downloaded does not hold the episode back, and one
that arrives later makes a job of its own. When a job ends, a voice-over that converged and raised
neither `suspect` nor `critical` becomes `aligned`; any other is `rejected` with the reason and a
job that failed makes its voice-overs `failed` (the next scan offers them again). An `aligned`
track is moved next to the reference video as `<video name>.<voice title>.<lang>.<ext>` and the row
becomes `placed` with the track's path; the series stays playable at every step. When every
voice-over of the episode is placed, rejected or failed, the episode's temporary directory is
removed. Without the service, or while the reference is missing, rows wait and are retried on
series.downloaded.changed and conform.daemon.up. Following the reference (stage 8). A track is valid for the reference it was aligned to (`ref_uid`).
The same reference under another name or in another directory: the track is moved after it. A
different reference (a higher resolution, another voice or source took the slot): every track
of the episode is removed from the library at once, since it would play out of step with the new
video, and the voice-overs start over against the new reference. An episode that left the plan
and whose video is gone loses its whole set; a track the user deleted is not made again.
Deleting a series removes only records and temporary files: videos stay, and so do their tracks.

Setting `voices.max_temp_episodes` (integer; 0 or unset: no limit) caps how many episodes may hold
temporary files at once: further voice-overs stay planned until an episode settles.

The service leaves an empty `_tmp` directory
next to every reference video it worked on; it is removed once no job of the series is running.
The series' own folder in the temporary directory goes when every voice-over of the series is
settled and the folder is empty.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from collections import OrderedDict, defaultdict

from core import BaseModule, BusRequestError, is_video_source
from core.contracts import (AlignmentJob, JobStatus, DubEvent, DubEventStatus as E, DubItem, DubRecord, DubsSnapshot, DubStatus as S,
                            SnapshotItem)
from core import storage
from core.db import Database
from core.envelope import Envelope

from .identity import Equivalence, same_voice
from .alignment import (ENDED, dub_folder_of_track, job_failure, job_folders, refusal, result_for, scratch_of,
                        track_of)
from .layout import download_chain, plan_layout
from .lifecycle import FINAL, WORKING
from .outputs import Output, SidecarOutput
from .reasons import parse as parse_reason, reason
from .repository import VoicesRepository
from .settings import PREFIX, VoiceSettings, parse
from .states import public_hint, public_state

DUB_MAX_HEIGHT = 720
PARSE_TIMEOUT_S = 180
SUBMIT_TIMEOUT_S = 60
SUBMIT_RETRY_S = 30.0
SUBMIT_RETRIES = 5
IO_TIMEOUT_S = 30.0
REMOVE_TIMEOUT_S = 300.0
MOVE_TIMEOUT_S = 900.0
# What the cleanup journal can hold: a plain file, a tree, an artifact of the output, a job of the service.
PATH, TREE, ARTIFACT, JOB = "path", "tree", "artifact", "job"
UNCLAIMED_JOBS = 256


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("._") or "dub"


def episode_dir(root: str, series_id: int, season: int, episode: int) -> str:
    return os.path.join(root, str(series_id), f"s{season:02d}e{episode:02d}")


def temp_path(root: str, series_id: int, season: int, episode: int, voice_key: str) -> str:
    return os.path.join(episode_dir(root, series_id, season, episode), f"{_safe(voice_key)}.mp4")


class VoicesModule(BaseModule):
    name = "voices"

    def __init__(self, bus, db: Database | None = None, *, output: Output | None = None) -> None:
        self.repo = VoicesRepository(db) if db is not None else None
        self._output = output or SidecarOutput()
        self._locks: dict[int, asyncio.Lock] = {}
        self._kicked: set[int] = set()
        self._retries: dict[int, int] = {}
        self._retry_pending: set[int] = set()
        self._job_status: dict[str, str] = {}
        self._fetching: set[int] = set()
        self._admit_lock = asyncio.Lock()
        self._unclaimed: OrderedDict[str, AlignmentJob] = OrderedDict()
        self._rev = 0
        self._reported: set[str] = set()
        super().__init__(bus)

    def register(self) -> None:
        self.handle("voices.layout", self.on_layout)
        self.handle("voices.dubs.get", self.on_dubs_get)
        self.handle("voices.dispatch", self.on_dispatch, concurrent=True)
        self.handle("downloads.fetch.started", self.on_fetch_started)
        self.handle("downloads.fetch.finished", self.on_fetch_finished)
        self.handle("series.downloaded.changed", self.on_downloaded)
        self.handle("downloads.item.renamed", self.on_downloaded)
        self.handle("library.relocation.finished", self.on_relocated)
        self.handle("conform.job.finished", self.on_job_finished)
        self.handle("conform.queue.changed", self.on_conform_picture)
        self.handle("conform.daemon.up", self.on_daemon_up)
        self.handle("series.deleted", self.on_series_deleted)
        self.handle("gateway.sse.clients", self.on_clients)

    async def on_start(self) -> None:
        if self.repo is None:
            return
        requeued = await self.repo.requeue_interrupted()
        pending = await self.repo.pending()
        if requeued or pending:
            self.log.info("озвучки: к докачке %d (прерванных %d)", len(pending), requeued)
        await self._admit()
        await self._sweep_cleanup()
        for series_id in await self.repo.series_waiting():
            self._kick(series_id)


    async def _io(self, fn, *args, timeout: float = IO_TIMEOUT_S):
        """A call into the storage that waits for a limited time."""
        return await storage.call(fn, *args, timeout=timeout)

    async def _settings(self) -> VoiceSettings:
        """Every setting of the module in one read; a problem with a value is logged once, then the default is used."""
        reply = await self.request("settings.values.by_prefix", {"prefix": PREFIX}, timeout=10)
        settings = parse(reply.get("values") or {})
        for problem in sorted(set(settings.problems) - self._reported):
            self.log.warning("озвучки: настройка: %s", problem)
        self._reported = set(settings.problems)
        return settings

    async def _temp_root(self) -> str:
        return (await self._settings()).temp_root


    async def on_layout(self, env: Envelope) -> dict:
        payload = env.payload
        settings = await self._settings()
        return plan_layout(payload["sources"], payload.get("mode") or "manual",
                           payload.get("reference"), list(settings.priority), settings.rules)

    async def on_dubs_get(self, env: Envelope) -> dict:
        rows = await self.repo.for_series(int(env.payload["series_id"]))
        return {"items": [self._record(row).model_dump(mode="json") for row in rows]}


    async def on_dispatch(self, env: Envelope) -> None:
        series_id = int(env.payload["series_id"])
        async with self._locks.setdefault(series_id, asyncio.Lock()):
            try:
                await self._dispatch(series_id)
            except (BusRequestError, LookupError, ValueError, OSError, asyncio.TimeoutError) as exc:
                self.log.warning("сериал %d: озвучки не подготовлены: %s", series_id, exc)

    async def _dispatch(self, series_id: int) -> None:
        series = await self.request("catalog.series.get", {"series_id": series_id})
        if not is_video_source(series["source_type"]) or series["source_type"] == "vk_video":
            return
        info = await self.request("sources.provider.info", {"url": series["url"]}, timeout=10)
        if not info.get("voices"):
            return
        parsed = await self.request("sources.parse", {"url": series["url"], "with_links": True},
                                    timeout=PARSE_TIMEOUT_S)
        sources = parsed.get("sources") or []
        if not sources:
            return
        settings = await self._settings()
        layout = plan_layout(sources, "manual", parsed.get("selected_source"), list(settings.priority),
                             settings.rules)
        dubs = [voice for voice in layout["voices"] if voice["role"] == "dub"]
        reference = layout["reference"]
        if not dubs or reference is None:
            await self._forget_all(series_id)
            return
        episodes = await self._reference_episodes(series_id, reference)
        self.log.info("сериал %d: озвучки — референс %s, дополнительных %d, серий плана на сайте %d",
                      series_id, reference["studio"], len(dubs), len(episodes))
        before = await self.repo.for_series(series_id)
        keys = self._adopted_keys(dubs, before, settings.rules)
        planned: set[tuple[int, int, str]] = set()
        touched: set[tuple[int, int, str]] = set()
        for season, episode, site_episode in episodes:
            for voice in dubs:
                chain = [{"player": source["player"], "studio": source["studio"],
                          "url": source["links"][str(site_episode)]}
                         for source in download_chain(voice, site_episode)
                         if str(site_episode) in source.get("links", {})]
                planned.add((season, episode, keys[voice["key"]]))
                if await self.repo.upsert(series_id, season, episode, site_episode,
                                          keys[voice["key"]], voice["label"], chain):
                    touched.add((season, episode, keys[voice["key"]]))
        for gone in await self.repo.drop_unplanned(series_id, planned):
            self.log.info("сериал %d: озвучка %s серии %d вышла из плана", series_id,
                          gone["voice_label"], gone["episode"])
            self._changed(gone, E.REMOVED)
        after = await self.repo.for_series(series_id)
        self._announce_difference(before, [row for row in after
                                           if (row["season"], row["episode"], row["voice_key"]) in touched])
        await self._recheck_ready(series_id)
        await self._admit()
        for row in after:
            if row["status"] == S.FAILED:
                self.log.warning("сериал %d, s%02de%02d: озвучка %s недоступна: %s", series_id,
                                 row["season"], row["episode"], row["voice_label"], row["error"])
        self._kick(series_id)

    @staticmethod
    def _adopted_keys(dubs: list[dict], rows: list[dict], rules: Equivalence) -> dict[str, str]:
        """A voice keeps the key its rows were recorded under when changed rules gave it another key."""
        recorded = {row["voice_key"] for row in rows}
        taken = {voice["key"] for voice in dubs if voice["key"] in recorded}
        keys: dict[str, str] = {}
        for voice in dubs:
            keys[voice["key"]] = voice["key"]
            if voice["key"] in recorded:
                continue
            same = [row for row in rows if same_voice(row["voice_label"], voice["label"], rules)
                    and row["voice_key"] not in taken]
            if same:
                keys[voice["key"]] = min(same, key=lambda row: row["id"])["voice_key"]
                taken.add(keys[voice["key"]])
        return keys

    async def _reference_episodes(self, series_id: int, reference: dict) -> list[tuple[int, int, int]]:
        """(season, episode, site episode) of every planned reference episode that the site lists."""
        by_link = {link: int(number) for number, link in (reference.get("links") or {}).items()}
        reply = await self.request("downloads.episodes.get", {"series_id": series_id}, timeout=30)
        found, lost = [], []
        for item in reply["items"]:
            site_episode = by_link.get(item["source_url"])
            if site_episode is None:
                lost.append(item["episode_start"])
            else:
                found.append((item["season"], item["episode_start"], site_episode))
        if lost:
            self.log.warning("сериал %d: серии плана не найдены в каталоге референса, озвучки не "
                             "скачиваются: %s", series_id, ", ".join(map(str, lost)))
        return found

    async def _forget_all(self, series_id: int) -> None:
        for gone in await self.repo.drop_unplanned(series_id, set()):
            self._changed(gone, E.REMOVED)

    async def _recheck_ready(self, series_id: int) -> None:
        """A ready dub whose file vanished from the temporary directory is downloaded again."""
        for row in await self.repo.for_series(series_id):
            if row["status"] == S.READY and not await self._io(os.path.exists, row["temp_path"]):
                await self.repo.mark_pending(row["id"], reason("file_vanished"))
                self._changed({**row, "status": S.PENDING}, E.STALE, reason("file_vanished"))


    async def _temp_cap(self) -> int:
        return (await self._settings()).max_temp_episodes

    async def _admit(self) -> None:
        """Start the waiting downloads, as many episodes at a time as the setting allows (0: no limit).

        An episode that is already under way takes all its voice-overs; the others wait, planned,
        until an episode settles and frees its place."""
        async with self._admit_lock:
            waiting = await self.repo.pending()
            if not waiting:
                return
            cap = await self._temp_cap()
            root = await self._temp_root()
            active = await self.repo.active_episodes() if cap else set()
            for row in sorted(waiting, key=lambda r: (r["series_id"], r["season"], r["episode"], r["id"])):
                episode = (row["series_id"], row["season"], row["episode"])
                if cap and episode not in active:
                    if len(active) >= cap:
                        continue
                    active.add(episode)
                await self._start(row, root)

    async def _start(self, row: dict, root: str | None = None) -> None:
        chain = row["chain"]
        if row["attempt"] >= len(chain):
            await self._fail(row, row["error"] or reason("no_source"))
            return
        path = temp_path(root or await self._temp_root(), row["series_id"], row["season"],
                         row["episode"], row["voice_key"])
        if not await self.repo.claim_download(row["id"], path):
            return
        self._fetching.discard(row["id"])
        self.send_command("downloads.fetch", {
            "key": f"dub:{row['id']}", "url": chain[row["attempt"]]["url"], "path": path,
            "max_height": DUB_MAX_HEIGHT, "series_id": row["series_id"]})
        self._changed(row, E.DOWNLOADING)

    async def on_fetch_started(self, env: Envelope) -> None:
        """The download left the queue of downloads and began: only now it is a download."""
        key = str(env.payload.get("key") or "")
        if not key.startswith("dub:"):
            return
        row = await self.repo.get(int(key[4:]))
        if row is not None and row["status"] == S.DOWNLOADING:
            self._fetching.add(row["id"])
            self._changed(row, E.DOWNLOADING)

    async def on_fetch_finished(self, env: Envelope) -> None:
        payload = env.payload
        key = str(payload.get("key") or "")
        if not key.startswith("dub:"):
            return
        self._fetching.discard(int(key[4:]))
        row = await self.repo.get(int(key[4:]))
        if row is None or row["status"] != S.DOWNLOADING:
            return  # a late or repeated ending of a download that this voice-over no longer waits for
        if payload.get("ok"):
            await self.repo.mark_ready(row["id"])
            self.log.info("сериал %d, s%02de%02d: озвучка %s скачана (%s)", row["series_id"],
                          row["season"], row["episode"], row["voice_label"],
                          row["chain"][row["attempt"]]["player"])
            self._changed(row, E.READY)
            self._kick(row["series_id"])
            return
        error = str(payload.get("error") or reason("download_failed"))
        source = row["chain"][row["attempt"]]
        self.log.info("сериал %d, s%02de%02d: озвучка %s не скачана из %s: %s", row["series_id"],
                      row["season"], row["episode"], row["voice_label"], source["player"], error)
        if row["attempt"] + 1 < len(row["chain"]):
            await self.repo.advance(row["id"], error)
            await self._start(await self.repo.get(row["id"]))
        else:
            await self._fail(row, error)

    async def _fail(self, row: dict, error: str) -> None:
        await self.repo.mark_failed(row["id"], error)
        self.log.warning("сериал %d, s%02de%02d: озвучка %s не скачана ни из одного источника: %s",
                         row["series_id"], row["season"], row["episode"], row["voice_label"], error)
        self._changed(row, E.FAILED, error)
        self._kick(row["series_id"])

    def _next_rev(self) -> int:
        """Revisions grow within the process and across restarts: the interface keeps the newest."""
        self._rev = max(self._rev + 1, time.time_ns() // 1000)
        return self._rev

    def _changed(self, row: dict, status: E, detail: str | None = None, job_id: str | None = None) -> None:
        state = public_state(status, self._job_status.get(job_id or row.get("job_id")),
                             row.get("id") in self._fetching)
        self.publish_event("voices.dub.changed", DubEvent(
            series_id=row["series_id"], season=row["season"], episode=row["episode"],
            voice_key=row["voice_key"], voice_label=row["voice_label"], status=status, state=state,
            hint=public_hint(status, state), detail=parse_reason(detail),
            rev=self._next_rev()).model_dump(mode="json"))

    def _announce_difference(self, before: list[dict], after: list[dict]) -> None:
        """Rows that the planner created or took over are news for the interface like any other change."""
        known = {(row["season"], row["episode"], row["voice_key"]): (row["status"], row["outcome"] or row["error"])
                 for row in before}
        for row in after:
            seen = (row["status"], row["outcome"] or row["error"])
            if known.get((row["season"], row["episode"], row["voice_key"])) != seen:
                self._changed(row, row["status"], seen[1])

    def _record(self, row: dict) -> DubRecord:
        state = public_state(row["status"], self._job_status.get(row["job_id"]), row["id"] in self._fetching)
        return DubRecord(**{field: row[field] for field in DubRecord.model_fields
                            if field in row and field not in ("state", "hint", "detail")},
                         state=state, hint=public_hint(row["status"], state),
                         detail=parse_reason(row["outcome"] or row["error"]))

    async def on_clients(self, env: Envelope) -> None:
        """A new interface has no history: it gets every voice-over at once, stamped before the read."""
        if self.repo is None or not env.payload.get("count"):
            return
        rev = self._next_rev()
        items = [SnapshotItem(series_id=row["series_id"], **self._record(row).model_dump(
            include=set(DubItem.model_fields))) for row in await self.repo.all_rows()]
        self.publish_event("voices.dubs.snapshot", DubsSnapshot(rev=rev, items=items).model_dump(mode="json"))

    async def on_conform_picture(self, env: Envelope) -> None:
        """A job of the service went from waiting to running (or vanished): its voice-overs follow."""
        states = {task["id"]: task["status"] for task in env.payload.get("tasks") or []}
        changed = {job for job in states.keys() | self._job_status.keys()
                   if states.get(job) != self._job_status.get(job)}
        self._job_status = states
        if self.repo is None:
            return
        for job_id in changed:
            for row in await self.repo.by_job(job_id):
                if row["status"] == S.ALIGNING:
                    self._changed(row, E.ALIGNING)


    async def on_downloaded(self, env: Envelope) -> None:
        self._kick(int(env.payload["series_id"]))

    async def on_relocated(self, env: Envelope) -> None:
        if env.payload.get("success"):
            self._kick(int(env.payload["series_id"]))

    async def on_daemon_up(self, env: Envelope) -> None:
        await self._recover_submissions()
        await self._sweep_cleanup()
        for job_id in await self.repo.aligning_jobs():
            await self._reconcile(job_id)
        for series_id in await self.repo.series_waiting():
            self._kick(series_id)

    def _kick(self, series_id: int) -> None:
        """Look at the series once more; kicks that arrive while it is scheduled merge into one."""
        if series_id in self._kicked:
            return
        self._kicked.add(series_id)
        task = asyncio.create_task(self._advance_locked(series_id), name=f"voices:advance:{series_id}")
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _advance_locked(self, series_id: int) -> None:
        async with self._locks.setdefault(series_id, asyncio.Lock()):
            self._kicked.discard(series_id)
            try:
                await self._advance(series_id)
            except (BusRequestError, asyncio.TimeoutError, OSError) as exc:
                self.log.warning("сериал %d: выравнивание озвучек отложено: %s", series_id, exc)
            except Exception:  # noqa: BLE001 - one series must not stop the others
                self.log.exception("сериал %d: сбой оркестрации озвучек", series_id)

    async def _advance(self, series_id: int) -> None:
        rows = await self.repo.for_series(series_id)
        if not rows:
            return
        series = await self.request("catalog.series.get", {"series_id": series_id})
        await self._recover_submissions()
        in_plan, references = await self._reference_state(series)
        root = await self._temp_root()
        episodes = sorted({(row["season"], row["episode"]) for row in rows})
        restart = False
        for season, episode in episodes:
            group = await self.repo.for_episode(series_id, season, episode)
            for row in group:
                if row["move_from"]:
                    await self._finish_move(row)
            group = await self.repo.for_episode(series_id, season, episode)
            current = references.get((season, episode))
            if current is None:
                if (season, episode) not in in_plan:
                    await self._drop_orphans(series_id, season, episode, group, root)
                continue
            reference, uid = current
            if await self._follow(series_id, season, episode, group, reference, uid):
                restart = True
                continue
            for row in group:
                if row["status"] == S.ALIGNED:
                    await self._place(row, reference)
            group = await self.repo.for_episode(series_id, season, episode)
            ready = [row for row in group if row["status"] == S.READY]
            if ready and not any(row["status"] in WORKING for row in group):
                await self._submit(series, season, episode, reference, uid, ready)
            await self._tidy(series_id, season, episode, root)
        await self._admit()
        await self._sweep_cleanup()
        if restart:
            self.send_command("voices.dispatch", {"series_id": series_id})
        await self._sweep_scratch(series_id, {os.path.dirname(path) for path, _ in references.values()})
        if all(row["status"] in FINAL for row in await self.repo.for_series(series_id)):
            await self._io(self._remove_if_empty, os.path.join(root, str(series_id)))

    async def _sweep_scratch(self, series_id: int, folders: set[str]) -> None:
        """The service's scratch directory is made next to the reference when a job starts: while
        one runs it must stay, and the series lock keeps a new job from starting meanwhile."""
        if any(row["status"] == S.ALIGNING for row in await self.repo.for_series(series_id)):
            return
        for folder in folders:
            await self._io(self._remove_if_empty, scratch_of(folder))

    @staticmethod
    def _remove_if_empty(path: str) -> None:
        try:
            os.rmdir(path)
        except OSError:
            pass

    async def _reference_state(self, series: dict) -> tuple[set, dict[tuple[int, int], tuple[str, str]]]:
        """The planned episodes, and (file, media item) of the reference that is on disk, by episode.

        When a slot holds a superseded file and its winner, the winner is the reference."""
        reply = await self.request("downloads.episodes.get", {"series_id": series["id"]}, timeout=30)
        in_plan, found = set(), {}
        for item in reply["items"]:
            slot = (item["season"], item["episode_start"])
            in_plan.add(slot)
            if not item.get("final_filename"):
                continue
            path = os.path.join(series["save_path"], item["final_filename"])
            if not await self._io(os.path.isfile, path):
                continue
            if slot not in found or not item.get("superseded_by"):
                found[slot] = (path, item["unique_id"])
        return in_plan, found

    async def _follow(self, series_id: int, season: int, episode: int, group: list[dict],
                      reference: str, uid: str) -> bool:
        """Keep the tracks of an episode in step with its reference; True when they went stale."""
        stale = [row for row in group if row["ref_uid"] and row["ref_uid"] != uid]
        if stale:
            for job_id in {row["job_id"] for row in stale if row["status"] == S.ALIGNING and row["job_id"]}:
                await self._cancel_job(job_id)
            for row in group:
                if row["ref_uid"] and row["ref_uid"] != uid:
                    await self._discard_files(row)
                    await self.repo.reset(row["id"], reason("reference_replaced"))
                    self._changed(row, E.STALE, reason("reference_replaced"))
            self.log.info("сериал %d, s%02de%02d: референс заменён, дорожки устарели и удалены: %d",
                          series_id, season, episode, len(stale))
            return True
        for row in group:
            if row["status"] == S.PLACED:
                await self._follow_name(row, reference)
        return False

    async def _follow_name(self, row: dict, reference: str) -> None:
        old = row["output_path"]
        if not old:
            return
        expected = self._output.destination(reference, row["voice_label"], old, await self._taken_paths(row))
        present = await self._io(self._output.exists, old)
        if expected == old and present:
            if row["ref_path"] != reference:
                await self.repo.set_output(row["id"], old, reference)
            return
        if present:
            await self.repo.begin_move(row["id"], expected, old)
            await self._finish_move(await self.repo.get(row["id"]), reference)
        elif expected == old or not await self._io(self._output.exists, expected):
            await self.repo.mark_rejected(row["id"], reason("track_removed"))
            self.log.info("сериал %d, s%02de%02d: дорожка %s удалена пользователем, заново не создаётся",
                          row["series_id"], row["season"], row["episode"], row["voice_label"])
            self._changed(row, E.REJECTED, reason("track_removed"))
            return
        else:
            await self.repo.set_output(row["id"], expected, reference)
        self.log.info("сериал %d, s%02de%02d: дорожка %s следует за видео: %s", row["series_id"],
                      row["season"], row["episode"], row["voice_label"], expected)
        self._changed(row, E.MOVED)

    async def _drop_orphans(self, series_id: int, season: int, episode: int, group: list[dict],
                            root: str) -> None:
        """An episode left the plan: with its video gone, its set of tracks goes too."""
        if any(row["status"] == S.DOWNLOADING for row in group):
            return
        for row in group:
            if row["ref_path"] and await self._io(os.path.isfile, row["ref_path"]):
                return
        folders = self._folders_of(group, episode_dir(root, series_id, season, episode))
        for row in group:
            await self._discard_files(row)
            await self.repo.delete(row["id"])
            self._changed(row, E.REMOVED)
        for folder in folders:
            await self._discard(folder, kind=TREE)
        self.log.info("сериал %d, s%02de%02d: серии нет в плане и видео нет — дорожки удалены",
                      series_id, season, episode)

    async def _discard_files(self, row: dict) -> None:
        for path in (row["output_path"], row["move_from"]):
            if path:
                await self._discard(path, kind=ARTIFACT)
        for path in (row["aligned_path"], row["temp_path"]):
            if path:
                await self._discard(path)

    def _unlink(self, path: str, kind: str) -> None:
        if kind == TREE:
            shutil.rmtree(path)
        elif kind == ARTIFACT:
            self._output.remove(path)
        else:
            os.remove(path)

    async def _discard(self, path: str, *, kind: str = PATH) -> bool:
        """Remove a file, an artifact or a tree. What cannot be removed goes to the cleanup journal and is
        tried again: a record of the file is never dropped before the file is gone."""
        try:
            await self._io(self._unlink, path, kind, timeout=REMOVE_TIMEOUT_S)
        except FileNotFoundError:
            return True
        except OSError as exc:
            await self.repo.journal_cleanup(path, kind, str(exc))
            self.log.error("не удалось удалить %s, повторим: %s", path, exc)
            return False
        return True

    async def _sweep_cleanup(self) -> None:
        for entry in await self.repo.cleanup_list():
            if entry["kind"] == JOB:
                try:
                    reply = await self.request("conform.cancel", {"job_id": entry["path"]}, timeout=30)
                except (BusRequestError, asyncio.TimeoutError):
                    continue
                if reply.get("done"):
                    await self.repo.cleanup_done(entry["path"])
                continue
            try:
                await self._io(self._unlink, entry["path"], entry["kind"], timeout=REMOVE_TIMEOUT_S)
            except FileNotFoundError:
                pass
            except OSError as exc:
                await self.repo.journal_cleanup(entry["path"], entry["kind"], str(exc))
                continue
            await self.repo.cleanup_done(entry["path"])
            self.log.info("отложенная очистка выполнена: %s", entry["path"])

    @staticmethod
    def _folders_of(group: list[dict], fallback: str) -> list[str]:
        """Episode folders of voice-overs as they were made, not as the settings say now."""
        found = {os.path.dirname(row["temp_path"]) for row in group if row["temp_path"]}
        found |= {dub_folder_of_track(row["aligned_path"]) for row in group if row["aligned_path"]}
        return sorted(found) or [fallback]

    async def _finish_move(self, row: dict, reference: str | None = None) -> None:
        """Complete a track move whose start is written down: safe to repeat after any interruption."""
        source, destination = row["move_from"], row["output_path"]
        if not await self._io(self._output.settle, source, destination, timeout=MOVE_TIMEOUT_S):
            await self.repo.mark_failed(row["id"], reason("track_vanished_on_move"))
            self._changed(row, E.FAILED, reason("track_vanished_on_move"))
            return
        ref_path = reference or row["ref_path"]
        if row["status"] == S.ALIGNED:
            await self.repo.mark_placed(row["id"], destination, ref_path)
        else:
            await self.repo.set_output(row["id"], destination, ref_path)

    async def _submit(self, series: dict, season: int, episode: int, reference: str, uid: str,
                      ready: list[dict]) -> None:
        label = f"{series['name']} s{season:02d}e{episode:02d}"
        ready = await self._with_files(ready, label)
        if not ready:
            return
        out_dir, cache_dir = job_folders(os.path.dirname(ready[0]["temp_path"]))
        key = uuid.uuid4().hex[:12]
        await self.repo.mark_submitting([row["id"] for row in ready], key, uid, reference)
        try:
            reply = await self.request("conform.submit", {
                "ref": reference, "dubs": [row["temp_path"] for row in ready],
                "out_dir": out_dir, "cache_dir": cache_dir,
                "label": label, "key": key}, timeout=SUBMIT_TIMEOUT_S)
        except (BusRequestError, asyncio.TimeoutError) as exc:
            self.log.warning("%s: ответа на постановку нет, спросим сервис, есть ли задача: %s", label, exc)
            self._retry_later(series["id"])
            return
        if reply.get("unavailable"):
            await self.repo.release_submission(key)
            self.log.info("%s: озвучки ждут сервис выравнивания: %s", label, reply["unavailable"])
            if reply.get("retry"):
                self._retry_later(series["id"])
            return
        self._retries.pop(series["id"], None)
        if reply.get("rejected"):
            for row in ready:
                await self.repo.mark_rejected(row["id"], reply["rejected"])
                self._changed(row, E.REJECTED, reply["rejected"])
            self.log.warning("%s: задача выравнивания отклонена: %s", label, reply["rejected"])
            return
        await self._attach(key, reply["job_id"])

    async def _attach(self, key: str, job_id: str) -> None:
        """The service has the job of this key: its voice-overs get the number, an early ending is applied."""
        await self.repo.attach_job(key, job_id)
        for row in await self.repo.by_key(key):
            if row["job_id"] == job_id:
                self._changed(row, E.ALIGNING, job_id=job_id)
        early = self._unclaimed.pop(job_id, None)
        if early is not None:
            await self._apply_job(early)

    async def _recover_submissions(self) -> None:
        """Jobs sent without an answer: the service says whether it has them; if not, they are sent anew."""
        for key in await self.repo.unsent_submissions():
            try:
                reply = await self.request("conform.job.find", {"key": key}, timeout=30)
            except (BusRequestError, asyncio.TimeoutError):
                continue
            if not reply.get("reachable"):
                continue
            rows = await self.repo.by_key(key)
            if reply.get("job_id"):
                self.log.info("задача %s принята сервисом, ответ потерян: номер восстановлен", reply["job_id"])
                await self._attach(key, reply["job_id"])
                continue
            await self.repo.release_submission(key)
            self.log.info("задачу %s сервис не получил, озвучки отправятся заново", key)
            for series_id in {row["series_id"] for row in rows}:
                self._kick(series_id)

    async def _cancel_job(self, job_id: str) -> None:
        """Stop a job nobody needs; when the service cannot be reached the job stays on the cleanup list."""
        try:
            reply = await self.request("conform.cancel", {"job_id": job_id}, timeout=30)
        except (BusRequestError, asyncio.TimeoutError):
            reply = {"done": False}
        if not reply.get("done"):
            await self.repo.journal_cleanup(job_id, JOB, "сервис недоступен")

    async def _with_files(self, ready: list[dict], label: str) -> list[dict]:
        """The ready voice-overs whose files are really on disk; the others are downloaded again."""
        present = []
        for row in ready:
            if row["temp_path"] and await self._io(os.path.isfile, row["temp_path"]):
                present.append(row)
                continue
            self.log.warning("%s: файла озвучки %s нет на месте (%s), скачивается заново", label,
                             row["voice_label"], row["temp_path"])
            await self.repo.mark_pending(row["id"], reason("file_vanished"))
            self._changed({**row, "status": S.PENDING}, E.STALE, reason("file_vanished"))
        if len(present) != len(ready):
            self.send_command("voices.dispatch", {"series_id": ready[0]["series_id"]})
        return present

    def _retry_later(self, series_id: int) -> None:
        """A transient failure with the service up: ask again after a pause, a few times only."""
        if series_id in self._retry_pending:
            return
        attempt = self._retries.get(series_id, 0) + 1
        if attempt > SUBMIT_RETRIES:
            self._retries.pop(series_id, None)
            self.log.warning("сериал %d: сервис выравнивания не принял серии за %d попыток, "
                             "дальше — по следующему событию", series_id, SUBMIT_RETRIES)
            return
        self._retries[series_id] = attempt
        self._retry_pending.add(series_id)

        async def later() -> None:
            await asyncio.sleep(SUBMIT_RETRY_S)
            self._retry_pending.discard(series_id)
            self._kick(series_id)

        task = asyncio.create_task(later(), name=f"voices:retry:{series_id}")
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def on_job_finished(self, env: Envelope) -> None:
        job = AlignmentJob.model_validate(env.payload)
        if not await self._apply_job(job):
            # The service can end a job before its number is written to the voice-overs: keep the
            # ending until they claim it.
            self._unclaimed[job.job_id] = job
            while len(self._unclaimed) > UNCLAIMED_JOBS:
                self._unclaimed.popitem(last=False)

    async def _reconcile(self, job_id: str) -> None:
        """A job sent before a restart: learn how it ended, or send its voice-overs again."""
        try:
            job = AlignmentJob.model_validate(await self.request("conform.job.get", {"job_id": job_id}, timeout=30))
        except (BusRequestError, asyncio.TimeoutError) as exc:
            self.log.info("задача выравнивания %s: итог пока неизвестен (%s)", job_id, exc)
            return
        if job.status is JobStatus.GONE:
            rows = [row for row in await self.repo.by_job(job_id) if row["status"] == S.ALIGNING]
            await self.repo.release_to_ready([row["id"] for row in rows])
            for series_id in {row["series_id"] for row in rows}:
                self._kick(series_id)
        elif job.status in ENDED:
            await self._apply_job(job)

    async def _apply_job(self, job: AlignmentJob) -> bool:
        """Take the end of a job into the voice-overs that wait for it; False when none does."""
        rows = [row for row in await self.repo.by_job(job.job_id) if row["status"] == S.ALIGNING]
        if not rows:
            return False
        for row in rows:
            if job.status is not JobStatus.DONE:
                error = job_failure(job)
                await self.repo.mark_failed(row["id"], error)
                self.log.warning("сериал %d, s%02de%02d: озвучка %s не выровнена: %s", row["series_id"],
                                 row["season"], row["episode"], row["voice_label"], error)
                self._changed(row, E.FAILED, error)
                continue
            result = result_for(job, row["temp_path"])
            track = track_of(row["temp_path"], result)
            verdict = refusal(result)
            if verdict is None and not await self._io(os.path.isfile, track):
                verdict = reason("output_missing")
            if verdict is None:
                await self.repo.mark_aligned(row["id"], track)
                self._changed(row, E.ALIGNED)
            else:
                await self.repo.mark_rejected(row["id"], verdict)
                self.log.warning("сериал %d, s%02de%02d: озвучка %s отклонена: %s", row["series_id"],
                                 row["season"], row["episode"], row["voice_label"], verdict)
                self._changed(row, E.REJECTED, verdict)
        for series_id in {row["series_id"] for row in rows}:
            self._kick(series_id)
        return True

    async def _taken_paths(self, row: dict) -> set[str]:
        """Tracks that other voice-overs of the series already have next to their videos."""
        return {other["output_path"] for other in await self.repo.for_series(row["series_id"])
                if other["id"] != row["id"] and other["output_path"]}

    async def _place(self, row: dict, reference: str) -> None:
        source = row["aligned_path"]
        if not source or not await self._io(os.path.isfile, source):
            await self.repo.mark_failed(row["id"], reason("aligned_file_vanished"))
            self._changed(row, E.FAILED, reason("aligned_file_vanished"))
            return
        destination = self._output.destination(reference, row["voice_label"], source, await self._taken_paths(row))
        await self.repo.begin_move(row["id"], destination, source)
        await self._finish_move(await self.repo.get(row["id"]), reference)
        self.log.info("сериал %d, s%02de%02d: дорожка %s рядом с видео: %s", row["series_id"],
                      row["season"], row["episode"], row["voice_label"], destination)
        self._changed(row, E.PLACED)

    async def _tidy(self, series_id: int, season: int, episode: int, root: str) -> None:
        """The episode is settled: its temporary files are of no use any more."""
        group = await self.repo.for_episode(series_id, season, episode)
        if not group or any(row["status"] not in FINAL for row in group):
            return
        folders = self._folders_of(group, episode_dir(root, series_id, season, episode))
        if not (any(row["temp_path"] or row["aligned_path"] for row in group)
                or any([await self._io(os.path.isdir, folder) for folder in folders])):
            return
        for folder in folders:
            await self._discard(folder, kind=TREE)
        await self.repo.forget_temp(series_id, season, episode)
        self.publish_event("voices.episode.settled",
                           {"series_id": series_id, "season": season, "episode": episode})


    async def on_series_deleted(self, env: Envelope) -> None:
        series_id = env.payload["series_id"]
        self.send_command("downloads.fetch.cancel", {"series_id": series_id})
        for job_id in {row["job_id"] for row in await self.repo.for_series(series_id)
                       if row["status"] == S.ALIGNING and row["job_id"]}:
            await self._cancel_job(job_id)
        rows = await self.repo.for_series(series_id)
        folders = {os.path.dirname(folder) for folder in self._folders_of(rows, "")} - {""}
        await self.repo.delete_for_series(series_id)
        for row in rows:
            self._changed(row, E.REMOVED)
        for folder in folders or {os.path.join(await self._temp_root(), str(series_id))}:
            await self._discard(folder, kind=TREE)
        await self._admit()
        self.publish_event("series.purged", {"series_id": series_id, "owner": self.name})
