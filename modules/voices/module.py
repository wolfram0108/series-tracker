"""Module voices: the voice-overs of a series (mission voice-alignment, stages 4 and 6).

Owns voice_dubs: one row per (series, episode, voice) — the additional voice-overs that are
downloaded next to the reference video and later aligned to it.

Settings: voices.source_priority (JSON list of player kinds), voices.groups and voices.player_groups
(JSON objects {group: [names]}: a voice-over, or a player, called by one of the names belongs to the group),
voices.max_temp_episodes. They are read together (settings.py): a wrong value is logged once and its default
is used.

Query:
  voices.groups.resolve GroupsResolveRequest -> GroupsResolveReply: the user group of every name (None for a name no group
      holds), by the folded name or trigger of the group (core/voice_names.py); asked by `composition` for the names of the
      voice-overs of its rows. A combined credit "A / B" is one name.
  voices.settings.get {} -> VoiceSettingsView: the groups of voice-overs and the order of sources as they apply, the
      order to return to and the fields whose stored value was refused
  voices.dubs.get {series_id} -> {items: [{season, episode, part, voice_key, voice_label, status,
      attempt, error, temp_path, job_id, aligned_path, output_path, outcome, ref_uid, ref_path,
      detail, state, hint}]}  (detail = {code, params} of the outcome or error, see reasons.py; the
      fields `error` and `outcome` hold the stored reason as it is)
Command:
  voices.settings.set VoiceSettingsDraft -> VoiceSettingsSaved: groups and order are checked (editor.check) and written
      in one step through settings.values.set_many, or refused with coded problems and nothing written; the next
      read of the settings uses the new values, no restart. Rows keep the key they were recorded under.
  voices.failures.forget {series_id} -> {reset} — the user's reset of failed attempts, a change of the series (under its
      admission) and not a sync: the rejected and failed voice-overs start over from the first source of their chain and
      wait for the next sync.
  voices.dispatch {series_id} — the explicit sync of a series, sent by scan once the references are
      loaded: plans the
      voice-overs of the series (composition.get for the rows and the levers, scan.output.get for the reference of
      every episode), records a voice_dubs row per planned voice-over, ADMITS every planned row to download (a row that
      a change of the layout recorded is not admitted until a sync), offers the rows that failed again and downloads
      what is not on disk yet into the temporary downloads folder of the work folder (named by the owner in the
      settings of the alignment server and asked of its bridge; not named: the rows are recorded and wait, and they
      begin when conform.settings.changed says it was named). Before a job is sent the reference video is copied into
      the service's folder of the episode, so that the service reads and writes only inside the work folder; the copy
      goes with the other temporary files of the episode. Only a series of kind `video` is planned (catalog.series.get).
  voices.settle {series_id} — a sync without download: the voice-overs follow the plan as in dispatch (what the plan
      does not want is withdrawn with what it made, tracks follow the names of their voice-overs), nothing is admitted.
Commands out: downloads.fetch. Event out: work.withdrawn (the downloads of dropped voice-overs and files stop). Queries to conform: workspace (the folders of the service
            for a place, asked of the bridge: this module does not know its layout), submit (with a key that
            finds the job again; the reply is a SubmitReply: accepted, rejected, unavailable (not sent) or
            uncertain, anything that is not a clear reply counts as uncertain, and only "unavailable" releases the
            key), job.find, job.get, cancel (a job whose reference was replaced or whose series was deleted; what
            could not be cancelled is retried from the cleanup journal).

Events in:  composition.changed {series_id, rev, episodes, summary_only} and scan.reference.changed {series_id, season,
            episode, mark}: the layout or a reference changed. They change the plan alone: a new voice-over is recorded as
            planned, a row with no work yet takes its fresh chain, and nothing on the disk or at the service changes and
            nothing is downloaded. What the plan takes away — a voice-over that left the plan, an episode whose reference
            is none or unfit, a work on a video the layout gives to another episode or voice-over or took out
            (`planning.moved`), a variant left out, a track renamed after its voice-over — is done by a sync
            (voices.dispatch, voices.settle). A row they record is not admitted: it waits, planned, for a sync. An event that says
            only the summaries of the sources changed is not acted on. Nothing is asked of composition or scan at start:
            the first event or dispatch of a series makes the whole plan of that series; conform.settings.changed (the
            work folder may be named now);
            downloads.fetch.started and .finished (our keys `dub:<id>`), conform.queue.changed (waiting or running
            jobs of the service), series.downloaded.changed,
            downloads.item.renamed, library.relocation.finished, conform.job.finished, conform.job.removed,
            conform.daemon.up, series.deleted.
Events out: series.status.contribution {source: voices, series_id, flags: {downloading, aligning, waiting}} — what the
            voice-overs a sync admitted are at (series_status): a file being fetched, a job at the service of alignment,
            or waiting for its turn, its reference or its placing; told when it changes, and at the start for every series
            with such work.
            voices.dub.changed {series_id, season, episode, part, voice_key, voice_label, status, state, hint, detail,
            epoch, rev} — epoch is the number of this start of the module, kept in the database and raised at every
            start; rev grows with every event of a run: the interface keeps the newest (epoch, rev) of two it
            receives out of order, and no clock takes part;
            every write to a row is announced, including the creation of a planned row and its removal;
            voices.dubs.snapshot {epoch, rev, items: [{series_id, season, episode, part, voice_key, voice_label, status,
            state, hint, detail}]} — every row, published when an interface connects (gateway.sse.clients);
            state is what the user sees (states.py: planned, download_queued, downloading, align_queued, running,
            done, error), hint names the sentence that tells apart the causes of one state, status the
            step behind it; detail is the reason of a failure, a rejection or a stale track as
            {code, params} (code `external`: the words of another system in params.text), else null;
            a voice-over that was sent to the service and has no job number yet says so in its hint (`awaiting_answer`);
            status is one of
            ready, aligning, aligned, placed, rejected, failed, moved (the track followed its video),
            stale (its reference was replaced, or downloaded again: a new file, every verdict on the old one is given again)
            and removed (the episode and its video are gone);
            series.purged,
            voices.settings.changed VoiceSettingsView — the settings as they apply after a save, so that every open
            settings screen shows them without asking back,
            voices.episode.settled {series_id, season, episode, part} — every voice-over of the episode is
            placed, rejected or failed and its temporary files (the downloaded voice-overs and the copy of the
            reference) are gone or on the cleanup journal; the folders the service wrote in stay with the records of
            its jobs.

Where each policy lives: the steps a row may take and the sets of steps (lifecycle.py, enforced by the repository),
what is known of the alignment service (alignment.py), the form in which a finished voice-over reaches the library
(outputs.py, injected as `output=`), the shape of every message (core/contracts).

The key of a voice-over is its label in the layout: the name of its user group, or "Неизвестная озвучка N". A row keeps
the key it was recorded under for good. When the layout calls a recorded voice-over by another name (a group was made
or renamed) the plan finds it by a link: a planned voice-over that has a candidate with the link of a row of the same
episode under another key is that row (planning.adopt); the row keeps its key and its work, and takes the new label
(a placed track follows it). The key is the identity, not what the user reads: the file of a voice-over is named by the
label it has when its download begins (dub_name; the key when it has none) with the player and the place of its source
(two sources of one voice never share a file name), and the row keeps that path.

A row of the layout that covers several episodes (a compilation: one video) is a candidate like a single one. Its video
is one file, a row of voice_files, downloaded once (downloads.fetch with the key `file:<id>`) and read by the voice-overs
of every episode it covers that has a reference: each goes to the service with the reference of its own episode. The
file belongs to its row in voice_files; it goes to the cleanup journal, with its row deleted in the same transaction,
when no voice-over reads it (all placed, rejected, failed or gone from the plan); a transfer under way is cancelled
(work.withdrawn with the key of the file), and an ending that comes anyway puts the folder on the journal again. A
file that cannot be downloaded sends every voice-over that waited on it to its next candidate; the new row of a changed
compilation is a new file, and the old one stays while a voice-over still reads it. A series that is deleted loses its
downloads, its files and its rows.
Paths of the working files are taken from the rows (folder of `temp_path`), never from the settings of the
moment or from the service's path map.

Downloading is delegated to downloads.fetch. A dub tries its chain in the order of its queue (the variants of the lever
`voice.order` first, then by the order of players in the settings: `core.variant_queue`), the rows of the same voice that cover the
episode; when none gives the file the row is `failed`, a warning is logged, and the next dispatch offers it again from
the main candidate. A dub is `ready` once its file
is on disk; rows left `downloading` by a restart are sent again on start.

Alignment (stage 7). An episode goes to the alignment service as one job once its reference video
is on disk and none of its voice-overs is still being downloaded or aligned: the `ready` ones are
sent (conform.submit). A voice-over that was not downloaded does not hold the episode back, and one
that arrives later makes a job of its own. When a job ends, a voice-over that the service
calls `ok` becomes `aligned` (what it adds, `suspect` or `warnings`, is what it found and put right: it goes to the
journal); a pair the service turned down itself (`refused`: its trace holds a decision that aborted, e.g. a foreign video)
is a verdict on the video of that source, not on the voice-over: the next source of its chain that is another video is
downloaded and sent (CVH gave another video under the number where Kodik gave the episode; the same video on another
page, by its player and `video`, is passed over), and it is `rejected` with the reason when no other video is left; any other that is not `ok` (a crash in the service, no result for the file, a success naming no file), a job
that failed (for the pairs it did not reach: those it had finished keep their results) and a job the service refused to take
make the voice-over `failed` with the reason (the next sync offers it again). An `aligned`
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
import json
import os
import re
import shutil
import uuid
from collections import OrderedDict, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from core import NO_HANDLER, BaseModule, BusRequestError
from pydantic import ValidationError

from core import container
from core.contracts import (RUN_CHANGED, WORK_WITHDRAWN, RunChanged, RunSnapshot, Slot, WorkWithdrawn, slot_of, AlignmentJob, CompositionChanged, CompositionReply, DubEvent, DubEventStatus as E, DubItem,
                            DubRecord, DubsSnapshot, DubStatus as S, GroupsResolveReply, GroupsResolveRequest,
                            JobStatus, Reference, ReferenceChanged, SnapshotItem, SubmitOutcome, SubmitReply,
                            VoiceSettingsDraft, VoiceSettingsSaved)
from core import storage
from core.storage import DEFAULT_TIMEOUT_S as IO_TIMEOUT_S, MOVE_TIMEOUT_S
from core.db import Database
from core.envelope import Envelope
from core.work_report import WorkReport
from core.reasons import describe, parse as parse_reason, reason

from . import editor, planning
from .planning import candidate_of
from .alignment import ENDED, job_failure, refusal, result_for, told, track_of, turned_down
from .cleanup import ARTIFACT, JOB, PATH, SUBMISSION, TREE, Cleanup, prune, remove_if_empty
from .lifecycle import FINAL, WORKING
from .outputs import Output, SidecarOutput
from .recovery import Recovery
from .repository import VoicesRepository
from .settings import PREFIX, VoiceSettings, parse
from .states import public_hint, public_state

DUB_MAX_HEIGHT = 720
# A reference video is some gigabytes: this is only the fuse of its copy.
COPY_TIMEOUT_S = 1800.0
# The keys of downloads.fetch: a voice-over of its own, a file read by several voice-overs.
DUB_KEY, FILE_KEY = "dub:", "file:"
# The folder of an episode where each job keeps the copy of the reference it reads.
REF_DIR = "_ref"
# The first pause before an obligation is tried again, the number of failures after which the owner is told,
# and the longest pause; the pauses double in between.
SUBMIT_RETRY_S = 30.0
SUBMIT_RETRIES = 6
RETRY_CEILING_S = 600.0
# The ceiling of the exponent of a pause: far above what the ceiling of the pause itself needs, far below an overflow.
MAX_DOUBLINGS = 30
UNCLAIMED_JOBS = 256


def _pause(failures: int) -> float:
    return min(SUBMIT_RETRY_S * 2 ** min(failures, MAX_DOUBLINGS), max(RETRY_CEILING_S, SUBMIT_RETRY_S))


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("._") or "dub"


def slot_tag(season: int, episode: int, part: int) -> str:
    """How an episode is named in file and folder names; the part follows where the episode has parts."""
    return f"s{season:02d}e{episode:02d}" + (f"p{part}" if part else "")


def _another_file(row: dict, rev: int | None) -> bool:
    """Whether the reference file of the row's media item is another than the one the row was judged against."""
    return rev is not None and row.get("ref_rev") is not None and row["ref_rev"] != rev


def _video_name(head: str, voice: str, candidate: Mapping[str, Any]) -> str:
    """The one form of the name of a downloaded video: what it is for (`head`), the voice, then the player and the place
    of the source it comes from (release_table.place_of), so that the sources of one voice never share a name."""
    tail = [candidate.get("player") or "", *candidate.get("place", ())]
    return "_".join([head, _safe(voice), *(_safe(part) for part in tail if part)]) + ".mp4"


def dub_file(row: Mapping[str, Any], name: str, candidate: Mapping[str, Any]) -> str:
    """The name a voice-over is downloaded and sent to the service under: series, episode, part, the name of the voice
    and its source."""
    return _video_name(f"{row['series_id']}_{slot_tag(row['season'], row['episode'], row['part'])}", name, candidate)


def dub_name(row: dict, slot_rows: Iterable[dict], candidate: Mapping[str, Any]) -> str:
    """The name of the voice in the name of its file: the name the voice-over has now, so that the user sees the one they
    gave; its key when it has none, or when another row of the episode holds a file of that name (two files never share
    one). Chosen once, when its download begins: the row keeps the path, and everything after reads the path."""
    held = {os.path.basename(each["temp_path"]) for each in slot_rows
            if each.get("temp_path") and each.get("id") != row.get("id")}
    for name in (row.get("voice_label") or "", row["voice_key"]):
        if name and dub_file(row, name, candidate) not in held:
            return name
    return row["voice_key"]


def file_path(root: str, series_id: int, file_id: int, candidate: Mapping[str, Any]) -> str:
    """Where the file of a source row that covers several episodes is downloaded: a folder of its own, so that the
    folder goes to the journal with the file whatever the episodes that read it do."""
    return os.path.join(root, str(series_id), f"f{file_id}",
                        _video_name(f"{series_id}_f{file_id}", candidate["studio"], candidate))


def sent_as(row: dict) -> str:
    """The name a voice-over is sent to the service under: the name of the file it reads; before its download begins,
    the name it would take now."""
    if row.get("temp_path"):
        return os.path.basename(row["temp_path"])
    return dub_file(row, row.get("voice_label") or row["voice_key"], candidate_of(row))


def reference_file(series_id: int, season: int, episode: int, part: int, reference: str) -> str:
    """The name the copy of a reference video is given for the service."""
    return f"{series_id}_{slot_tag(season, episode, part)}_ref{os.path.splitext(reference)[1]}"


def episode_dir(root: str, series_id: int, season: int, episode: int, part: int) -> str:
    return os.path.join(root, str(series_id), slot_tag(season, episode, part))


def temp_path(root: str, row: Mapping[str, Any], name: str, candidate: Mapping[str, Any]) -> str:
    return os.path.join(episode_dir(root, row["series_id"], row["season"], row["episode"], row["part"]),
                        dub_file(row, name, candidate))


class VoicesModule(BaseModule):
    name = "voices"

    def __init__(self, bus, db: Database | None = None, *, output: Output | None = None) -> None:
        self.repo = VoicesRepository(db, on_idle_transfer=self._cancel_transfer) if db is not None else None
        self._output = output or SidecarOutput()
        self._locks: dict[int, asyncio.Lock] = {}
        self._kicked: set[int] = set()
        self._job_status: dict[str, str] = {}
        self._fetching: set[int] = set()
        self._status: dict[int, dict[str, bool]] = {}  # what the status of each series was last told
        self._file_fetching: set[int] = set()
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
        # Series with a planning pass asked for and not begun (asks merge into one), and series that were deleted.
        self._planning: set[int] = set()
        self._deleted: set[int] = set()
        # Series with an episode that has no reference and a download or the work folder to wait for: the end of the
        # download, or the naming of the folder, asks for the plan again.
        self._deferred: set[int] = set()
        # The series whose sync is under way: the plan of their voice-overs is the one the sync made (the snapshot);
        # a change of the layout or a reference that comes meanwhile is planned when the run ends.
        self._in_run: set[int] = set()
        # The settings of the voice-overs each open run took (RunSnapshot.voices): every pass of a series in a run works
        # by them, not by the settings as they are now (I-60).
        self._run_values: dict[int, dict] = {}
        # The generation of the last announcement of a run heard for each series (RunChanged.seq): a reading of the open
        # runs older than it says nothing of that series.
        self._run_heard: dict[int, int] = {}
        self._owed_plans: set[int] = set()
        self._work = WorkReport(self.name, self._has_work, self.publish_event)
        super().__init__(bus)
        self._recovery = Recovery(self.log, pause=_pause, report_after=lambda: SUBMIT_RETRIES)
        self._cleanup = Cleanup(self.repo, self._output, self.log, io=self._io, request=self.request,
                                retry=self._await_cleanup, sending=self._sending, poke=self._recovery.poke)

    def register(self) -> None:
        self.handle("voices.groups.resolve", self.on_groups_resolve)
        self.handle("voices.dubs.get", self.on_dubs_get)
        self.handle("voices.settings.get", self.on_settings_get)
        self.handle("voices.settings.export", self.on_settings_export)
        self.handle("voices.settings.set", self.on_settings_set)
        self.handle("voices.dispatch", self.on_dispatch, concurrent=True)
        self.handle("voices.failures.forget", self.on_failures_forget)
        self.handle("voices.settle", self.on_settle, concurrent=True)
        self.handle("voices.work.get", self.on_work_get, concurrent=True)
        self.handle("voices.tracks.get", self.on_tracks_get, concurrent=True)
        self.handle("voices.tracks.packed", self.on_tracks_packed)
        self.handle(RUN_CHANGED, self.on_run_changed)
        self.handle("system.started", self.on_system_started)
        self.handle("composition.changed", self.on_composition_changed)
        self.handle("scan.reference.changed", self.on_reference_changed)
        self.handle("conform.settings.changed", self.on_conform_settings_changed)
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

    async def on_system_started(self, env: Envelope) -> None:
        """What the database says is unfinished is taken up once every module is up: the workspace is asked of conform and
        the layout of composition, and both start after this module, so asking on its own start would fail the system's start.
        The open runs and their snapshots are taken first, so that what is taken up works by them."""
        if self.repo is None:
            return
        await self._take_open_runs()
        await self._release_files()
        await self._admit()
        await self._cleanup.sweep()
        await self._resume_obligations()
        for series_id in await self.repo.series_with_open_work():
            self._kick(series_id)
        for series_id in await self.repo.series_with_live_dubs():  # the status of a series is rebuilt from the tables
            await self._tell_work(series_id)

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
        self._recovery.want(f"cleanup:{path}", lambda: self._cleanup.clear(path), now=now)

    def _await_series(self, series_id: int) -> None:
        self._recovery.want(f"series:{series_id}", lambda: self._retry_series(series_id))

    async def _retry_series(self, series_id: int) -> bool:
        return not await self._advance_locked(series_id)

    async def _io(self, fn, *args, timeout: float = IO_TIMEOUT_S, owns=()):
        """A call into the storage that waits for a limited time; `owns` is what a changing call changes."""
        return await storage.call(fn, *args, timeout=timeout, owns=owns)

    async def _workspace(self, *, dub_folder: str) -> dict:
        """The folders the service writes to for an episode's folder: only the service's bridge knows its layout."""
        return await self.request("conform.workspace", {"dub_folder": dub_folder})

    def _when_free(self, path: str, series_id: int) -> None:
        """A step that needs a path an unfinished storage call still owns waits for that call to end."""
        if path not in self._watched:
            self._watched.add(path)

            def free() -> None:
                self._watched.discard(path)
                self._kick(series_id)

            storage.when_free(path, free)

    async def _settings(self, values: dict | None = None) -> VoiceSettings:
        """Every setting of the module in one read, or the values a run took (RunSnapshot.voices); a problem with a value
        is logged once, then the default is used."""
        if values is None:
            values = (await self.request("settings.values.by_prefix", {"prefix": PREFIX})).get("values") or {}
        settings = parse(values)
        for problem in sorted(set(settings.problems) - self._reported):
            self.log.warning("озвучки: настройка: %s", problem)
        self._reported = set(settings.problems)
        return settings

    async def _folders(self) -> dict:
        """The two folders inside the work folder, asked of the bridge: both empty while the owner has not named it."""
        return await self.request("conform.workspace", {})

    async def _downloads_root(self) -> str:
        return (await self._folders())["downloads"]

    async def _conform_episode(self, series_id: int, season: int, episode: int, part: int) -> str:
        """Where the service works for this episode; empty while the work folder is not named."""
        root = (await self._folders())["conform"]
        return episode_dir(root, series_id, season, episode, part) if root else ""


    async def on_groups_resolve(self, env: Envelope) -> dict:
        request = GroupsResolveRequest.model_validate(env.payload)
        groups = (await self._settings(request.values)).voices
        return GroupsResolveReply(groups={name: groups.group_of(name) for name in request.names}).model_dump(mode="json")

    async def on_dubs_get(self, env: Envelope) -> dict:
        rows = await self.repo.for_series(int(env.payload["series_id"]))
        return {"items": [self._record(row).model_dump(mode="json") for row in rows]}

    async def on_settings_get(self, env: Envelope) -> dict:
        """What applies now; with `values` (RunSnapshot.voices), what applied when a run took them."""
        return editor.view(await self._settings((env.payload or {}).get("values"))).model_dump(mode="json")

    async def on_settings_export(self, env: Envelope) -> dict:
        """The stored settings as they are, for the snapshot of a run (RunSnapshot.voices)."""
        reply = await self.request("settings.values.by_prefix", {"prefix": PREFIX})
        return {"values": reply.get("values") or {}}

    async def on_settings_set(self, env: Envelope) -> dict:
        draft = VoiceSettingsDraft.model_validate(env.payload)
        problems = editor.check(draft, (await self._settings()).player_groups)
        if not problems:
            await self.request("settings.values.set_many", {"values": editor.stored(editor.clean(draft))})
        view = editor.view(await self._settings())
        if not problems:
            self.log.info("озвучки: настройки сохранены")
            self.publish_event("voices.settings.changed", view.model_dump(mode="json"))
        return VoiceSettingsSaved(saved=not problems, problems=problems, settings=view).model_dump(mode="json")


    async def on_dispatch(self, env: Envelope) -> None:
        series_id = int(env.payload["series_id"])
        async with self._locks.setdefault(series_id, asyncio.Lock()):
            try:
                self._take_run_values(series_id, env.payload.get("settings"))
                await self._dispatch(series_id, env.payload.get("settings"))
            except (BusRequestError, LookupError, ValueError, OSError, asyncio.TimeoutError) as exc:
                self.log.warning("сериал %d: озвучки не подготовлены: %s", series_id, exc)
                raise  # the sender keeps its command: the reply is the word that the work is written down

    async def on_failures_forget(self, env: Envelope) -> dict:
        """The user asked to try again what failed: a rejected or failed voice-over starts over from the first source of
        its chain, read anew by the plan; what it owned goes to the cleanup journal (the same step as a replaced
        reference). A change of the series, not a sync: the voice-overs set back wait for the next sync."""
        series_id = int(env.payload["series_id"])
        async with self._locks.setdefault(series_id, asyncio.Lock()):
            failed = [row for row in await self.repo.for_series(series_id) if row["status"] in (S.REJECTED, S.FAILED)]
            reset = 0
            for row in failed:
                if await self.repo.reset(row["id"], None):
                    reset += 1
                    await self._announce(await self.repo.get(row["id"]))
        if reset:
            self.log.info("сериал %d: неудачные озвучки сброшены по команде пользователя: %d", series_id, reset)
        return {"reset": reset}

    async def on_settle(self, env: Envelope) -> None:
        """A sync without download: the voice-overs follow the plan (works the plan does not want are withdrawn, tracks
        follow the names of their voice-overs), and nothing new is admitted to download."""
        series_id = int(env.payload["series_id"])
        async with self._locks.setdefault(series_id, asyncio.Lock()):
            if series_id in self._deleted or not await self._is_video(series_id):
                return
            self._take_run_values(series_id, env.payload.get("settings"))
            await self._plan_series(series_id, offer=False, settle=True, values=env.payload.get("settings"))
        self._kick(series_id)

    async def _take_open_runs(self) -> None:
        """The runs a restart left open, as scan has their commands written: the series are in a run, by its snapshot."""
        try:
            reply = await self.request("scan.runs.get", {})
        except BusRequestError as exc:
            if NO_HANDLER not in str(exc):
                raise
            return  # no runs in this process
        as_of = int(reply.get("as_of") or 0)
        for run in reply["runs"]:
            series_id = int(run["series_id"])
            if self._run_heard.get(series_id, 0) > as_of:
                continue  # its run was announced after this reading (opened again or closed): the announcement stands
            self._in_run.add(series_id)
            if run.get("snapshot") is not None:
                self._run_values[series_id] = dict(RunSnapshot.model_validate(run["snapshot"]).voices)

    def _take_run_values(self, series_id: int, values: dict | None) -> None:
        """The settings a run handed over with its step are the settings of the whole run of the series."""
        if values is not None:
            self._run_values[series_id] = dict(values)

    async def _dispatch(self, series_id: int, values: dict | None = None) -> None:
        """The explicit sync: the series may download from now on, and what failed is offered again."""
        if series_id in self._deleted or not await self._is_video(series_id):
            return
        if not await self._downloads_root():
            self.log.warning("сериал %d: озвучки записаны, но не скачиваются: рабочая папка не задана в настройках "
                             "сервера выравнивания", series_id)
        await self._plan_series(series_id, offer=True, values=values)
        for row in await self.repo.for_series(series_id):
            if row["status"] == S.FAILED:
                self.log.warning("сериал %d, s%02de%02d: озвучка %s недоступна: %s", series_id,
                                 row["season"], row["episode"], row["voice_label"], row["error"])
        self._kick(series_id)

    async def on_composition_changed(self, env: Envelope) -> None:
        changed = CompositionChanged.model_validate(env.payload)
        if not changed.summary_only:  # only the summaries of the sources moved: no row and no lever did
            self._plan_after_change(changed.series_id)

    async def on_reference_changed(self, env: Envelope) -> None:
        self._plan_after_change(ReferenceChanged.model_validate(env.payload).series_id)

    def _plan_after_change(self, series_id: int) -> None:
        if series_id in self._in_run:
            self._owed_plans.add(series_id)
            return
        self._want_plan(series_id)

    async def on_tracks_get(self, env: Envelope) -> dict:
        """The voice-overs of a series, for the packing of its containers (ЭТАП_11): each by its key (the track of it in a
        container carries the same key), with its name, its step (`placed` — at its end), where its own file lies (none
        once it is packed) and the identity of the video it is aligned to. A voice-over the plan holds at any step keeps
        its track in the container; one that left the plan has no row here."""
        rows = await self.repo.for_series(int(env.payload["series_id"]))
        return {"tracks": [{"dub_id": row["id"], "season": row["season"], "episode": row["episode"], "part": row["part"],
                            "key": row["voice_key"], "label": row["voice_label"], "status": row["status"],
                            "path": row["output_path"] if row["status"] == S.PLACED else None,
                            "aligned_to": row.get("aligned_to"), "ref_uid": row["ref_uid"], "ref_path": row["ref_path"]}
                           for row in rows]}

    async def on_tracks_packed(self, env: Envelope) -> None:
        """The tracks went into the container of their episode: their own files are gone, the container holds them."""
        dub_ids = [int(each) for each in env.payload.get("dub_ids", [])]
        await self.repo.mark_packed(dub_ids, env.payload["container"], env.payload.get("aligned_to") or "")
        for dub_id in dub_ids:
            row = await self.repo.get(dub_id)
            if row is not None:
                await self._announce(row)

    async def on_run_changed(self, env: Envelope) -> None:
        run = RunChanged.model_validate(env.payload)
        self._run_heard[run.series_id] = max(self._run_heard.get(run.series_id, 0), run.seq)
        if run.active:
            self._in_run.add(run.series_id)
            if run.snapshot is not None:
                self._run_values[run.series_id] = dict(run.snapshot.voices)
            return
        self._in_run.discard(run.series_id)
        self._run_values.pop(run.series_id, None)
        if run.series_id in self._owed_plans:
            self._owed_plans.discard(run.series_id)
            self._want_plan(run.series_id)

    async def on_conform_settings_changed(self, env: Envelope) -> None:
        """The work folder may have been named: what waited for it begins, and the episodes that waited to be looked at are."""
        for series_id in list(self._deferred):
            self._replan_deferred(series_id)
        await self._admit()

    def _want_plan(self, series_id: int) -> None:
        """Bring the rows of the series in line with the plan once more; asks that come while one is waiting merge."""
        if series_id in self._planning or series_id in self._deleted:
            return
        self._planning.add(series_id)
        self._spawn(self._plan_or_await(series_id), f"voices:plan:{series_id}")

    async def _plan_or_await(self, series_id: int) -> None:
        if not await self._plan_locked(series_id):
            self._recovery.want(f"plan:{series_id}", lambda: self._plan_locked(series_id))

    async def _plan_locked(self, series_id: int) -> bool:
        """One pass that records the plan without offering the failed again; False when a neighbour did not answer
        and the pass is owed (the recovery carries it)."""
        async with self._locks.setdefault(series_id, asyncio.Lock()):
            self._planning.discard(series_id)
            if series_id in self._deleted:
                return True
            try:
                if await self._is_video(series_id):
                    await self._plan_series(series_id, offer=False)
                return True
            except (BusRequestError, LookupError, ValueError, OSError, asyncio.TimeoutError) as exc:
                self.log.warning("сериал %d: озвучки не приведены к плану, повторим: %s", series_id, exc)
                return False

    async def _is_video(self, series_id: int) -> bool:
        """Voice-overs are planned for a series of kind `video`; a series the catalog no longer has is no one's."""
        try:
            series = await self.request("catalog.series.get", {"series_id": series_id})
        except BusRequestError as exc:
            if "LookupError" in str(exc):
                return False
            raise
        return series.get("kind") == "video"

    async def _plan_series(self, series_id: int, *, offer: bool, settle: bool | None = None,
                           values: dict | None = None) -> None:
        """Read the layout and the references, plan the voice-overs, bring the rows in line, and begin what is allowed.

        What the plan alone takes away — a work withdrawn, a track removed, a job cancelled, a track renamed after its
        voice-over — is done in a sync only (`settle`, by default with `offer`): a recount on an event records the plan
        of the rows that have no work yet and changes nothing on the disk."""
        settle = offer if settle is None else settle
        # The excluded rows too: a work on a video the configuration took out is withdrawn, on one not brought it stands.
        layout = CompositionReply.model_validate(
            await self.request("composition.get", {"series_id": series_id, "include_excluded": True}))
        references = [Reference.model_validate(item) for item in
                      await self.request("scan.output.get", {"series_id": series_id})]
        # A sync plans by the settings its run took (RunSnapshot.voices), and so does every pass while the run is open (a
        # replan after a change of the reference, a recovery); a recount after the run by the current ones.
        settings = await self._settings(values if values is not None else self._run_values.get(series_id))
        plan = planning.plan(layout.rows, references, layout.settings, settings.priority, settings.players,
                             {source.source_id: source.place for source in layout.sources})
        self.log.info("сериал %d: озвучки — раскладка %d, серий с референсом %d, озвучек к плану %d", series_id,
                      layout.rev, len(plan.with_reference), len(plan.dubs))
        before = await self.repo.for_series(series_id)
        keys = planning.adopt(plan.dubs, before)
        planned: set[tuple[int, int, int, str]] = set()
        touched: set[tuple[int, int, int, str]] = set()
        relabeled: set[tuple[int, int, int, str]] = set()
        recorded = {(row["season"], row["episode"], row["part"], row["voice_key"]): row for row in before}
        videos = planning.Videos.of(layout.rows)
        for dub in plan.dubs:
            key = (dub.season, dub.episode, dub.part, keys[(dub.season, dub.episode, dub.part, dub.voice_key)])
            planned.add(key)
            if settle and key in recorded and planning.moved(recorded[key], dub.chain, videos):
                # The video the work runs on is another episode's or voice-over's now: the work, what it made and the
                # track go; the voice-over is planned again from its own videos, in this pass.
                self.log.info("сериал %d, s%02de%02d: озвучка %s шла с видео, которое раскладка отдала другой серии "
                              "или исключила, — снята",
                              series_id, dub.season, dub.episode, dub.voice_label)
                await self._retire_started(series_id, recorded.pop(key))
            if settle and (dub.left_out or dub.left_out_links) and key in recorded:
                await self._follow_variants(series_id, recorded[key], dub)
            if settle and await self.repo.relabel(series_id, *key, dub.voice_label):
                relabeled.add(key)
            if await self.repo.upsert(series_id, *key, dub.voice_label, [each.as_json() for each in dub.chain],
                                      offer=offer):
                touched.add(key)
        if offer:
            await self.repo.admit(series_id, planned)
        for gone in (await self.repo.drop_unplanned(series_id, planned, plan.undecided, plan.with_reference)
                     if settle else []):
            self.log.info("сериал %d: озвучка %s серии %d вышла из плана", series_id, gone["voice_label"], gone["episode"])
            if gone["status"] == S.DOWNLOADING and gone["file_id"] is None:  # its own download goes with it
                self._fetching.discard(gone["id"])
                self._withdraw(series_id, [f"{DUB_KEY}{gone['id']}"])
            await self._announce(gone, E.REMOVED)
        if settle:
            await self._drop_without_reference(series_id, plan, before)
        await self._cleanup.work_off()
        after = await self.repo.for_series(series_id)
        await self._announce_difference(before, [row for row in after
                                                 if (row["season"], row["episode"], row["part"], row["voice_key"]) in touched])
        for row in after:
            if (row["season"], row["episode"], row["part"], row["voice_key"]) in relabeled:
                await self._announce(row)
        if relabeled and settle:
            self._kick(series_id)  # a track that is placed follows the name of its voice-over
        if series_id in self._deleted:  # the series went while the pass was writing: what it wrote goes too
            await self._forget(series_id)
            return
        await self._recheck_ready(series_id)
        await self._release_files()
        await self._admit()

    async def _retire_started(self, series_id: int, row: dict) -> None:
        """The work of a row is not wanted: its download stops, what it made goes to the journal with the track and the job
        at the service, the row leaves, and the interface is told."""
        if row["status"] == S.DOWNLOADING and row["file_id"] is None:  # its own download goes with it
            self._fetching.discard(row["id"])
            self._withdraw(series_id, [f"{DUB_KEY}{row['id']}"])
        await self.repo.retire(row["id"])
        await self._announce(row, E.REMOVED)

    async def _follow_variants(self, series_id: int, row: dict, dub: planning.PlannedDub) -> None:
        """The user left variants out. Work that runs from one of them, or a track made from it, is not wanted at any
        step: it stops, what it made goes to the journal, and the voice-over is planned again from the others. Work
        that runs from another candidate goes on, without the variants left out among its next ones."""
        if row["status"] in (S.PENDING, S.FAILED) or not row["chain"]:  # nothing was begun: the plan offers it again
            return

        offered = {each.url for each in dub.chain}
        players = {player for _, player in dub.left_out}

        def left_out(candidate: dict) -> bool:
            # A candidate is its source and player; the link is only where the variant showed its video when it was recorded.
            if candidate.get("source_id") is not None:
                return (candidate["source_id"], candidate["player"]) in dub.left_out
            # A chain recorded before candidates said their source: its link left out is left out; a link no variant offers
            # any more is of the variant that was left out when that player is one of them (a work that goes on from a
            # variant left out is worse than one that begins again).
            return candidate["url"] in dub.left_out_links or (candidate["url"] not in offered and candidate["player"] in players)

        chain = row["chain"]
        active = chain[row["attempt"]] if row["attempt"] < len(chain) else None
        if active is not None and left_out(active):
            self.log.info("сериал %d, s%02de%02d: озвучка %s шла из варианта, который исключили, — снята", series_id,
                          row["season"], row["episode"], row["voice_label"])
            await self._retire_started(series_id, row)
            return
        kept = [each for each in chain if not left_out(each)]
        if len(kept) != len(chain):
            await self.repo.set_chain(row["id"], kept, kept.index(active) if active is not None else 0)

    async def _drop_without_reference(self, series_id: int, plan: planning.Plan, rows: list[dict]) -> None:
        """An episode that has no reference loses the voice-overs it had begun, as an episode that left the plan does."""
        root = await self._downloads_root()
        episodes = {(row["season"], row["episode"], row["part"]) for row in rows} - plan.with_reference - plan.undecided
        if episodes and not root:  # nowhere to look for what they hold: the naming of the folder brings them back
            self._deferred.add(series_id)
            return
        for season, episode, part in sorted(episodes):
            group = await self.repo.for_episode(series_id, season, episode, part)
            if group:
                await self._drop_orphans(series_id, season, episode, part, group, root)

    async def _forget(self, series_id: int) -> None:
        """Remove every row of a series that is gone, announcing each."""
        rows = await self.repo.for_series(series_id)
        await self.repo.delete_for_series(series_id)
        for row in rows:
            await self._announce(row, E.REMOVED)

    async def _recheck_ready(self, series_id: int) -> None:
        """A ready dub whose file vanished from the temporary directory is downloaded again."""
        for row in await self.repo.for_series(series_id):
            if row["status"] == S.READY and not await self._io(os.path.exists, row["temp_path"]):
                if row["file_id"] is not None:
                    await self.repo.file_lost(row["file_id"])
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
            for row in sorted(waiting, key=lambda r: (r["series_id"], r["season"], r["episode"], r["part"], r["id"])):
                episode = (row["series_id"], row["season"], row["episode"], row["part"])
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
        if chain[row["attempt"]].get("file"):
            await self._start_on_file(row, chain[row["attempt"]], root)
            return
        slot_rows = [each for each in await self.repo.for_series(row["series_id"])
                     if (each["season"], each["episode"], each["part"]) == (row["season"], row["episode"], row["part"])]
        candidate = chain[row["attempt"]]
        path = temp_path(root, row, dub_name(row, slot_rows, candidate), candidate)
        if storage.busy(path):  # an earlier removal of this name has not ended: a file written now could be lost
            self._when_free(path, row["series_id"])
            return
        if not await self.repo.claim_download(row["id"], path):
            return
        self._fetching.discard(row["id"])
        # Announced before the command goes out: a quick download could end first and this event would repeat its end.
        await self._announce(row)
        self.send_command("downloads.fetch", {
            "key": f"{DUB_KEY}{row['id']}", "url": candidate["url"], "path": path,
            "max_height": DUB_MAX_HEIGHT, "series_id": row["series_id"]})

    async def _start_on_file(self, row: dict, candidate: dict, root: str) -> None:
        """The candidate is a row that covers several episodes: its video is one file, downloaded once and read by the
        voice-overs of all the episodes it covers. The first to come sends the download; the others only link to it."""
        key = (candidate["file"]["source_id"], candidate["file"]["row_key"], candidate["url"])
        known = next((file for file in await self.repo.files_of(row["series_id"])
                      if (file["source_id"], file["row_key"], file["url"]) == key), None)
        if known is not None and known["temp_path"] and storage.busy(known["temp_path"]):
            self._when_free(known["temp_path"], row["series_id"])
            return
        file = await self.repo.join_file(
            row["id"], row["series_id"], key[0], key[1], key[2],
            lambda file_id: file_path(root, row["series_id"], file_id, candidate))
        if file is None:
            return
        self._fetching.discard(row["id"])
        await self._announce(row)  # before the command goes out, as for a download of its own
        if file["claimed"]:
            self.send_command("downloads.fetch", {
                "key": f"{FILE_KEY}{file['id']}", "url": candidate["url"], "path": file["temp_path"],
                "max_height": DUB_MAX_HEIGHT, "series_id": row["series_id"]})
        elif file["status"] == S.READY:
            self._kick(row["series_id"])

    async def on_fetch_started(self, env: Envelope) -> None:
        """The download left the queue of downloads and began: only now it is a download."""
        key = str(env.payload.get("key") or "")
        if key.startswith(FILE_KEY):
            file_id = int(key[len(FILE_KEY):])
            self._file_fetching.add(file_id)
            for row in await self.repo.by_file(file_id):
                if row["status"] == S.DOWNLOADING:
                    await self._announce(row)
            return
        if not key.startswith(DUB_KEY):
            return
        row = await self.repo.get(int(key[4:]))
        if row is not None and row["status"] == S.DOWNLOADING:
            self._fetching.add(row["id"])
            await self._announce(row)

    async def on_fetch_finished(self, env: Envelope) -> None:
        payload = env.payload
        key = str(payload.get("key") or "")
        if key.startswith(FILE_KEY):
            await self._on_file_finished(int(key[len(FILE_KEY):]), payload)
            return
        if not key.startswith(DUB_KEY):
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
            self._replan_deferred(row["series_id"])
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

    async def _on_file_finished(self, file_id: int, payload: dict) -> None:
        """The transfer of a file ended: the voice-overs that waited on it are ready, or each goes on to its next candidate."""
        self._file_fetching.discard(file_id)
        ok = bool(payload.get("ok"))
        error = str(payload.get("error") or reason("download_failed"))
        dependents = await self.repo.finish_file(file_id, ok, None if ok else error)
        if dependents is None:
            # An ending of a download that nothing waits for. When the file was released meanwhile, the cancel came too
            # late and the video is on the disk with no owner: its folder goes to the journal again.
            if ok and payload.get("path") and await self.repo.file(file_id) is None:
                await self._cleanup.discard(os.path.dirname(payload["path"]), kind=TREE)
            return
        file = await self.repo.file(file_id)
        if ok:
            self.log.info("сериал %d: файл склейки %d скачан, озвучек, ждавших его: %d", file["series_id"], file_id,
                          len(dependents))
            for row in dependents:
                await self._announce(row)
            self._kick(file["series_id"])
        else:
            for row in dependents:
                self.log.info("сериал %d, s%02de%02d: озвучка %s не скачана из %s: %s", row["series_id"], row["season"],
                              row["episode"], row["voice_label"], row["chain"][row["attempt"]]["player"], error)
                if row["attempt"] + 1 < len(row["chain"]):
                    await self.repo.advance(row["id"], error)
                    await self._start(await self.repo.get(row["id"]))
                else:
                    await self._fail(row, error)
        await self._release_files()
        self._replan_deferred(file["series_id"])

    def _withdraw(self, series_id: int, keys: list[str] | None) -> None:
        """The work given out under these keys (all of the series without keys) is not needed: whoever does it hears."""
        self.publish_event(WORK_WITHDRAWN, WorkWithdrawn(series_id=series_id, keys=keys).model_dump(mode="json"))

    def _cancel_transfer(self, file: dict) -> None:
        """No voice-over reads the file whose transfer is under way: the transfer is cancelled, the file is free at once
        and its folder is on the journal. A transfer that ends before the cancel arrives is met in `_on_file_finished`."""
        self._file_fetching.discard(file["id"])
        self.log.info("сериал %d: загрузка файла склейки %d отменена, он больше не нужен озвучкам", file["series_id"],
                      file["id"])
        self._withdraw(file["series_id"], [f"{FILE_KEY}{file['id']}"])

    async def _release_files(self) -> None:
        """Files that no voice-over reads any more go to the cleanup journal, and are carried out."""
        released = await self.repo.release_idle_files()
        for file in released:
            self.log.info("сериал %d: файл склейки %d больше не нужен озвучкам", file["series_id"], file["id"])
        if released:
            await self._cleanup.work_off()

    async def _fail(self, row: dict, error: str) -> None:
        await self.repo.mark_failed(row["id"], error)
        self.log.warning("сериал %d, s%02de%02d: озвучка %s не скачана ни из одного источника: %s",
                         row["series_id"], row["season"], row["episode"], row["voice_label"], error)
        await self._announce(row)
        self._kick(row["series_id"])
        self._replan_deferred(row["series_id"])

    def _replan_deferred(self, series_id: int) -> None:
        """A download ended where an episode with no reference waited for it: its voice-overs can go now."""
        if series_id in self._deferred:
            self._deferred.discard(series_id)
            self._want_plan(series_id)

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
            await self._tell_work(row["series_id"])
            return
        status = E(current["status"])
        if (event is E.STALE and status == S.PENDING) or (event is E.MOVED and status == S.PLACED):
            status = event
        self._publish_event(current, status, current["outcome"] or current["error"], epoch, rev)
        await self._tell_work(current["series_id"])

    async def _has_work(self, series_id: int) -> tuple[bool, set[Slot]]:
        """A voice-over a sync admitted and that is not at its end: work of the run of the series, and of its episode. A row
        a recount planned outside a sync is not admitted and is no work yet."""
        busy = {slot_of(row["season"], row["episode"], row["part"]) for row in await self.repo.for_series(series_id)
                if row["admitted"] and row["status"] not in FINAL}
        return bool(busy), busy

    async def on_work_get(self, env: Envelope) -> dict:
        return (await self._work.tell(int(env.payload["series_id"]))).model_dump()

    async def _tell_work(self, series_id: int) -> None:
        """The run of the series hears when this module's work of it begins or ends (the run owner is scan), and the
        status of the series shows what that work is at, step by step (`series_status`)."""
        await self._work.tell(series_id)
        flags = series_status(await self.repo.for_series(series_id))
        if self._status.get(series_id) != flags:
            self._status[series_id] = flags
            self.publish_event("series.status.contribution", {"source": "voices", "series_id": series_id, "flags": flags})

    def _is_fetching(self, row: dict) -> bool:
        """Whether the download behind a voice-over has begun: its own, or the one of the file it reads."""
        return row.get("id") in self._fetching or row.get("file_id") in self._file_fetching

    def _publish_event(self, row: dict, status: E, detail: str | None, epoch: int, rev: int) -> None:
        state = public_state(status, self._job_status.get(row.get("job_id")), self._is_fetching(row))
        self.publish_event("voices.dub.changed", DubEvent(
            series_id=row["series_id"], season=row["season"], episode=row["episode"], part=row["part"] or None,
            voice_key=row["voice_key"], voice_label=row["voice_label"], status=status, state=state,
            hint=public_hint(status, state, row.get("job_id"), _packed(row)), detail=parse_reason(detail), epoch=epoch,
            rev=rev, job_id=row.get("job_id"), file=sent_as(row),
            source_id=candidate_of(row).get("source_id"), player=candidate_of(row).get("player")).model_dump(mode="json"))

    async def _announce_difference(self, before: list[dict], after: list[dict]) -> None:
        """Rows that the planner created or took over are news for the interface like any other change."""
        known = {(row["season"], row["episode"], row["part"], row["voice_key"]): (row["status"], row["outcome"] or row["error"])
                 for row in before}
        for row in after:
            seen = (row["status"], row["outcome"] or row["error"])
            if known.get((row["season"], row["episode"], row["part"], row["voice_key"])) != seen:
                await self._announce(row)

    def _record(self, row: dict) -> DubRecord:
        state = public_state(row["status"], self._job_status.get(row["job_id"]), self._is_fetching(row))
        return DubRecord(**{field: row[field] for field in DubRecord.model_fields
                            if field in row and field not in ("state", "hint", "detail", "part")},
                         part=row["part"] or None, state=state,
                         hint=public_hint(row["status"], state, row["job_id"], _packed(row)),
                         detail=parse_reason(row["outcome"] or row["error"]),
                         source_id=candidate_of(row).get("source_id"), player=candidate_of(row).get("player"))

    async def on_clients(self, env: Envelope) -> None:
        """A new interface has no history: it gets every voice-over at once, stamped before the read."""
        if self.repo is None or not env.payload.get("count"):
            return
        epoch = await self._generation()
        rev = self._next_rev()
        items = [SnapshotItem(series_id=row["series_id"], job_id=row.get("job_id"),
                              file=sent_as(row),
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
        episodes = sorted({(row["season"], row["episode"], row["part"]) for row in rows})
        restart = False
        owed = False
        for season, episode, part in episodes:
            group = await self.repo.for_episode(series_id, season, episode, part)
            for row in group:
                if row["move_from"]:
                    await self._finish_move(row)
            group = await self.repo.for_episode(series_id, season, episode, part)
            current = references.get((season, episode, part))
            if current is None:
                if (season, episode, part) not in in_plan:
                    await self._drop_orphans(series_id, season, episode, part, group, root)
                continue
            reference, uid, identity, rev = current
            if await self._follow(series_id, season, episode, part, group, reference, uid, identity, rev):
                restart = True
                continue
            for row in group:
                if row["status"] == S.ALIGNED:
                    await self._place(row, reference)
            group = await self.repo.for_episode(series_id, season, episode, part)
            ready = [row for row in group if row["status"] == S.READY]
            if ready and not any(row["status"] in WORKING for row in group):
                owed = await self._submit(series, season, episode, part, reference, uid, ready, identity, rev) or owed
            await self._tidy(series_id, season, episode, part, root)
        await self._admit()
        await self._cleanup.work_off()
        await self._release_files()
        if restart:  # the reset voice-overs start over: their chains are read from the layout once more
            self._want_plan(series_id)
        if all(row["status"] in FINAL for row in await self.repo.for_series(series_id)):
            for base in (root, (await self._folders())["conform"]):
                if base:
                    await self._io(remove_if_empty, os.path.join(base, str(series_id)))
        return owed

    async def _reference_state(self, series: dict) -> tuple[set, dict[tuple[int, int, int], tuple[str, str, str, int | None]]]:
        """The planned episodes, and (file, media item, identity of its video, revision of its file) of the reference
        that is on disk, by episode and part.

        When a slot holds a superseded file and its winner, the winner is the reference."""
        reply = await self.request("downloads.episodes.get", {"series_id": series["id"]})
        in_plan, found = set(), {}
        for item in reply["items"]:
            slot = (item["season"], item["episode_start"], item["part"] or 0)
            in_plan.add(slot)
            if not item.get("final_filename"):
                continue
            path = os.path.join(series["save_path"], item["final_filename"])
            if not await self._io(os.path.isfile, path):
                continue
            if slot not in found or not item.get("superseded_by"):
                found[slot] = (path, item["unique_id"], container.identity(item.get("source_id"), item.get("row_key")),
                               item.get("file_rev"))
        return in_plan, found

    async def _follow(self, series_id: int, season: int, episode: int, part: int, group: list[dict],
                      reference: str, uid: str, identity: str = "", rev: int | None = None) -> bool:
        """Keep the tracks of an episode in step with its reference; True when they went stale. A track aligned to a video
        of the same identity (the same source and row, another resolution) stays valid and follows it (ЭТАП_11, п. 5).
        A reference downloaded again is a new file: every verdict on the old one (an alignment, a refusal) is given again,
        since the old file may not have been the video its row names (a download cut short)."""
        same = [row for row in group if row["ref_uid"] and row["ref_uid"] != uid
                and identity and row.get("aligned_to") == identity]
        for row in same:
            await self.repo.follow_reference(row["id"], uid, reference, rev)
            self.log.info("сериал %d, s%02de%02d: дорожка %s остаётся — то же видео в другом разрешении",
                          series_id, season, episode, row["voice_label"])
        if same:
            group = await self.repo.for_episode(series_id, season, episode, part)
        stale = [row for row in group if row["ref_uid"] and (row["ref_uid"] != uid or _another_file(row, rev))]
        if stale:
            for row in stale:
                # What the row owns, its job included, goes to the journal in the same transaction.
                await self.repo.reset(row["id"], reason("reference_downloaded_again" if row["ref_uid"] == uid
                                                        else "reference_replaced"))
            await self._cleanup.work_off()
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

    async def _drop_orphans(self, series_id: int, season: int, episode: int, part: int, group: list[dict],
                            root: str) -> None:
        """An episode left the plan: with its video gone, its set of tracks goes too, and a download of one of its
        voice-overs stops: nobody needs it any more."""
        for row in group:
            if row["ref_path"] and await self._io(os.path.isfile, row["ref_path"]):
                return
        for row in group:
            if row["status"] == S.DOWNLOADING and row["file_id"] is None:
                self._fetching.discard(row["id"])
                self._withdraw(series_id, [f"{DUB_KEY}{row['id']}"])
        folders = await self._episode_folders(group, root, series_id, season, episode, part)
        await self.repo.drop_episode(series_id, season, episode, part, folders)
        await self._cleanup.work_off()
        for row in group:
            await self._announce(row, E.REMOVED)
        self.log.info("сериал %d, s%02de%02d: серии нет в плане и видео нет — дорожки удалены",
                      series_id, season, episode)

    async def _episode_folders(self, group: list[dict], root: str, series_id: int, season: int, episode: int,
                               part: int) -> list[str]:
        """Everything of an episode that lies in the work folder: its voice-overs and the folder of the service."""
        folders = self._folders_of(group, episode_dir(root, series_id, season, episode, part))
        # Where the service worked is read from the rows (<episode folder>/_aligned/<key>), not from the settings now.
        made = {os.path.dirname(os.path.dirname(row["out_dir"])) for row in group if row["out_dir"]}
        return sorted({*folders, *made, await self._conform_episode(series_id, season, episode, part)} - {""})

    async def _settle_folders(self, group: list[dict], root: str, series_id: int, season: int, episode: int,
                              part: int) -> list[str]:
        """What of a settled episode is of no use: its voice-overs and the copy of the reference. The folders of the
        jobs stay with their records."""
        folders = self._folders_of(group, episode_dir(root, series_id, season, episode, part))
        service = {os.path.dirname(os.path.dirname(row["out_dir"])) for row in group if row["out_dir"]}
        service.add(await self._conform_episode(series_id, season, episode, part))
        return sorted({*folders, *(os.path.join(folder, REF_DIR) for folder in service if folder)})

    async def _clear_finished(self) -> bool:
        """The owner's choice: a job whose result is taken leaves the service's history at once."""
        try:
            reply = await self.request("conform.settings.get", {})
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
            if await self._io(os.path.isdir, out_dir) and await self._cleanup.discard(out_dir, kind=TREE):
                await self._io(prune, out_dir)

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
                job = AlignmentJob.model_validate(await self.request("conform.job.get", {"job_id": job_id}))
            except (BusRequestError, asyncio.TimeoutError, ValidationError):
                return  # the service could not say: the next time it is reachable asks again
            if job.status is JobStatus.GONE:
                await self._release_outputs(await self.repo.by_job(job_id))

    @staticmethod
    def _folders_of(group: list[dict], fallback: str) -> list[str]:
        """Episode folders of voice-overs as they were made, not as the settings say now."""
        found = {os.path.dirname(row["temp_path"]) for row in group if row["temp_path"] and row["file_id"] is None}
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

    async def _submit(self, series: dict, season: int, episode: int, part: int, reference: str, uid: str,
                      ready: list[dict], identity: str = "", rev: int | None = None) -> bool:
        """Send the ready voice-overs of an episode as one job; True when the service was certainly not reached
        and another try is owed. What may have reached it is carried by the recovery from the key."""
        label = f"{series['name']} {slot_tag(season, episode, part)}"
        ready = await self._with_files(ready, label)
        if not ready:
            return False
        conform_dir = await self._conform_episode(series["id"], season, episode, part)
        if not conform_dir:
            self.log.warning("%s: выравнивание не начато: рабочая папка не задана в настройках сервера выравнивания", label)
            return False
        folders = await self._workspace(dub_folder=conform_dir)
        key = uuid.uuid4().hex[:12]
        # A job works in folders of its own: an earlier job that is not confirmed stopped can not reach its files.
        out_dir, cache_dir = os.path.join(folders["out_dir"], key), os.path.join(folders["cache_dir"], key)
        ref_dir = os.path.join(conform_dir, REF_DIR, key)
        copy = os.path.join(ref_dir, reference_file(series["id"], season, episode, part, reference))
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
        await self.repo.mark_submitting([row["id"] for row in ready], key, uid, reference, out_dir, identity, rev)
        # A reference that is a container already holds voice-overs: the alignment listens to the video's own sound.
        held = await container.contents(reference)
        self._sending.add(key)
        try:
            return await self._send(label, key, ready, copy, out_dir, cache_dir, held.own_track if held else 0)
        finally:
            self._sending.discard(key)
            self._recovery.poke("cleanup:")  # what waited for the answer to this submission may go on now
            self._recovery.poke("submission:")

    async def _send(self, label: str, key: str, ready: list[dict], reference: str, out_dir: str,
                    cache_dir: str, ref_atrack: int = 0) -> bool:
        """Send the job and take its outcome: until this returns the service may still take the job."""
        try:
            reply = SubmitReply.model_validate(await self.request("conform.submit", {
                "ref": reference, "dubs": [row["temp_path"] for row in ready],
                "out_dir": out_dir, "cache_dir": cache_dir,
                "label": label, "key": key, **({"ref_atrack": ref_atrack} if ref_atrack else {})}))
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
            # The service refused the request, not the voice-overs: nothing was judged, so they are offered again by the next sync.
            for row in ready:
                await self.repo.mark_failed(row["id"], reply.reason)
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
            reply = await self.request("conform.job.find", {"key": key})
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
        if not await self._cleanup.clear(job_id):
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
            if row["file_id"] is not None:
                await self.repo.file_lost(row["file_id"])
            await self.repo.mark_pending(row["id"], reason("file_vanished"))
            await self._announce({**row, "status": S.PENDING}, E.STALE)
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
            job = AlignmentJob.model_validate(await self.request("conform.job.get", {"job_id": job_id}))
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
        result = result_for(job, row["temp_path"])
        if job.status is not JobStatus.DONE and result is None:
            # The job ended before this pair was reached (the service lost its worker, the job was stopped): the end of the
            # job is the reason. A pair the service had finished keeps its result, which is read like any other.
            error = job_failure(job)
            await self.repo.mark_failed(row["id"], error)
            self.log.warning("сериал %d, s%02de%02d: озвучка %s не выровнена: %s", row["series_id"],
                             row["season"], row["episode"], row["voice_label"], describe(error))
            await self._announce(row)
            return
        verdict = refusal(result)
        conform_dir = await self._conform_episode(row["series_id"], row["season"], row["episode"], row["part"])
        out_dir = row["out_dir"] or (await self._workspace(dub_folder=conform_dir))["out_dir"]
        track = track_of(out_dir, result) if verdict is None else None
        if verdict is None and not await self._io(os.path.isfile, track):
            verdict = reason("output_missing")
        if verdict is None:
            await self.repo.mark_aligned(row["id"], track)
            if told(result):
                self.log.info("сериал %d, s%02de%02d: озвучка %s выровнена, сервис сообщил: %s", row["series_id"],
                              row["season"], row["episode"], row["voice_label"], told(result))
        elif turned_down(result) and (following := planning.other_video(row["chain"], row["attempt"])) is not None:
            # The verdict is on the video of this source, not on the voice-over: the same video on other pages is passed
            # over, another video may be the episode.
            await self.repo.turn_to_next(row["id"], verdict, following)
            skipped = following - row["attempt"] - 1
            self.log.warning("сериал %d, s%02de%02d: озвучка %s из %s отклонена: %s — беру следующий источник %s%s",
                             row["series_id"], row["season"], row["episode"], row["voice_label"],
                             row["chain"][row["attempt"]]["player"], describe(verdict), row["chain"][following]["player"],
                             f" (тот же ролик на других страницах пропущен: {skipped})" if skipped else "")
            await self._announce(row)
            await self._start(await self.repo.get(row["id"]))
            return
        elif turned_down(result):
            await self.repo.mark_rejected(row["id"], verdict)
            self.log.warning("сериал %d, s%02de%02d: озвучка %s отклонена: %s", row["series_id"],
                             row["season"], row["episode"], row["voice_label"], describe(verdict))
        else:
            await self.repo.mark_failed(row["id"], verdict)
            self.log.warning("сериал %d, s%02de%02d: озвучка %s не выровнена: %s", row["series_id"],
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

    async def _tidy(self, series_id: int, season: int, episode: int, part: int, root: str) -> None:
        """The episode is settled: its temporary files are of no use any more. What the service wrote stays while the
        record of its job is in the service's history, since the plots are shown from there."""
        group = await self.repo.for_episode(series_id, season, episode, part)
        if not group or any(row["status"] not in FINAL for row in group):
            return
        folders = await self._settle_folders(group, root, series_id, season, episode, part)
        if not (any(row["temp_path"] or row["aligned_path"] for row in group)
                or any([await self._io(os.path.isdir, folder) for folder in folders])):
            return
        await self.repo.settle_episode(series_id, season, episode, part, folders)
        if await self._clear_finished():
            await self.repo.hand_over_jobs(series_id, season, episode, part)
        await self._cleanup.work_off()
        await self._release_gone(await self.repo.for_episode(series_id, season, episode, part))
        self.publish_event("voices.episode.settled",
                           {"series_id": series_id, "season": season, "episode": episode, "part": part or None})


    async def on_series_deleted(self, env: Envelope) -> None:
        series_id = env.payload["series_id"]
        self._deleted.add(series_id)
        self._file_fetching -= {file["id"] for file in await self.repo.files_of(series_id)}
        self._withdraw(series_id, None)
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
        await self._cleanup.work_off()
        await self._admit()
        self.publish_event("series.purged", {"series_id": series_id, "owner": self.name})


def series_status(rows: list[dict]) -> dict[str, bool]:
    """The contribution of the voice-overs to the status of their series, by the step of each admitted one at work: its
    file is fetched (`downloading`), its job is at the service (`aligning`), or it waits — for its turn, its reference,
    its placing (`waiting`). A voice-over at its end or not admitted by a sync is no work and shows nothing."""
    steps = {row["status"] for row in rows if row["admitted"] and row["status"] not in FINAL}
    return {"downloading": S.DOWNLOADING in steps, "aligning": S.ALIGNING in steps,
            "waiting": bool(steps - {S.DOWNLOADING, S.ALIGNING})}


def _packed(row: dict) -> bool:
    """A placed track with no file of its own lives inside the container of its episode (voices.tracks.packed)."""
    return row.get("status") == S.PLACED and not row.get("output_path")
