"""Module voices: the voice-overs of a series (mission voice-alignment, stages 4 and 6).

Owns voice_dubs: one row per (series, episode, voice) — the additional voice-overs that are
downloaded next to the reference video and later aligned to it.

Settings: voices.source_priority (JSON list of player kinds), voices.groups and voices.player_groups
(JSON objects {group: [names]}: a voice-over, or a player, called by one of the names belongs to the group),
voices.max_temp_episodes. They are read together (settings.py): a wrong value is logged once and its default
is used.

Query:
  voices.layout {sources: [{url, player, studio, kind, episodes, links?}], mode?, reference?}
      -> {mode, reference, voices: [{key, label, role, source, fallbacks}], skipped, priority}
      The source priority is the setting `voices.source_priority` (JSON list of player kinds);
      layout.DEFAULT_PRIORITY when unset.
  voices.settings.get {} -> VoiceSettingsView: the groups of voice-overs and the order of sources as they apply, the
      order to return to and the fields whose stored value was refused
  voices.dubs.get {series_id} -> {items: [{season, episode, voice_key, voice_label, status,
      attempt, error, temp_path, job_id, aligned_path, output_path, outcome, ref_uid, ref_path,
      detail, state, hint}]}  (detail = {code, params} of the outcome or error, see reasons.py; the
      fields `error` and `outcome` hold the stored reason as it is)
Command:
  voices.settings.set VoiceSettingsDraft -> VoiceSettingsSaved: groups and order are checked (editor.check) and written
      in one step through settings.values.set_many, or refused with coded problems and nothing written; the next
      read of the settings uses the new values, no restart. Rows keep the key they were recorded under.
  voices.dispatch {series_id} — sent by scan after an explicit scan: read the catalog of the site,
      lay the voices out around the selected reference, record a voice_dubs row per planned
      reference episode and voice, and download what is not on disk yet into the temporary
      downloads folder of the work folder (named by the owner in the settings of the alignment server and asked of
      its bridge; not named: nothing is recorded or downloaded and the log says so). Before a job is sent the
      reference video is copied into the service's folder of the episode, so that the service reads and writes
      only inside the work folder; the copy goes with the other temporary files of the episode.
Commands out: downloads.fetch, downloads.fetch.cancel. Queries to conform: workspace (the folders of the service
            for a place, asked of the bridge: this module does not know its layout), submit (with a key that
            finds the job again; the reply is a SubmitReply: accepted, rejected, unavailable (not sent) or
            uncertain, anything that is not a clear reply counts as uncertain, and only "unavailable" releases the
            key), job.find, job.get, cancel (a job whose reference was replaced or whose series was deleted; what
            could not be cancelled is retried from the cleanup journal).

Events in:  downloads.fetch.started and .finished (our keys `dub:<id>`), conform.queue.changed (waiting or running
            jobs of the service), series.downloaded.changed,
            downloads.item.renamed, library.relocation.finished, conform.job.finished, conform.job.removed,
            conform.daemon.up, series.deleted.
Events out: voices.dub.changed {series_id, season, episode, voice_key, voice_label, status, state, hint, detail,
            epoch, rev} — epoch is the number of this start of the module, kept in the database and raised at every
            start; rev grows with every event of a run: the interface keeps the newest (epoch, rev) of two it
            receives out of order, and no clock takes part;
            every write to a row is announced, including the creation of a planned row and its removal;
            voices.dubs.snapshot {epoch, rev, items: [{series_id, season, episode, voice_key, voice_label, status,
            state, hint, detail}]} — every row, published when an interface connects (gateway.sse.clients);
            state is what the user sees (states.py: planned, download_queued, downloading, align_queued, running,
            done, error), hint names the sentence that tells apart the causes of one state, status the
            step behind it; detail is the reason of a failure, a rejection or a stale track as
            {code, params} (code `external`: the words of another system in params.text), else null;
            a voice-over that was sent to the service and has no job number yet says so in its hint (`awaiting_answer`);
            status is one of
            ready, aligning, aligned, placed, rejected, failed, moved (the track followed its video),
            stale (its reference was replaced) and removed (the episode and its video are gone);
            series.purged,
            voices.settings.changed VoiceSettingsView — the settings as they apply after a save, so that every open
            settings screen shows them without asking back,
            voices.episode.settled {series_id, season, episode} — every voice-over of the episode is
            placed, rejected or failed and its temporary files (the downloaded voice-overs and the copy of the
            reference) are gone or on the cleanup journal; the folders the service wrote in stay with the records of
            its jobs.

Where each policy lives: the steps a row may take and the sets of steps (lifecycle.py, enforced by the repository),
what is known of the alignment service (alignment.py), the form in which a finished voice-over reaches the library
(outputs.py, injected as `output=`), the shape of every message (core/contracts).

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

Obligations (recovery.py). What the database says is unfinished and may be blocked by something that passes — a
job sent without an answer (`submission:<key>`), a job whose ending is not known (`job:<id>`), an entry of the
cleanup journal (`cleanup:<path>`), a pass over a series that the service or the storage stopped
(`series:<id>`) — has its own account of attempts: it is tried again after a pause that doubles up to a
ceiling, it is told to try at once by conform.daemon.up, and after SUBMIT_RETRIES failures the owner is told
once (an error in the log) while the attempts go on at the longest pause. Every such obligation is registered
when it is written down and again at the start of the module, so no outside event is needed to carry it on;
the number of a job found by its key starts the obligation to learn the job's end, and a pause that cannot be
computed is replaced by a fixed one: the planner never ends an obligation.

Removal and hand-over. What a row owns — its files, the job it waits for, a submission whose answer is not
known — is handed to the cleanup journal in the same transaction that lets the row go (reset, deletion of the
episode or of the series, end of the episode): a name is never without a record or without an owner, and a
journal entry never competes with the row that wrote it. The entries are then carried out: a file, an
artifact or a tree is removed with the ownership of its name and the removal is confirmed by the name being
free; a job is cancelled, a submission is found by its key and its job cancelled, and only a confirmed stop
ends the entry, after which the folder the job wrote in is cleaned. A name that a live voice-over holds again
is not removed by a deferred attempt. No clock takes part.

Every submission writes in folders of its own (`<out>/<key>`, `<cache>/<key>`), so a job that is not
confirmed stopped can not put its files into the result of a later job, whatever names they share.

A removal owns the name before it looks at who holds it, and waits for every area of ownership that overlaps its path, a folder above or below it included: the
folder of a job of a live voice-over and the folder of a job or a submission on the journal that is not confirmed
stopped. An entry is ended in the version that was read: a name handed over again meanwhile keeps its new entry.
A submission is on its way until its outcome is handled (`_sending`): no look at the service ends its entry or
sends its voice-overs anew meanwhile, and a job accepted for voice-overs that are gone is cancelled at once.
An operation carries out the entries it has just handed over; older ones belong to the recovery.

Setting `voices.max_temp_episodes` (integer; 0 or unset: no limit) caps how many episodes may hold
temporary files at once: further voice-overs stay planned until an episode settles.

The folder a job wrote in (`<episode folder>/_aligned/<key>`: a track that was refused, the plots, the statistics) is
kept while the record of the job is in the service's history, because the interface shows the plots from there. The
record leaves the history when the owner deletes it, or when this module deletes it: the setting `conform.clear_finished`
asks it to at once when the episode is settled, and a replaced reference, a dropped episode and a deleted series always
do. The service then drops what it kept for the record, `conform.job.removed` tells this module, and the folder of the
job goes, with the empty folders around it. A record removed while the module was not listening is found by asking
the service (`conform.job.get`, status `gone`) when the service becomes reachable and when an episode is settled. The
series' own folders in the work folder go when every voice-over of the series is settled and the folders are empty.
"""
from __future__ import annotations

import asyncio
import errno
import json
import os
import re
import shutil
import uuid
from collections import OrderedDict, defaultdict

from core import BaseModule, BusRequestError, is_video_source
from pydantic import ValidationError

from core.contracts import (AlignmentJob, JobStatus, Layout, DubEvent, DubEventStatus as E, DubItem, DubRecord, DubsSnapshot, DubStatus as S,
                            SnapshotItem, SubmitOutcome, SubmitReply, VoiceSettingsDraft, VoiceSettingsSaved)
from core import storage
from core.storage import DEFAULT_TIMEOUT_S as IO_TIMEOUT_S, MOVE_TIMEOUT_S, REMOVE_TIMEOUT_S
from core.db import Database
from core.envelope import Envelope
from core.reasons import describe, parse as parse_reason, reason

from . import editor
from .identity import Equivalence, same_voice
from .alignment import ENDED, job_failure, refusal, result_for, track_of
from .layout import download_chain, plan_layout
from .lifecycle import FINAL, WORKING
from .outputs import Output, SidecarOutput
from .recovery import Recovery
from .repository import HANDED_OVER, VoicesRepository
from .settings import PREFIX, VoiceSettings, parse
from .states import public_hint, public_state

DUB_MAX_HEIGHT = 720
PARSE_TIMEOUT_S = 180
SUBMIT_TIMEOUT_S = 60
# A reference video is some gigabytes: this is only the fuse of its copy.
COPY_TIMEOUT_S = 1800.0
# The folder of an episode where each job keeps the copy of the reference it reads.
REF_DIR = "_ref"
# The first pause before an obligation is tried again, the number of failures after which the owner is told,
# and the longest pause; the pauses double in between.
SUBMIT_RETRY_S = 30.0
SUBMIT_RETRIES = 6
RETRY_CEILING_S = 600.0
# What the cleanup journal can hold: a plain file, a tree, an artifact of the output, a job of the service and
# a submission whose answer is not known (the key finds the job).
PATH, TREE, ARTIFACT, JOB, SUBMISSION = "path", "tree", "artifact", "job", "submission"
# The ceiling of the exponent of a pause: far above what the ceiling of the pause itself needs, far below an overflow.
MAX_DOUBLINGS = 30
UNCLAIMED_JOBS = 256


def _pause(failures: int) -> float:
    return min(SUBMIT_RETRY_S * 2 ** min(failures, MAX_DOUBLINGS), max(RETRY_CEILING_S, SUBMIT_RETRY_S))


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("._") or "dub"


def dub_file(series_id: int, season: int, episode: int, voice_key: str) -> str:
    """The name a voice-over is downloaded and sent to the service under: unique by series, episode and voice."""
    return f"{series_id}_s{season:02d}e{episode:02d}_{_safe(voice_key)}.mp4"


def reference_file(series_id: int, season: int, episode: int, reference: str) -> str:
    """The name the copy of a reference video is given for the service."""
    return f"{series_id}_s{season:02d}e{episode:02d}_ref{os.path.splitext(reference)[1]}"


def episode_dir(root: str, series_id: int, season: int, episode: int) -> str:
    return os.path.join(root, str(series_id), f"s{season:02d}e{episode:02d}")


def temp_path(root: str, series_id: int, season: int, episode: int, voice_key: str) -> str:
    return os.path.join(episode_dir(root, series_id, season, episode), dub_file(series_id, season, episode, voice_key))


class VoicesModule(BaseModule):
    name = "voices"

    def __init__(self, bus, db: Database | None = None, *, output: Output | None = None) -> None:
        self.repo = VoicesRepository(db) if db is not None else None
        self._output = output or SidecarOutput()
        self._locks: dict[int, asyncio.Lock] = {}
        self._kicked: set[int] = set()
        self._job_status: dict[str, str] = {}
        self._fetching: set[int] = set()
        self._admit_lock = asyncio.Lock()
        # The number of a job and its ending arrive in any order; each looks for the other under this lock.
        self._claim_lock = asyncio.Lock()
        self._unclaimed: OrderedDict[str, AlignmentJob] = OrderedDict()
        # Keys of submissions whose request is on its way: the service may still take such a job.
        self._sending: set[str] = set()
        self._watched: set[str] = set()
        self._epoch = 0
        self._epoch_lock = asyncio.Lock()
        self._rev = 0
        self._reported: set[str] = set()
        super().__init__(bus)
        self._recovery = Recovery(self.log, pause=_pause, report_after=lambda: SUBMIT_RETRIES)

    def register(self) -> None:
        self.handle("voices.layout", self.on_layout)
        self.handle("voices.dubs.get", self.on_dubs_get)
        self.handle("voices.settings.get", self.on_settings_get)
        self.handle("voices.settings.set", self.on_settings_set)
        self.handle("voices.dispatch", self.on_dispatch, concurrent=True)
        self.handle("downloads.fetch.started", self.on_fetch_started)
        self.handle("downloads.fetch.finished", self.on_fetch_finished)
        self.handle("series.downloaded.changed", self.on_downloaded)
        self.handle("downloads.item.renamed", self.on_downloaded)
        self.handle("library.relocation.finished", self.on_relocated)
        self.handle("conform.job.finished", self.on_job_finished)
        self.handle("conform.job.removed", self.on_job_removed)
        self.handle("conform.queue.changed", self.on_conform_picture)
        self.handle("conform.daemon.up", self.on_daemon_up)
        self.handle("series.deleted", self.on_series_deleted)
        self.handle("gateway.sse.clients", self.on_clients)

    async def on_start(self) -> None:
        if self.repo is None:
            return
        await self._generation()
        requeued = await self.repo.requeue_interrupted()
        pending = await self.repo.pending()
        if requeued or pending:
            self.log.info("озвучки: к докачке %d (прерванных %d)", len(pending), requeued)
        await self._admit()
        await self._sweep_cleanup()
        await self._resume_obligations()
        for series_id in await self.repo.series_with_open_work():
            self._kick(series_id)

    async def on_stop(self) -> None:
        await self._recovery.stop()

    def _spawn(self, work, name: str) -> None:
        task = asyncio.create_task(work, name=name)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _resume_obligations(self) -> None:
        """Whatever the database says is unfinished gets its own account of attempts, whatever event comes or not."""
        for key in await self.repo.unsent_submissions():
            self._await_submission(key, now=True)
        for job_id in await self.repo.aligning_jobs():
            self._await_job(job_id, now=True)
        for entry in await self.repo.cleanup_list():
            self._await_cleanup(entry["path"], now=True)

    def _await_submission(self, key: str, *, now: bool = False) -> None:
        self._recovery.want(f"submission:{key}", lambda: self._recover_one(key), now=now)

    def _await_job(self, job_id: str, *, now: bool = False) -> None:
        self._recovery.want(f"job:{job_id}", lambda: self._reconcile(job_id), now=now)

    def _await_cleanup(self, path: str, *, now: bool = False) -> None:
        self._recovery.want(f"cleanup:{path}", lambda: self._clear(path), now=now)

    def _await_series(self, series_id: int) -> None:
        self._recovery.want(f"series:{series_id}", lambda: self._retry_series(series_id))

    async def _retry_series(self, series_id: int) -> bool:
        return not await self._advance_locked(series_id)

    async def _io(self, fn, *args, timeout: float = IO_TIMEOUT_S, owns=()):
        """A call into the storage that waits for a limited time; `owns` is what a changing call changes."""
        return await storage.call(fn, *args, timeout=timeout, owns=owns)

    async def _workspace(self, *, dub_folder: str) -> dict:
        """The folders the service writes to for an episode's folder: only the service's bridge knows its layout."""
        return await self.request("conform.workspace", {"dub_folder": dub_folder}, timeout=10)

    def _when_free(self, path: str, series_id: int) -> None:
        """A step that needs a path an unfinished storage call still owns waits for that call to end."""
        if path not in self._watched:
            self._watched.add(path)

            def free() -> None:
                self._watched.discard(path)
                self._kick(series_id)

            storage.when_free(path, free)

    async def _settings(self) -> VoiceSettings:
        """Every setting of the module in one read; a problem with a value is logged once, then the default is used."""
        reply = await self.request("settings.values.by_prefix", {"prefix": PREFIX}, timeout=10)
        settings = parse(reply.get("values") or {})
        for problem in sorted(set(settings.problems) - self._reported):
            self.log.warning("озвучки: настройка: %s", problem)
        self._reported = set(settings.problems)
        return settings

    async def _folders(self) -> dict:
        """The two folders inside the work folder, asked of the bridge: both empty while the owner has not named it."""
        return await self.request("conform.workspace", {}, timeout=10)

    async def _downloads_root(self) -> str:
        return (await self._folders())["downloads"]

    async def _conform_episode(self, series_id: int, season: int, episode: int) -> str:
        """Where the service works for this episode; empty while the work folder is not named."""
        root = (await self._folders())["conform"]
        return episode_dir(root, series_id, season, episode) if root else ""


    async def on_layout(self, env: Envelope) -> dict:
        payload = env.payload
        settings = await self._settings()
        return Layout.model_validate(plan_layout(
            payload["sources"], payload.get("mode") or "manual", payload.get("reference"),
            list(settings.priority), settings.rules)).model_dump(mode="json", exclude_unset=True)

    async def on_dubs_get(self, env: Envelope) -> dict:
        rows = await self.repo.for_series(int(env.payload["series_id"]))
        return {"items": [self._record(row).model_dump(mode="json") for row in rows]}

    async def on_settings_get(self, env: Envelope) -> dict:
        return editor.view(await self._settings()).model_dump(mode="json")

    async def on_settings_set(self, env: Envelope) -> dict:
        draft = VoiceSettingsDraft.model_validate(env.payload)
        problems = editor.check(draft, (await self._settings()).player_groups)
        if not problems:
            await self.request("settings.values.set_many", {"values": editor.stored(editor.clean(draft))}, timeout=10)
        view = editor.view(await self._settings())
        if not problems:
            self.log.info("озвучки: настройки сохранены")
            self.publish_event("voices.settings.changed", view.model_dump(mode="json"))
        return VoiceSettingsSaved(saved=not problems, problems=problems, settings=view).model_dump(mode="json")


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
        if not await self._downloads_root():
            self.log.warning("сериал %d: озвучки не начаты: рабочая папка не задана в настройках сервера выравнивания", series_id)
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
            await self._announce(gone, E.REMOVED)
        await self._work_off()
        after = await self.repo.for_series(series_id)
        await self._announce_difference(before, [row for row in after
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
            await self._announce(gone, E.REMOVED)
        await self._work_off()

    async def _recheck_ready(self, series_id: int) -> None:
        """A ready dub whose file vanished from the temporary directory is downloaded again."""
        for row in await self.repo.for_series(series_id):
            if row["status"] == S.READY and not await self._io(os.path.exists, row["temp_path"]):
                await self.repo.mark_pending(row["id"], reason("file_vanished"))
                await self._announce({**row, "status": S.PENDING}, E.STALE)


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
            root = await self._downloads_root()
            if not root:
                return
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
        root = root or await self._downloads_root()
        if not root:
            return
        path = temp_path(root, row["series_id"], row["season"], row["episode"], row["voice_key"])
        if storage.busy(path):  # an earlier removal of this name has not ended: a file written now could be lost
            self._when_free(path, row["series_id"])
            return
        if not await self.repo.claim_download(row["id"], path):
            return
        self._fetching.discard(row["id"])
        # Announced before the command goes out: a quick download could end first and this event would repeat its end.
        await self._announce(row)
        self.send_command("downloads.fetch", {
            "key": f"dub:{row['id']}", "url": chain[row["attempt"]]["url"], "path": path,
            "max_height": DUB_MAX_HEIGHT, "series_id": row["series_id"]})

    async def on_fetch_started(self, env: Envelope) -> None:
        """The download left the queue of downloads and began: only now it is a download."""
        key = str(env.payload.get("key") or "")
        if not key.startswith("dub:"):
            return
        row = await self.repo.get(int(key[4:]))
        if row is not None and row["status"] == S.DOWNLOADING:
            self._fetching.add(row["id"])
            await self._announce(row)

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
            await self._announce(row)
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
        await self._announce(row)
        self._kick(row["series_id"])

    async def _generation(self) -> int:
        """The epoch of this run: kept in the database and raised at every start, so that the packets of a later
        run outrank those of an earlier one whatever the clock says; `rev` only orders packets within a run."""
        if not self._epoch:
            async with self._epoch_lock:
                if not self._epoch:
                    self._epoch = await self.repo.next_epoch()
        return self._epoch

    def _next_rev(self) -> int:
        self._rev += 1
        return self._rev

    async def _announce(self, row: dict, event: E | None = None) -> None:
        """Tell the interface what the row is now. The event is built from the stored row, never from what
        the caller meant to write; `event` names a kind of news (stale, moved, removed) the stored row must confirm."""
        epoch = await self._generation()
        rev = self._next_rev()  # taken before the read, like the snapshot's: a later event is never older
        current = await self.repo.get(row["id"])
        if current is None:
            if event is E.REMOVED:
                self._publish_event(row, E.REMOVED, None, epoch, rev)
            return
        status = E(current["status"])
        if (event is E.STALE and status == S.PENDING) or (event is E.MOVED and status == S.PLACED):
            status = event
        self._publish_event(current, status, current["outcome"] or current["error"], epoch, rev)

    def _publish_event(self, row: dict, status: E, detail: str | None, epoch: int, rev: int) -> None:
        state = public_state(status, self._job_status.get(row.get("job_id")), row.get("id") in self._fetching)
        self.publish_event("voices.dub.changed", DubEvent(
            series_id=row["series_id"], season=row["season"], episode=row["episode"],
            voice_key=row["voice_key"], voice_label=row["voice_label"], status=status, state=state,
            hint=public_hint(status, state, row.get("job_id")), detail=parse_reason(detail), epoch=epoch,
            rev=rev, job_id=row.get("job_id"),
            file=dub_file(row["series_id"], row["season"], row["episode"], row["voice_key"])).model_dump(mode="json"))

    async def _announce_difference(self, before: list[dict], after: list[dict]) -> None:
        """Rows that the planner created or took over are news for the interface like any other change."""
        known = {(row["season"], row["episode"], row["voice_key"]): (row["status"], row["outcome"] or row["error"])
                 for row in before}
        for row in after:
            seen = (row["status"], row["outcome"] or row["error"])
            if known.get((row["season"], row["episode"], row["voice_key"])) != seen:
                await self._announce(row)

    def _record(self, row: dict) -> DubRecord:
        state = public_state(row["status"], self._job_status.get(row["job_id"]), row["id"] in self._fetching)
        return DubRecord(**{field: row[field] for field in DubRecord.model_fields
                            if field in row and field not in ("state", "hint", "detail")},
                         state=state, hint=public_hint(row["status"], state, row["job_id"]),
                         detail=parse_reason(row["outcome"] or row["error"]))

    async def on_clients(self, env: Envelope) -> None:
        """A new interface has no history: it gets every voice-over at once, stamped before the read."""
        if self.repo is None or not env.payload.get("count"):
            return
        epoch = await self._generation()
        rev = self._next_rev()
        items = [SnapshotItem(series_id=row["series_id"], job_id=row.get("job_id"),
                              file=dub_file(row["series_id"], row["season"], row["episode"], row["voice_key"]),
                              **self._record(row).model_dump(include=set(DubItem.model_fields)))
                 for row in await self.repo.all_rows()]
        self.publish_event("voices.dubs.snapshot", DubsSnapshot(epoch=epoch, rev=rev, items=items).model_dump(mode="json"))

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
                    await self._announce(row)


    async def on_downloaded(self, env: Envelope) -> None:
        self._kick(int(env.payload["series_id"]))

    async def on_relocated(self, env: Envelope) -> None:
        if env.payload.get("success"):
            self._kick(int(env.payload["series_id"]))

    async def on_daemon_up(self, env: Envelope) -> None:
        """The service is back: whatever waited for it tries again now instead of at its next pause."""
        await self._resume_obligations()
        self._recovery.poke()
        await self._release_gone([row for row in await self.repo.all_rows() if row["status"] in FINAL])
        for series_id in await self.repo.series_with_open_work():
            self._kick(series_id)

    def _kick(self, series_id: int) -> None:
        """Look at the series once more; kicks that arrive while it is scheduled merge into one."""
        if series_id in self._kicked:
            return
        self._kicked.add(series_id)
        self._spawn(self._advance_or_await(series_id), f"voices:advance:{series_id}")

    async def _advance_or_await(self, series_id: int) -> None:
        if await self._advance_locked(series_id):
            self._await_series(series_id)

    async def _advance_locked(self, series_id: int) -> bool:
        """One pass over the series; True when something that may pass stopped it and a new pass is owed."""
        async with self._locks.setdefault(series_id, asyncio.Lock()):
            self._kicked.discard(series_id)
            try:
                return await self._advance(series_id)
            except (BusRequestError, asyncio.TimeoutError, OSError) as exc:
                self.log.warning("сериал %d: выравнивание озвучек отложено: %s", series_id, exc)
                return True
            except Exception:  # noqa: BLE001 - one series must not stop the others
                self.log.exception("сериал %d: сбой оркестрации озвучек", series_id)
                return False

    async def _advance(self, series_id: int) -> bool:
        rows = await self.repo.for_series(series_id)
        if not rows:
            return False
        series = await self.request("catalog.series.get", {"series_id": series_id})
        in_plan, references = await self._reference_state(series)
        root = await self._downloads_root()
        if not root:  # a folder made of nothing would be a path relative to wherever the service runs
            return False
        episodes = sorted({(row["season"], row["episode"]) for row in rows})
        restart = False
        owed = False
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
                owed = await self._submit(series, season, episode, reference, uid, ready) or owed
            await self._tidy(series_id, season, episode, root)
        await self._admit()
        await self._work_off()
        if restart:
            self.send_command("voices.dispatch", {"series_id": series_id})
        if all(row["status"] in FINAL for row in await self.repo.for_series(series_id)):
            for base in (root, (await self._folders())["conform"]):
                if base:
                    await self._io(self._remove_if_empty, os.path.join(base, str(series_id)))
        return owed

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
            for row in stale:
                # What the row owns, its job included, goes to the journal in the same transaction.
                await self.repo.reset(row["id"], reason("reference_replaced"))
            await self._work_off()
            for row in stale:
                await self._announce(row, E.STALE)
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
            if not await self._finish_move(await self.repo.get(row["id"]), reference):
                return
        elif expected == old or not await self._io(self._output.exists, expected):
            await self.repo.mark_rejected(row["id"], reason("track_removed"))
            self.log.info("сериал %d, s%02de%02d: дорожка %s удалена пользователем, заново не создаётся",
                          row["series_id"], row["season"], row["episode"], row["voice_label"])
            await self._announce(row)
            return
        else:
            await self.repo.set_output(row["id"], expected, reference)
        self.log.info("сериал %d, s%02de%02d: дорожка %s следует за видео: %s", row["series_id"],
                      row["season"], row["episode"], row["voice_label"], expected)
        await self._announce(row, E.MOVED)

    async def _drop_orphans(self, series_id: int, season: int, episode: int, group: list[dict],
                            root: str) -> None:
        """An episode left the plan: with its video gone, its set of tracks goes too."""
        if any(row["status"] == S.DOWNLOADING for row in group):
            return
        for row in group:
            if row["ref_path"] and await self._io(os.path.isfile, row["ref_path"]):
                return
        folders = await self._episode_folders(group, root, series_id, season, episode)
        await self.repo.drop_episode(series_id, season, episode, folders)
        await self._work_off()
        for row in group:
            await self._announce(row, E.REMOVED)
        self.log.info("сериал %d, s%02de%02d: серии нет в плане и видео нет — дорожки удалены",
                      series_id, season, episode)

    def _unlink(self, path: str, kind: str) -> None:
        """Remove what the kind says and confirm it: success is the name being free, not the absence of an error."""
        if kind == TREE:
            shutil.rmtree(path)
        elif kind == ARTIFACT:
            self._output.remove(path)
        else:
            os.remove(path)
        if self._output.exists(path) if kind == ARTIFACT else os.path.lexists(path):
            raise OSError(errno.EEXIST, "the name is still taken after its removal", path)

    async def _discard(self, path: str, *, kind: str = PATH) -> bool:
        """Remove a file, an artifact or a tree: written to the journal first, then removed. True means it is
        gone; anything else stays on the journal and is carried to the end by the recovery."""
        await self.repo.journal_cleanup(path, kind, "removal begun")
        if await self._clear(path):
            return True
        self._await_cleanup(path)
        return False

    @staticmethod
    def _held(path: str, rows: list[dict]) -> bool:
        """True when a voice-over holds the path or something inside it."""
        inside = path.rstrip(os.sep) + os.sep
        return any(held == path or held.startswith(inside)
                   for row in rows
                   for held in (row["output_path"], row["move_from"], row["aligned_path"], row["temp_path"])
                   if held)

    @staticmethod
    def _overlap(one: str, other: str) -> bool:
        """The same place, one inside the other, either way round."""
        one, other = one.rstrip(os.sep), other.rstrip(os.sep)
        return one == other or one.startswith(other + os.sep) or other.startswith(one + os.sep)

    def _contested(self, path: str, rows: list[dict], entries: list[dict]) -> bool:
        """True when something that may still write overlaps the path: a job of a live voice-over, or a job or a
        submission on the journal that is not confirmed stopped (a file, the folder it writes in, or a folder above it)."""
        return (any(self._overlap(path, row["out_dir"]) for row in rows if row["status"] == S.ALIGNING and row["out_dir"])
                or any(self._overlap(path, entry["scope"]) for entry in entries
                       if entry["kind"] in (JOB, SUBMISSION) and entry["scope"]))

    async def _sweep_cleanup(self) -> None:
        """A pass over the whole journal; what does not end here stays with the recovery."""
        for entry in await self.repo.cleanup_list():
            if not await self._clear(entry["path"]):
                self._await_cleanup(entry["path"])

    async def _work_off(self) -> None:
        """Carry out what has just been handed over; entries tried before belong to the recovery."""
        for entry in await self.repo.cleanup_list():
            if entry["error"] == HANDED_OVER and not await self._clear(entry["path"]):
                await self.repo.note_cleanup_error(entry["path"], "deferred", only_if=HANDED_OVER)
                self._await_cleanup(entry["path"])

    async def _finish(self, entry: dict) -> bool:
        """End the entry in the version that was read: when the name was handed over again meanwhile, the entry
        stays for the pass that belongs to the new hand-over."""
        return await self.repo.cleanup_done(entry["path"], entry["version"])

    async def _clear(self, path: str) -> bool:
        """One attempt to finish a journal entry; True when it is over. The name is owned before anything about it
        is looked at, so a voice-over that takes the name meanwhile waits for the end of the removal."""
        entry = next((entry for entry in await self.repo.cleanup_list() if entry["path"] == path), None)
        if entry is None:
            return True
        if entry["kind"] in (JOB, SUBMISSION):
            return await self._stop_job(entry)
        try:
            with storage.hold(path):
                return await self._remove(path)
        except storage.StorageBusy:
            return False  # another removal, or a call that timed out and has not ended, owns the name

    async def _remove(self, path: str) -> bool:
        entries = await self.repo.cleanup_list()
        entry = next((entry for entry in entries if entry["path"] == path), None)
        if entry is None:
            return True
        rows = await self.repo.all_rows()
        if self._held(path, rows):
            return await self._finish(entry)  # the name belongs to a live voice-over again: removing it destroys new work
        if self._contested(path, rows, entries):
            return False  # the job that may write there ends first
        try:
            await self._io(self._unlink, path, entry["kind"], timeout=REMOVE_TIMEOUT_S, owns=path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            await self.repo.note_cleanup_error(path, str(exc))
            self.log.error("не удалось удалить %s, повторим: %s", path, exc)
            return False
        if not await self._finish(entry):
            return False
        self.log.info("очистка выполнена: %s", path)
        return True

    async def _stop_job(self, entry: dict) -> bool:
        """A job nobody needs (named, or found by the key of a submission without an answer) is cancelled; only a
        confirmed stop ends the entry, and then the folder the job wrote in is cleaned."""
        job_id = entry["path"]
        if entry["kind"] == SUBMISSION:
            if entry["path"] in self._sending:
                return False  # the request is on its way: the service may take the job after any look at it
            try:
                found = await self.request("conform.job.find", {"key": entry["path"]}, timeout=30)
            except (BusRequestError, asyncio.TimeoutError):
                return False
            if not found.get("reachable"):
                return False
            job_id = found.get("job_id")
        if job_id:
            try:
                reply = await self.request("conform.cancel", {"job_id": job_id}, timeout=30)
            except (BusRequestError, asyncio.TimeoutError):
                return False
            if not reply.get("done"):
                return False
        scope = entry["scope"]
        if scope:
            await self.repo.journal_cleanup(scope, TREE, "job stopped")
        if not await self._finish(entry):
            return False
        self._recovery.poke("cleanup:")  # what waited for this job to stop may go on now
        if scope and not await self._clear(scope):
            self._await_cleanup(scope)
        elif scope:
            await self._io(self._prune, scope)
        return True

    async def _episode_folders(self, group: list[dict], root: str, series_id: int, season: int, episode: int) -> list[str]:
        """Everything of an episode that lies in the work folder: its voice-overs and the folder of the service."""
        folders = self._folders_of(group, episode_dir(root, series_id, season, episode))
        # Where the service worked is read from the rows (<episode folder>/_aligned/<key>), not from the settings now.
        made = {os.path.dirname(os.path.dirname(row["out_dir"])) for row in group if row["out_dir"]}
        return sorted({*folders, *made, await self._conform_episode(series_id, season, episode)} - {""})

    async def _settle_folders(self, group: list[dict], root: str, series_id: int, season: int,
                              episode: int) -> list[str]:
        """What of a settled episode is of no use: its voice-overs and the copy of the reference. The folders of the
        jobs stay with their records."""
        folders = self._folders_of(group, episode_dir(root, series_id, season, episode))
        service = {os.path.dirname(os.path.dirname(row["out_dir"])) for row in group if row["out_dir"]}
        service.add(await self._conform_episode(series_id, season, episode))
        return sorted({*folders, *(os.path.join(folder, REF_DIR) for folder in service if folder)})

    async def _clear_finished(self) -> bool:
        """The owner's choice: a job whose result is taken leaves the service's history at once."""
        try:
            reply = await self.request("conform.settings.get", {}, timeout=10)
        except (BusRequestError, asyncio.TimeoutError):
            return False
        return bool(reply.get("clear_finished"))

    async def on_job_removed(self, env: Envelope) -> None:
        """The record of a job left the service's history: the folder it wrote in is of no use any more."""
        if self.repo is None:
            return
        await self._release_outputs(await self.repo.by_job(env.payload["job_id"]))

    async def _release_outputs(self, rows: list[dict]) -> None:
        """Remove the folders the jobs of these rows wrote in, once nothing depends on them: a voice-over that still
        waits for its track, or still holds its files, keeps the folder until its episode is settled."""
        if any(row["status"] not in FINAL or row["aligned_path"] or row["temp_path"] for row in rows):
            return
        for out_dir in sorted({row["out_dir"] for row in rows if row["out_dir"]}):
            if await self._io(os.path.isdir, out_dir) and await self._discard(out_dir, kind=TREE):
                await self._io(self._prune, out_dir)

    async def _release_gone(self, rows: list[dict]) -> None:
        """Jobs whose record is gone from the history, as the service itself says: a job it was not asked about is not
        gone. Covers the records removed while this module was not listening."""
        jobs: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            if row["job_id"] and row["out_dir"] and row["status"] in FINAL:
                jobs[row["job_id"]].append(row)
        for job_id, group in jobs.items():
            if not any([await self._io(os.path.isdir, row["out_dir"]) for row in group]):
                continue
            try:
                job = AlignmentJob.model_validate(await self.request("conform.job.get", {"job_id": job_id}, timeout=30))
            except (BusRequestError, asyncio.TimeoutError, ValidationError):
                return  # the service could not say: the next time it is reachable asks again
            if job.status is JobStatus.GONE:
                await self._release_outputs(await self.repo.by_job(job_id))

    @staticmethod
    def _prune(out_dir: str) -> None:
        """What the removal of a job's folder leaves empty goes too: the job's own folders of the same key (its cache),
        then the folders around them. Another job's empty folder is not touched, the key tells them apart."""
        key = os.path.basename(out_dir)
        episode = os.path.dirname(os.path.dirname(out_dir))
        if os.path.isdir(episode):
            for entry in list(os.scandir(episode)):
                if entry.is_dir(follow_symlinks=False):
                    VoicesModule._remove_if_empty(os.path.join(entry.path, key))
                    VoicesModule._remove_if_empty(entry.path)
        for folder in (episode, os.path.dirname(episode)):
            VoicesModule._remove_if_empty(folder)

    @staticmethod
    def _folders_of(group: list[dict], fallback: str) -> list[str]:
        """Episode folders of voice-overs as they were made, not as the settings say now."""
        found = {os.path.dirname(row["temp_path"]) for row in group if row["temp_path"]}
        return sorted(found) or [fallback]

    async def _finish_move(self, row: dict, reference: str | None = None) -> bool:
        """Complete a track move whose start is written down: safe to repeat after any interruption.
        False when the track is in neither place; the row is failed and announced then."""
        source, destination = row["move_from"], row["output_path"]
        if not await self._io(self._output.settle, source, destination, timeout=MOVE_TIMEOUT_S,
                              owns=(source, destination)):
            await self.repo.mark_failed(row["id"], reason("track_vanished_on_move"))
            await self._announce(row)
            return False
        ref_path = reference or row["ref_path"]
        if row["status"] == S.ALIGNED:
            await self.repo.mark_placed(row["id"], destination, ref_path)
        else:
            await self.repo.set_output(row["id"], destination, ref_path)
        return True

    async def _submit(self, series: dict, season: int, episode: int, reference: str, uid: str,
                      ready: list[dict]) -> bool:
        """Send the ready voice-overs of an episode as one job; True when the service was certainly not reached
        and another try is owed. What may have reached it is carried by the recovery from the key."""
        label = f"{series['name']} s{season:02d}e{episode:02d}"
        ready = await self._with_files(ready, label)
        if not ready:
            return False
        conform_dir = await self._conform_episode(series["id"], season, episode)
        if not conform_dir:
            self.log.warning("%s: выравнивание не начато: рабочая папка не задана в настройках сервера выравнивания", label)
            return False
        folders = await self._workspace(dub_folder=conform_dir)
        key = uuid.uuid4().hex[:12]
        # A job works in folders of its own: an earlier job that is not confirmed stopped can not reach its files.
        out_dir, cache_dir = os.path.join(folders["out_dir"], key), os.path.join(folders["cache_dir"], key)
        ref_dir = os.path.join(conform_dir, REF_DIR, key)
        copy = os.path.join(ref_dir, reference_file(series["id"], season, episode, reference))
        for guarded in (out_dir, cache_dir, ref_dir, *[row["temp_path"] for row in ready]):
            if storage.busy(guarded):  # the service would work where an unfinished removal may still act
                self._when_free(guarded, series["id"])
                return False
        await self._io(lambda: [os.makedirs(folder, exist_ok=True) for folder in (out_dir, cache_dir, ref_dir)],
                       owns=(out_dir, cache_dir, ref_dir))
        try:  # the service reads only inside the work folder, so the reference is copied there first
            await self._io(shutil.copyfile, reference, copy, timeout=COPY_TIMEOUT_S, owns=(ref_dir,))
        except Exception:
            await self._io(shutil.rmtree, ref_dir, True, owns=(ref_dir,))
            raise
        await self.repo.mark_submitting([row["id"] for row in ready], key, uid, reference, out_dir)
        self._sending.add(key)
        try:
            return await self._send(label, key, ready, copy, out_dir, cache_dir)
        finally:
            self._sending.discard(key)
            self._recovery.poke("cleanup:")  # what waited for the answer to this submission may go on now
            self._recovery.poke("submission:")

    async def _send(self, label: str, key: str, ready: list[dict], reference: str, out_dir: str,
                    cache_dir: str) -> bool:
        """Send the job and take its outcome: until this returns the service may still take the job."""
        try:
            reply = SubmitReply.model_validate(await self.request("conform.submit", {
                "ref": reference, "dubs": [row["temp_path"] for row in ready],
                "out_dir": out_dir, "cache_dir": cache_dir,
                "label": label, "key": key}, timeout=SUBMIT_TIMEOUT_S))
        except (BusRequestError, asyncio.TimeoutError, ValidationError) as exc:
            self.log.warning("%s: ответа на постановку нет, спросим сервис, есть ли задача: %s", label, exc)
            await self._ask_service(key, ready)
            return False
        if reply.outcome is SubmitOutcome.UNCERTAIN:  # the service may have taken the job: the key stays, the service is asked
            self.log.warning("%s: исход постановки неизвестен, спросим сервис: %s", label, describe(reply.reason))
            await self._ask_service(key, ready)
            return False
        if reply.outcome is SubmitOutcome.UNAVAILABLE:
            await self.repo.release_submission(key)
            await self._io(shutil.rmtree, os.path.dirname(reference), True, owns=(os.path.dirname(reference),))
            self.log.info("%s: озвучки ждут сервис выравнивания: %s", label, describe(reply.reason))
            return reply.retry
        if reply.outcome is SubmitOutcome.REJECTED:
            for row in ready:
                await self.repo.mark_rejected(row["id"], reply.reason)
                await self._announce(row)
            self.log.warning("%s: задача выравнивания отклонена: %s", label, describe(reply.reason))
            return False
        await self._attach(key, reply.job_id, out_dir)
        return False

    async def _ask_service(self, key: str, rows: list[dict]) -> None:
        """The answer to a sent job is not known: the interface is told so, and the key finds the job again."""
        for row in rows:
            await self._announce(row)
        self._await_submission(key)

    async def _attach(self, key: str, job_id: str, scope: str | None = None) -> None:
        """The service has the job of this key: its voice-overs get the number, an early ending is applied. A job
        that no voice-over waits for any more (the rows were reset or deleted while the request was on its way)
        is handed to cancellation with the folder it writes in."""
        async with self._claim_lock:
            await self.repo.attach_job(key, job_id)
            mine = [row for row in await self.repo.by_key(key) if row["job_id"] == job_id]
            for row in mine:
                await self._announce(row)
            early = self._unclaimed.pop(job_id, None)
            if early is not None and mine:
                await self._apply_job(early)
        if not mine:
            await self._cancel_job(job_id, scope)

    async def _recover_one(self, key: str) -> bool:
        """Ask the service about a job sent without an answer: it names the job, or it never got it and the
        voice-overs are sent anew. False when the service cannot say, or while the request is still on its way."""
        if key in self._sending:
            return False
        try:
            reply = await self.request("conform.job.find", {"key": key}, timeout=30)
        except (BusRequestError, asyncio.TimeoutError):
            return False
        if not reply.get("reachable"):
            return False
        rows = await self.repo.by_key(key)
        waiting = [row for row in rows if row["status"] == S.ALIGNING and not row["job_id"]]
        if reply.get("job_id"):
            job_id = reply["job_id"]
            if waiting:
                self.log.info("задача %s принята сервисом, ответ потерян: номер восстановлен", job_id)
                await self._attach(key, job_id)
                self._await_job(job_id, now=True)  # the duty to learn its end starts with its number
            elif not any(row["job_id"] == job_id for row in rows) and not await self._journaled(key):
                await self._cancel_job(job_id)  # no voice-over and no journal entry owns this job
            return True
        await self.repo.release_submission(key)
        self.log.info("задачу %s сервис не получил, озвучки отправятся заново", key)
        for series_id in {row["series_id"] for row in rows}:
            self._kick(series_id)
        return True

    async def _journaled(self, path: str) -> bool:
        return any(entry["path"] == path for entry in await self.repo.cleanup_list())

    async def _cancel_job(self, job_id: str, scope: str | None = None) -> None:
        """Stop a job nobody needs: written to the journal first, with the folder it writes in, then asked to stop."""
        await self.repo.journal_cleanup(job_id, JOB, "cancel begun", scope)
        if not await self._clear(job_id):
            self._await_cleanup(job_id)

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
            await self._announce({**row, "status": S.PENDING}, E.STALE)
        if len(present) != len(ready):
            self.send_command("voices.dispatch", {"series_id": ready[0]["series_id"]})
        return present

    async def on_job_finished(self, env: Envelope) -> None:
        job = AlignmentJob.model_validate(env.payload)
        async with self._claim_lock:
            if not await self._apply_job(job):
                # The service can end a job before its number is written to the voice-overs: keep the
                # ending until they claim it.
                self._unclaimed[job.job_id] = job
                while len(self._unclaimed) > UNCLAIMED_JOBS:
                    self._unclaimed.popitem(last=False)

    async def _reconcile(self, job_id: str) -> bool:
        """A job sent before a restart: learn how it ended, or send its voice-overs again. False when the
        service could not say."""
        try:
            job = AlignmentJob.model_validate(await self.request("conform.job.get", {"job_id": job_id}, timeout=30))
        except (BusRequestError, asyncio.TimeoutError, ValidationError) as exc:
            self.log.info("задача выравнивания %s: итог пока неизвестен (%s)", job_id, exc)
            return False
        if job.status is JobStatus.GONE:
            rows = [row for row in await self.repo.by_job(job_id) if row["status"] == S.ALIGNING]
            await self.repo.release_to_ready([row["id"] for row in rows])
            for series_id in {row["series_id"] for row in rows}:
                self._kick(series_id)
        elif job.status in ENDED:
            await self._apply_job(job)
        return True

    async def _apply_job(self, job: AlignmentJob) -> bool:
        """Take the end of a job into the voice-overs that wait for it; False when none does."""
        rows = [row for row in await self.repo.by_job(job.job_id) if row["status"] == S.ALIGNING]
        if not rows:
            return False
        for row in rows:
            try:
                await self._apply_to(job, row)
            except Exception as exc:  # noqa: BLE001 - one broken result must not strand the others
                self.log.exception("сериал %d, s%02de%02d: результат озвучки %s не разобран",
                                   row["series_id"], row["season"], row["episode"], row["voice_label"])
                await self.repo.mark_failed(row["id"], reason("result_unreadable", error=f"{type(exc).__name__}: {exc}"))
                await self._announce(row)
        for series_id in {row["series_id"] for row in rows}:
            self._kick(series_id)
        return True

    async def _apply_to(self, job: AlignmentJob, row: dict) -> None:
        if job.status is not JobStatus.DONE:
            error = job_failure(job)
            await self.repo.mark_failed(row["id"], error)
            self.log.warning("сериал %d, s%02de%02d: озвучка %s не выровнена: %s", row["series_id"],
                             row["season"], row["episode"], row["voice_label"], describe(error))
            await self._announce(row)
            return
        result = result_for(job, row["temp_path"])
        verdict = refusal(result)
        conform_dir = await self._conform_episode(row["series_id"], row["season"], row["episode"])
        out_dir = row["out_dir"] or (await self._workspace(dub_folder=conform_dir))["out_dir"]
        track = track_of(out_dir, result) if verdict is None else None
        if verdict is None and not await self._io(os.path.isfile, track):
            verdict = reason("output_missing")
        if verdict is None:
            await self.repo.mark_aligned(row["id"], track)
        else:
            await self.repo.mark_rejected(row["id"], verdict)
            self.log.warning("сериал %d, s%02de%02d: озвучка %s отклонена: %s", row["series_id"],
                             row["season"], row["episode"], row["voice_label"], describe(verdict))
        await self._announce(row)

    async def _taken_paths(self, row: dict) -> set[str]:
        """Tracks that other voice-overs of the series already have next to their videos."""
        return {other["output_path"] for other in await self.repo.for_series(row["series_id"])
                if other["id"] != row["id"] and other["output_path"]}

    async def _place(self, row: dict, reference: str) -> None:
        source = row["aligned_path"]
        if not source or not await self._io(os.path.isfile, source):
            await self.repo.mark_failed(row["id"], reason("aligned_file_vanished"))
            await self._announce(row)
            return
        destination = self._output.destination(reference, row["voice_label"], source, await self._taken_paths(row))
        for guarded in (destination, source):
            if storage.busy(guarded):
                self._when_free(guarded, row["series_id"])
                return
        await self.repo.begin_move(row["id"], destination, source)
        if not await self._finish_move(await self.repo.get(row["id"]), reference):
            return
        self.log.info("сериал %d, s%02de%02d: дорожка %s рядом с видео: %s", row["series_id"],
                      row["season"], row["episode"], row["voice_label"], destination)
        await self._announce(row)

    async def _tidy(self, series_id: int, season: int, episode: int, root: str) -> None:
        """The episode is settled: its temporary files are of no use any more. What the service wrote stays while the
        record of its job is in the service's history, since the plots are shown from there."""
        group = await self.repo.for_episode(series_id, season, episode)
        if not group or any(row["status"] not in FINAL for row in group):
            return
        folders = await self._settle_folders(group, root, series_id, season, episode)
        if not (any(row["temp_path"] or row["aligned_path"] for row in group)
                or any([await self._io(os.path.isdir, folder) for folder in folders])):
            return
        await self.repo.settle_episode(series_id, season, episode, folders)
        if await self._clear_finished():
            await self.repo.hand_over_jobs(series_id, season, episode)
        await self._work_off()
        await self._release_gone(await self.repo.for_episode(series_id, season, episode))
        self.publish_event("voices.episode.settled",
                           {"series_id": series_id, "season": season, "episode": episode})


    async def on_series_deleted(self, env: Envelope) -> None:
        series_id = env.payload["series_id"]
        self.send_command("downloads.fetch.cancel", {"series_id": series_id})
        rows = await self.repo.for_series(series_id)
        folders = {os.path.dirname(folder) for folder in self._folders_of(rows, "")} - {""}
        # The rows go with their jobs and the folders to remove written to the journal in one transaction.
        found = await self._folders()
        made = {os.path.dirname(os.path.dirname(os.path.dirname(row["out_dir"]))) for row in rows if row["out_dir"]}
        folders = (folders or ({os.path.join(found["downloads"], str(series_id))} if found["downloads"] else set())) | made | (
            {os.path.join(found["conform"], str(series_id))} if found["conform"] else set())
        await self.repo.delete_for_series(series_id, folders)
        for row in rows:
            await self._announce(row, E.REMOVED)
        await self._work_off()
        await self._admit()
        self.publish_event("series.purged", {"series_id": series_id, "owner": self.name})
