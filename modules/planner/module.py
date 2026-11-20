"""Module planner: the plan of a video series and its sync (stage 12, the table of an episode).

Owns the table of the asset of every video series (plan_assets: the containers of its library, each with the provenance of
its video and tracks), the attempts that failed (plan_failures), the commands «Удалить» that wait (plan_commands) and the
syncs under way (plan_runs). The plan itself is calc/plan: a pure function of the tables and the priorities; what the window
shows of it is calc/view (the view, the cards of the packing); the steps of a sync and the counting over them are
calc/plan/steps.py. This module reads the inputs over the bus, runs the executor (modules/planner/executor.SeriesSync) with
the service's own workers, and keeps the table. A count is made in steps, with the questions between them: the neighbours
are asked (the card, the layout, the settings of the voice-overs), the pure step is one call of calc in the pool, the rule of
names is asked between the first plan and the second, the view is counted last. Every topic the module handles, asks or tells is
a record of `core.topics`.

Inputs, read when a sync starts and frozen until it ends: the layout of the composition (`composition.get`: its rows, the
levers of every episode and compilation, the order of the voice-overs), the settings of the voice-overs (`voices.settings.get`),
the card of the series (`catalog.series.get`: the order of its sources, its folder, the episodes switched off) and the asset.

Steps of the graph, each through its owner:
  download (a video, a voice-over, a compilation): the command `downloads.fetch` into the work folder of the alignment
      service; its end is the event `downloads.fetch.finished` of the key.
  align: one `conform.submit` per episode — its reference once and every voice-over of it that came, once none of them
      is still being downloaded (a voice-over that did not come holds none back); a voice-over out of a file of the library
      by its track (`dub_atracks`); its end is `conform.job.finished`. A result is taken by `ok`. A plan made again
      while the episode waits (a voice-over failed and its next source is downloaded) keeps it one job: the voice-overs
      of the plan in force go together (modules/planner/executor.Batch); only a job already sent is not joined.
      Every source of a job (its video, its voice-overs, the episode and the picture of a cut) is in the job's own folder,
      a hard link to the file: the service keeps its temporary files beside the sources of a job and removes all of them
      beside the sources of a job whose record goes, so two jobs never share a folder of sources.
  cut: `conform.reverse` with the single video of the episode; its end is `conform.job.finished`.
  A job is keyed by its sync (plan_runs.started_at) and what it does: a sync taken up after a stop finds its job by the
      key (conform.job.find) and takes its end instead of sending it again. When a job ends, what it was given (copies,
      cache) goes; its folder with what it wrote (tracks, plots) stays while its record is in the service's history
      (conform.job.keys), and goes with the record (conform.job.removed, the end of a sync, the start). The setting
      conform.clear_finished takes a job that ended with every result out of the history at the end of its sync.
  A result of the service is also kept (modules/planner/kept.py): a hard link of the file its job wrote, under
      `<work folder of the service>/conform/kept/<series>/<episode>/` with the timeline it was aligned to and the row of its
      voice-over. A sync that falls before its container is in place (the build fails, the process stops) or a job that is
      taken out of the history would lose what hours of the service's work made; a sync that asks the same alignment takes
      it from the keep — the job is not sent and the voice-over not downloaded. The set of an episode goes once its container
      is in place (modules/planner/executor `placed`), when a sync that covers the episode has no container to build for it, and with
      the series. Nothing is copied: the data is the file of the job while it lasts and the keep's alone after.
  build and apply: here, by modules/planner/files (the container with its provenance, the journal in the folder of the series).
A failure the service judged is recorded and the plan is counted again from the same snapshot; a failure of the moment
(the network, a service that is down, an answer that never came) is no record and is tried at the next sync. A pair the
service fell on (its result is there, with no file and no decision of its gates: an exception in it) judged nothing, so it is
no record either, but its source is passed over for this sync like a download that did not come, and the plan goes on with
the next source; where there is none, the episode is built without that voice-over and the next sync tries it again.

Queries:
  planner.sync {series_id, download?, season?, episode?} -> {accepted}: the sync of a series is recorded and starts; it is
      a work of the run of the sync (scan), told by `work.series.state` (owner `planner`). Without download no step runs:
      only what needs no work is applied (names, `.off`, removals the plan owes). With a season (and an episode of it) the
      sync covers it alone (SyncScope): the plan is counted for the whole series and shown whole, and what is carried out is
      the groups of the plan that hold a place of the scope — the episodes and files that share work go together (the
      episode of a compilation brings the compilation, its download and every cut on it); nothing of the other groups is
      done, applied, removed or hidden. The scope is written with the run (plan_runs.scope): a restart takes it up as it was.
  planner.sync.cancel {series_id} -> {cancelled}: the user stops the sync of the series: its downloads (work.withdrawn),
      its jobs of the service (cancelled, taken out of the history), the build aside stop; the episodes it applied by
      then stay applied (each group of the plan is put in place and written as soon as its own steps have ended),
      nothing more of it is, and it is not taken up again at the start. A journal of the library under way is not cut:
      the stop waits for its end.
  planner.work.get {series_id} -> WorkState.
  planner.series.get {series_id} -> PlannerView: the asset, the goal of every episode, the failures and the commands. The
      models (`PlannerView`, `WorkState`, and the `Reference`s of planner.references.get) go as they are, immutable; the
      gateway writes them for the window, and `planner.series.changed` carries the same view as a packet.
  planner.delete {series_id, kind: video | track, name, dub?} -> {recorded}: the command «Удалить» of a file or of a track
      in it, carried out by the next sync (a video goes only for another video of its episode).
  planner.delete.cancel {series_id, kind, name, dub?} -> {dropped}.
  planner.failures.forget {series_id, season?, episode?, voice?, kind?} -> {reset}: the user's reset of the failed attempts —
      of the whole series, or only of the level named (PlanForget: a season, an episode of it, a voice-over group, the cuts);
      the attempts of the other levels stay, and what the plan chose because they failed stays chosen.
  planner.pack.get {} -> {tasks, journals}: the cards of the packing and the journals being carried out. planner.pack.remove {id} -> {removed}: the user removes a card
      that stayed after its sync.
  planner.files.get {series_id} -> {names}: the files of the asset, relative to the folder of the series.
  planner.downloaded_counts {} -> {counts: {series_id: n}}: the episodes every series holds and shows, specials aside.
  planner.seasons {series_id} -> {seasons}: the seasons its files are of.
  planner.references.get {series_id} -> (Reference, ...): the reference of every episode and why it is that one, told by the
      plan (calc/plan/marks): the window shows the choice the sync makes.
Events out:
  planner.series.changed PlannerView — the asset, the plan, a failure or a command of a series changed.
  series.purged {series_id, owner} — the tables of a deleted series are cleaned.
  series.downloaded.changed {series_id, count} — the number of the episodes the library holds, after a sync.
  series.status.contribution {source: planner, series_id, flags: {downloading, aligning, packing}} — what the sync of the
      series is at (the card of the series), told when it changes; all false when the sync ends.
  work.series.state (owner `planner`).
  planner.pack.changed {tasks: [PackCard]} — the cards of the packing (core/contracts/packing.py; SSE pack_queue_update,
      on the beat of core/clock while a container is under way, and when an interface connects): every container a sync
      builds — its stages with their time (queue, build by ffmpeg with its progress, the result read
      back, the journal that puts it in place), its tracks as numbers of the season with where each comes from, what
      changed against the file before. At the end of the sync only a failed card stays (table pack_cards) until the user
      removes it or the next sync of the series begins.
  planner.journal.changed {journals: [PackJournal]} — the journal of each series being carried out: how many steps, how many
      done, the step under way and its file, whichever episode's file it is (SSE pack_journal_update); a journal goes
      when its last step is done.
  planner.sync.trace {series_id, run, kind, download, data} — what a sync does, as it happens, for the dump of the run
      (modules/runlog): `plan` — the inputs whole (the online rows, the asset, the choices, the commands, the failures)
      and the plan made of them, every round; `step` / `done` — a step of the graph begins / ends with its outcome
      (made, failed, passing with its reason, left behind a failed step); `apply` — a group applied or left, and why.
Events in: gateway.sse.clients, downloads.fetch.finished, conform.job.finished, conform.job.removed, system.started (syncs under way are taken up
  again, a journal a stop cut short is carried out), series.deleted, composition.changed, voices.settings.changed and
  series.updated with the episodes switched off (the plan is counted again from the tables and the window told: nothing on
  disk changes between syncs).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from dataclasses import asdict

from calc.plan.codec import (command_of_key, containers_json, failure_of_key, key_of_command, key_of_failure,
                             stored_command)
from calc.plan.inputs import assets_table, layout_inputs
from calc.plan.names import filename_of, namer_over, wanted_names
from calc.plan.planner import Plan, cut_video, name_of, video_op
from calc.plan.steps import DOWNLOADS, Crashed, Made, Passing, fate_of, judged, video_of
from calc.plan.tables import Track, Video, label_of
from calc.view.body import Held, packet_of, standing_of, view_body
from calc.view.packing import card_id
from calc.view.view import placed
from core import compute, storage
from core.clock import beat_while
from core.contracts import (CompositionGetRequest, DownloadNeed, DownloadVariant, PlanDelete, PlanForget, PlannerView,
                            Reference, SeriesSource, SourceType, SubmitOutcome, SubmitReply, SyncScope, job_dir, job_key)
from core.contracts.torrent import fonts_of, parse_link
from core.contracts.work import WorkState, WorkWithdrawn
from core.dump import plain
from core.envelope import Envelope
from core.ffprobe import END_SLACK_S, duration_of
from core.fsnames import sanitize_name
from core.module import NO_HANDLER, BaseModule, BusRequestError
from core.reasons import describe, reason
from core.topics import (CATALOG_SERIES_GET, CATALOG_SERIES_LIST, CATALOG_SERIES_TOUCH_SCAN_TIME, COMPOSITION_CHANGED,
                         COMPOSITION_GET, CONFORM_CANCEL, CONFORM_JOB_FIND, CONFORM_JOB_FINISHED, CONFORM_JOB_GET,
                         CONFORM_JOB_KEYS, CONFORM_JOB_REMOVED, CONFORM_REVERSE, CONFORM_SETTINGS_GET, CONFORM_SUBMIT,
                         CONFORM_WORKSPACE, DOWNLOADS_FETCH, DOWNLOADS_FETCH_FINISHED, DOWNLOADS_WITHDRAW,
                         GATEWAY_SSE_CLIENTS, PLANNER_DELETE, PLANNER_DELETE_CANCEL, PLANNER_DOWNLOADED_COUNTS,
                         PLANNER_FAILURES_FORGET, PLANNER_FILES_GET, PLANNER_JOURNAL_CHANGED, PLANNER_PACK_CHANGED,
                         PLANNER_PACK_GET, PLANNER_PACK_REMOVE, PLANNER_REFERENCES_GET, PLANNER_SEASONS,
                         PLANNER_SERIES_CHANGED, PLANNER_SERIES_GET, PLANNER_SYNC, PLANNER_SYNC_CANCEL,
                         PLANNER_SYNC_TRACE, PLANNER_WORK_GET, RULES_FORMAT_FILENAME, SERIES_DELETED,
                         SERIES_DOWNLOADED_CHANGED, SERIES_PURGED, SERIES_STATUS_CONTRIBUTION, SERIES_UPDATED,
                         SYSTEM_STARTED, TORRENTS_RELEASE_DROP_FILES, TORRENTS_RELEASES_GET, VOICES_SETTINGS_CHANGED,
                         VOICES_SETTINGS_GET, WORK_SERIES_STATE, WORK_WITHDRAWN, Topic)

from .executor import SeriesSync
from .files import Library, as_in_file
from .kept import Kept
from .packing import PackBook
from .repository import PlannerRepository

PLANNER = "planner"
# A safeguard of the copy of a video into the work folder of the alignment service: a 4K episode over the network.
COPY_TIMEOUT_S = 1800.0
ENDED = ("done", "failed", "cancelled", "gone")  # the states of a job that ended (conform.job.get)
WORK = "planner"  # the folder of the planner's work inside the work folder of the alignment service
# The containers built at one time, whatever series they are of: the episodes of a sync are built as each is ready, the
# machine of the service holds 4 GB and the library lies on a network disk. A container that waits is `queued` in its card.
BUILDS_AT_ONCE = 2


# Every download but the video of the reference is taken not above this height (the owner's rule, stage 10).
OTHER_MAX_HEIGHT = 720

# What the view of a series is counted from — the questions the planner asks its neighbours while it counts it — and the
# event that tells that the answer changed: one table, so that an input added to the view is an input whose change counts
# the view again. Its own tables (the asset, the failures, the commands) are written by the module itself, which counts the
# view again after it writes them.
VIEW_INPUTS: dict[Topic, Topic] = {
    CATALOG_SERIES_GET: SERIES_UPDATED,
    COMPOSITION_GET: COMPOSITION_CHANGED,
    VOICES_SETTINGS_GET: VOICES_SETTINGS_CHANGED,
    RULES_FORMAT_FILENAME: SERIES_UPDATED,  # the rule of names reads the card and the numbers of the episode
}


class BusSync(SeriesSync):
    """The sync of one series with the service's workers (see the module)."""

    def __init__(self, module: "PlannerModule", series: dict, download: bool, scope: SyncScope | None = None) -> None:
        super().__init__()
        self.m = module
        self.series = series
        self.download = download
        self.scope = scope
        self.holds = None if scope is None else scope.holds
        self.files = Library(series["save_path"])
        self.downloads_dir = ""
        self.conform_dir = ""
        self.kept: Kept | None = None  # the alignments this series keeps until their containers are in place
        self.made: Plan | None = None  # the plan in force: what a step asks about its neighbours
        self._aligners: tuple[Plan | None, dict] = (None, {})
        self.download_names: dict[str, str] = {}  # the name of the file of a download, by the step that took it
        # The names the rule of the series gave, kept by the module for the card they were given for: a view of the window
        # and a sync of the same card ask none of them again.
        self.names: dict[tuple, str] = module.names_of(series)
        self.namer = namer_over(self.names)
        self.told = False  # the window was shown the plan this sync froze
        self.flags: dict[str, int] = {}  # the steps under way by what they are, for the card of the series
        self.run_mark = ""  # when the sync under way began (plan_runs): the same after a stop, so its jobs are found again
        self.jobs: list[tuple[str, bool]] = []  # the jobs of the service this sync took the end of: (number, all ok)
        # The keys of every job of the service this sync gave out or took up, written before it is sent: a stop finds
        # each by its key (an answer to the sending that has not come yet is not waited for) and cancels it.
        self.keys: set[str] = set()
        self.committing = False  # the end of the sync is being written: a stop waits for it
        # A VK video whose stream request fails may be handed to the downloader itself (as the service always did).
        # The page of every source: a row of a torrent source is taken from the folder of its release on that page.
        self.source_urls = {each.id: each.url for each in map(SeriesSource.model_validate, series.get("sources", []))}
        self.vk_sources = {each.id for each in map(SeriesSource.model_validate, series.get("sources", []))
                           if each.source_type == SourceType.VK_VIDEO}
        # The marks of the sources as the window «Выход» shows them: the place in the list and the colour of each.
        ordered = sorted(map(SeriesSource.model_validate, series.get("sources", [])), key=lambda each: (each.position, each.id))
        self.source_marks = {each.id: (place, each.color) for place, each in enumerate(ordered, start=1)}
        self.voice_order: dict[int, tuple[str, ...]] = {}  # the order of the voice-overs of each season (choices.groups)

    async def plan_of(self, inputs):
        """The plan of the sync; the first one of it is the plan the window shows while the sync runs."""
        made = self.made = await super().plan_of(inputs)
        if self.download and not self.told:
            self.told = True
            await self.m._publish_view(self.series["id"], self, made, inputs)
        elif self.download:
            # A plan made again (a failure, a length): the window shows the plan the sync works by now.
            self.m._views[self.series["id"]] = (made, inputs)
            self.m._ask_recount(self.series["id"])
        if self.download:
            self.m.packing.planned(self.series, self.run_mark, made, self.containers, self.voice_order, self.source_marks)
            self.m.pack_going()
        return made

    async def left(self, made, group, missing) -> None:
        self.m.packing.waited([card_id(self.series["id"], slot) for slot in group.built])
        self.m.pack_publish()

    async def trace(self, kind: str, data: dict) -> None:
        # The tables of a plan of a big series are large: they are made plain off the loop.
        body = await compute.run(plain, data) if kind == "plan" else plain(data)
        self.m.publish_event(PLANNER_SYNC_TRACE, {"series_id": self.series["id"], "run": self.run_mark, "kind": kind,
                                                    "download": self.download, "data": body})

    async def prepare(self) -> None:
        folders = await self.m.request(CONFORM_WORKSPACE, {})
        if folders.get("downloads") and folders.get("conform"):
            self.downloads_dir = os.path.join(folders["downloads"], WORK, str(self.series["id"]))
            self.conform_dir = os.path.join(folders["conform"], WORK, str(self.series["id"]))
            self.kept = Kept.of(folders["conform"], self.series["id"])

    async def namer_for(self, made: Plan):
        """The series' own rule of names (`rules.format_filename`), asked once for every goal the names do not hold yet.
        The namer of the sync when no name was added (the plan made with it stands); a new one when names came. A name is
        kept by the request it answered, whole (the card and the item): what the rule is given is what tells two names
        apart, and nothing else."""
        wanted = wanted_names(made.goals, self.names)
        for key, (slot, video, item) in wanted.items():
            off = key[-1]
            try:
                made_name = (await self.m.request(RULES_FORMAT_FILENAME, {"series": self.series, "media_item": item}))["filename"]
                self.names[key] = filename_of(made_name, off)
            except (BusRequestError, KeyError):
                self.names[key] = name_of(slot, video.snapshot, off)  # the rule refused: the planner's own name
            except asyncio.TimeoutError:
                continue  # no answer is no word of the rule: its own name stands for now, and the rule is asked again
        return namer_over(self.names) if wanted else self.namer

    # --- the steps -------------------------------------------------------------------------------------------------

    def _flag(self, name: str, step: int) -> None:
        """What the work of the series is at, for the card of the series: told when it changes."""
        self.flags[name] = self.flags.get(name, 0) + step
        self.m.contribute(self.series["id"], {each: count > 0 for each, count in self.flags.items()})

    async def do(self, op, results):
        if not self.download or not self.downloads_dir:
            raise Passing  # a sync without download, or no work folder named: nothing is done, nothing recorded
        flag = "downloading" if op.kind in DOWNLOADS else "aligning" if op.kind in ("align", "cut") else None
        if flag is None:
            return await self._step(op, results)
        self._flag(flag, 1)
        try:
            return await self._step(op, results)
        finally:
            self._flag(flag, -1)

    async def _step(self, op, results):
        try:
            if op.kind in DOWNLOADS:
                return await self._download(op)
            if op.kind == "cut":
                return await self._cut(op, results)
            if op.kind == "build":
                return Made(op.subject)
        except Passing:
            raise
        except Exception as exc:  # noqa: BLE001 - one step that breaks is a step of the moment, not the end of the sync
            self.m.log.exception("сериал %d: шаг %s не выполнен", self.series["id"], op.id)
            raise Passing(reason("internal_error", cause=str(exc))) from None
        raise AssertionError(op.kind)

    async def do_batch(self, ops, results):
        """The alignment of an episode is one job of the service: its reference once, every voice-over of it that came
        (the reference is what the service works out first, for all of them)."""
        if ops[0].kind != "align":
            return await super().do_batch(ops, results)
        if not self.download or not self.downloads_dir:
            return {op.id: Passing() for op in ops}
        self._flag("aligning", 1)
        try:
            return await self._align(ops, results)
        except Passing as passing:
            return {op.id: passing for op in ops}
        except Exception as exc:  # noqa: BLE001 - the job that breaks is a step of the moment, not the end of the sync
            self.m.log.exception("сериал %d: выравнивание %s не выполнено", self.series["id"], ops[0].batch)
            return {op.id: Passing(reason("internal_error", cause=str(exc))) for op in ops}
        finally:
            self._flag("aligning", -1)

    def _key(self, op_id: str) -> str:
        return job_key(self.series["id"], hashlib.md5(op_id.encode()).hexdigest()[:12])

    def _download_name(self, op, row, key: str) -> str:
        """The file of a download is named for people — the series, the episode, what it is: the queue of the downloads,
        the queue of the alignment service and the log show it. A name another step of the sync took gets its key."""
        series = self.series.get("name_en") or self.series["name"]
        episode = label_of(op.slot) if op.slot else f"s{row.season:02d}e{row.start:02d}-e{row.end:02d}"
        picture = f"компиляция {row.player} {row.height}p" if row.compilation else f"видео для разбора {row.player} {row.height}p"
        what = {"download_video": f"видео {row.player} {row.height}p", "download_dub": f"озвучка {row.group or row.label}",
                "download_picture": picture}.get(op.kind, op.kind)
        name = sanitize_name(f"{series} {episode} — {what}")
        if self.download_names.setdefault(name, op.id) != op.id:
            name = f"{name} {key.split(':')[-1]}"
        return name + ".mp4"

    def _telemetry(self, op, row, cap: int | None) -> dict:
        """What a download is for and which variant it takes (core/contracts/downloads.py): every variant the plan takes
        for one need is one card of the queue, in the marks of the window «Выход»."""
        kind = {"download_video": "video", "download_dub": "dub"}.get(op.kind, "picture")
        episode = label_of(op.slot) if op.slot else f"s{row.season:02d}e{row.start:02d}-e{row.end:02d}"
        order = self.voice_order.get(row.season, ())
        group = row.group if kind == "dub" else None
        need = DownloadNeed(key=f"{self.series['id']}:{episode}:{kind}:{group or ''}", series_id=self.series["id"],
                            series_name=self.series.get("name") or "", sync_started=self.run_mark, episode=episode,
                            kind=kind, group=group, voice_number=order.index(group) + 1 if group in order else None, cap=cap)
        place, color = self.source_marks.get(row.id.source_id, (0, 0))
        variant = DownloadVariant(source_id=row.id.source_id, source_n=place, source_color=color, place=row.source,
                                  player=row.player, player_label=row.player_label or row.player, height=row.height or None)
        return {"need": need.model_dump(mode="json"), "variant": variant.model_dump(mode="json")}

    async def _height_cap(self, op) -> int | None:
        """The highest a download may be: a single video is taken whole — the video of the reference, and the timeline a
        cut is laid on, which gives the cut its edges and its sound (stage 14); a voice-over not above 720p (the owner's
        rule, stage 10), it only gives its sound; the picture of a cut at the height of the setting
        `voices.cut_max_height` (none: the best)."""
        if op.kind == "download_picture":
            height = (await self.m.reply_of(VOICES_SETTINGS_GET, {})).cut_max_height
            return height or None
        if op.kind == "download_dub":
            return OTHER_MAX_HEIGHT
        return None

    async def _download(self, op):
        row = op.subject
        if op.kind == "download_dub" and await self._aligned_kept(op):
            return Made(row)  # every alignment it is for is kept: its file is not needed
        if parse_link(row.link) is not None:
            return await self._take(op, row)
        key = self._key(op.id)
        path = os.path.join(self.downloads_dir, self._download_name(op, row, key))
        await storage.call(os.makedirs, self.downloads_dir, exist_ok=True)
        cap = await self._height_cap(op)
        finished = await self.m.fetch(key, {"key": key, "url": row.link, "path": path, "series_id": self.series["id"],
                                            "whole": op.kind == "download_video",
                                            "ytdlp": row.id.source_id in self.vk_sources,
                                            **({"max_height": cap} if cap else {}),
                                            **self._telemetry(op, row, cap)})
        if not finished.get("ok"):
            if finished.get("transient"):
                self.m.log.warning("сериал %d: загрузка %s не удалась, повторится при следующей синхронизации: %s",
                                   self.series["id"], op.id, describe(finished.get("error") or ""))
                raise Passing(reason("download_not_done", cause=finished.get("error") or ""))
            self.m.log.warning("сериал %d: загрузка %s не удалась, записано как неудача: %s", self.series["id"], op.id,
                               describe(finished.get("error") or ""))
            return None
        if op.kind == "download_video":
            return Made(Video(row.id, row.snapshot(), row.slot, row.timeline), path, retimed=await self._retimed(row.id, path))
        if op.kind == "download_dub":
            return Made(row, path)
        return Made(None, path)

    async def _take(self, op, row):
        """A row of a torrent source is not downloaded (stage 15, И-4): its file lies in the folder of its release, and the
        step reads it there. Nothing is copied, and the file is never removed by the sync: it is the release's."""
        release, video, file, stream = parse_link(row.link)
        url = self.source_urls.get(row.id.source_id)
        reply = await self.m.request(TORRENTS_RELEASES_GET, {"url": url}) if url else {}
        kept = next((each for each in reply.get("releases") or [] if each["release"] == release), None)
        if kept is None or kept["state"] != "ready" or not kept.get("folder"):
            raise Passing(reason("torrent_not_ready", release=release))
        video_path, sound_path = os.path.join(kept["folder"], video), os.path.join(kept["folder"], file)
        for path in {video_path, sound_path}:
            if not await storage.call(os.path.exists, path):
                raise Passing(reason("torrent_file_missing", path=path))
        sound = sound_path if file != video else None
        if op.kind == "download_video":
            return Made(Video(row.id, row.snapshot(), row.slot, row.timeline), video_path, sound=sound, stream=stream)
        if op.kind == "download_dub":
            return Made(row, video_path, sound=sound, stream=stream)
        if op.kind == "take_subtitles":  # the n-th subtitles of its file, with the fonts beside it (DESIGN.md 7.1, 7.1.1)
            fonts = tuple(os.path.join(kept["folder"], path)
                          for path in fonts_of((each["path"] for each in kept.get("files") or []), file))
            return Made(None, video_path, sound=sound, stream=stream, fonts=fonts)
        return Made(None, video_path)

    async def _retimed(self, row_id, path: str) -> bool:
        """The length of a row's video is known only once it is downloaded: a video the library holds of the same row
        whose length is another one is another timeline (the host cut or recut it), and what is aligned to it before is
        not in step with what came."""
        held = [each for each in self.containers.values() if each.video.id == row_id and each.video.cut_of is None]
        if not held:
            return False
        now = await duration_of(path)
        for each in held:
            before = await duration_of(self.files.path(each.name))
            if now and before and abs(now - before) > END_SLACK_S:
                self.m.log.warning("сериал %d: видео строки %s скачано длиной %.1f с, в файле %s было %.1f с — "
                                   "задача перепланирована на новую длину", self.series["id"], row_id.row_key, now,
                                   each.name, before)
                return True
        return False

    async def _shed(self, folder: str, keep: str) -> None:
        """A job ended: what it was given (the copies, its cache) goes; what it wrote (the tracks, the plots) stays while
        its record is in the service's history (PlannerModule.sweep)."""
        def shed() -> None:
            for name in os.listdir(folder) if os.path.isdir(folder) else ():
                path = os.path.join(folder, name)
                if os.path.normpath(path) == os.path.normpath(keep):
                    continue
                if os.path.isdir(path):
                    shutil.rmtree(path, True)
                else:
                    os.remove(path)
        await storage.call(shed)

    async def _own(self, source: str, target: str) -> str:
        """A source of a job at its place in the job's own folder: a hard link (a file of the library or a download is
        never rewritten in place, only replaced), a copy where no link can be made. The service keeps its temporary
        files beside the sources of a job and removes them all with the job's record: a folder of sources two jobs
        shared lost the files of the one running with the record of the other."""
        folder = os.path.dirname(target)
        await storage.call(os.makedirs, folder, exist_ok=True)
        try:
            await storage.call(os.link, source, target, owns=(folder,))
        except FileExistsError:
            pass  # the job taken up again: its folder has the source already
        except OSError:
            await storage.call(shutil.copyfile, source, target, timeout=COPY_TIMEOUT_S, owns=(folder,))
        return target

    async def _copy(self, source: str, folder: str) -> str:
        return await self._own(source, os.path.join(folder, os.path.basename(source)))

    def _job_key(self, *what) -> str:
        """The key of a job of the service: the sync it belongs to and what it does. A stop and the start after it make
        the same job of the same sync, which is found by its key instead of being sent again; the next sync is another."""
        return job_key(self.series["id"], hashlib.md5("|".join(map(str, (self.series["id"], self.run_mark, *what))).encode()).hexdigest()[:12])

    async def _align(self, ops, results):
        """The alignment of an episode: what an earlier sync kept (modules/planner/kept.py) is taken as it is, the rest is one
        job of the service."""
        goal = ops[0].subject[0]
        timeline = video_of(goal, results).timeline
        outcomes, rest = {}, []
        for op in ops:
            way = op.subject[1]
            path = await self._kept_path(goal, way, timeline)
            if path is None:
                rest.append(op)
            else:
                outcomes[op.id] = Made(Track(way.dub, way.snapshot, goal.slot, timeline), path)
        if outcomes:
            self.m.log.info("сериал %d %s: выравнивание взято из сохранённого, сервису не отдаётся: %s", self.series["id"],
                            label_of(goal.slot), ", ".join(op.subject[1].voice for op in ops if op.id in outcomes))
        return {**outcomes, **(await self._make_aligned(rest, results) if rest else {})}

    async def _kept_path(self, goal, way, timeline) -> str | None:
        if self.kept is None:
            return None
        return await storage.call(self.kept.find, goal.slot, asdict(timeline), way.voice, asdict(way.dub))

    async def _aligned_kept(self, op) -> bool:
        """Whether every alignment a voice-over is downloaded for is kept (a sync that fell after the service's job): the
        file of the voice-over is then no step's input."""
        if self.made is None:
            return False
        if self._aligners[0] is not self.made:
            # The alignments of each voice-over in the plan in force, indexed once per plan rather than per voice-over.
            index: dict[str, list] = {}
            for each in self.made.ops:
                if each.kind == "align":
                    for need in each.needs:
                        index.setdefault(need, []).append(each)
            self._aligners = (self.made, index)
        needers = self._aligners[1].get(op.id, [])
        for each in needers:
            goal, way = each.subject
            if await self._kept_path(goal, way, goal.video.timeline) is None:
                return False
        return bool(needers)

    async def _keep(self, goal, way, timeline, path: str) -> None:
        """A result of the service is kept at once: its job goes with its folder when a sync stops or the setting says so. No
        link is a loss of the safety, not of the sync: the result is used as it is."""
        if self.kept is None:
            return
        try:
            await storage.call(self.kept.keep, goal.slot, asdict(timeline), way.voice, asdict(way.dub), path,
                               owns=self.kept.folder(goal.slot))
        except OSError as exc:
            self.m.log.warning("сериал %d %s %s: выравнивание не сохранено про запас (жёсткая ссылка не сделана): %s",
                               self.series["id"], label_of(goal.slot), way.voice, exc)

    async def _retire(self, slot) -> None:
        try:
            if await storage.call(self.kept.retire, slot, owns=self.kept.folder(slot), timeout=storage.REMOVE_TIMEOUT_S):
                self.m.log.info("сериал %d %s: сохранённые выравнивания убраны", self.series["id"], label_of(slot))
        except OSError as exc:
            self.m.log.warning("сериал %d %s: сохранённые выравнивания не убраны: %s", self.series["id"], label_of(slot), exc)

    async def placed(self, made, group) -> None:
        """The containers of a group are in place: the alignments kept for them have done their work."""
        if self.kept is not None:
            for slot in group.built:
                await self._retire(slot)

    async def release(self, synced) -> None:
        """The end of a sync: the alignments kept for an episode of its scope that it had no container to build for are of no
        use (the file is as it is to be, or no voice-over of it is wanted any more)."""
        if self.kept is None:
            return
        building = {op.slot for op in synced.plan.ops if op.kind == "build"}
        try:
            slots = await storage.call(self.kept.slots)
        except OSError as exc:
            self.m.log.warning("сериал %d: сохранённые выравнивания не прочитаны: %s", self.series["id"], exc)
            return
        for slot in slots:
            if slot not in building and (self.holds is None or self.holds(slot)):
                await self._retire(slot)

    async def _make_aligned(self, ops, results):
        lost = [op for op in ops if op.subject[1].kind == "download" and not results[f"dub:{op.subject[1].dub}"].file]
        if lost:  # its download was skipped for an alignment kept then and not kept now: the next sync downloads it
            rest = [op for op in ops if op not in lost]
            outcomes = await self._make_aligned(rest, results) if rest else {}
            return {**outcomes, **{op.id: Passing(reason("kept_alignment_lost")) for op in lost}}
        # A sound lying in its own file beside the video of a torrent release cannot be given to the service now: it takes
        # a voice-over as a file of a picture and a number of its sound (stage 15, И-5; the task Т-13 of track-muxer). Such
        # a pair is passed over for this sync with the next source taken; no copy of the video is made to carry it.
        apart = [op for op in ops if op.subject[1].kind == "download" and results[f"dub:{op.subject[1].dub}"].sound]
        if apart:
            rest = [op for op in ops if op not in apart]
            outcomes = await self._make_aligned(rest, results) if rest else {}
            return {**outcomes, **{op.id: Crashed(reason("conform_pair_unsupported")) for op in apart}}
        goal = ops[0].subject[0]
        timeline = video_of(goal, results).timeline
        label = f"{self.series['name']} {label_of(goal.slot)}"
        key = self._job_key("align", goal.slot, timeline,
                            sorted((op.subject[1].voice, repr(op.subject[1].dub)) for op in ops))
        folder = os.path.join(self.conform_dir, job_dir(key))
        places = await self.m.request(CONFORM_WORKSPACE, {"dub_folder": folder})
        of_dub = {}
        for op in ops:  # the name the file of each voice-over has in the job (the job taken up again has the same)
            way = op.subject[1]
            source = (results[f"dub:{way.dub}"].file if way.kind == "download"
                      else (await self.files.source_of(way.container, way.dub, way.own))[0])
            of_dub[sanitize_name(way.voice) + os.path.splitext(source)[1]] = op
        self.keys.add(key)
        job_id = await self.m.find_job(key)
        found = (await self.m.attach(job_id) if job_id
                 else await self._submit_align(goal, ops, results, key, folder, places, label))
        if found is None:
            return {op.id: None for op in ops}
        job_id, ended = found
        await self._shed(folder, places["out_dir"])
        by_dub = {os.path.basename(result.get("dub") or ""): result for result in ended.get("results") or []}
        outcomes = {}
        for name, op in of_dub.items():
            way = op.subject[1]
            result = by_dub.get(name)
            why = (result or {}).get("error") or ended.get("error") or reason("daemon_no_result")
            try:
                made = judged(result, cut=False)
            except Passing as passing:
                self.m.log.warning("%s %s: выравнивание не сделано: сбой сервиса в задаче %s: %s; %s", label, way.voice,
                                   job_id, describe(why), fate_of(passing))
                outcomes[op.id] = type(passing)(reason("daemon_job_failed", job=job_id, cause=why))
                continue
            if made is None:
                what = "критичные замечания" if result and result.get("ok") else why
                self.m.log.warning("%s %s: сервис выравнивания отказал в задаче %s, записано как неудача: %s", label,
                                   way.voice, job_id, what)
                outcomes[op.id] = None
            else:
                outcomes[op.id] = Made(Track(way.dub, way.snapshot, goal.slot, timeline), made)
                await self._keep(goal, way, timeline, made)
        clean = ended.get("status") == "done" and all(isinstance(each, Made) for each in outcomes.values())
        self.jobs.append((job_id, clean))
        return outcomes

    async def _submit_align(self, goal, ops, results, key, folder, places, label):
        video_path = self.files.video_path(goal, results)
        ref = await self._copy(video_path, os.path.join(folder, "ref"))
        ref_atrack = 0
        if goal.video.kind == "asset":
            ref_atrack = await self.files.stream_of(goal.video.container, goal.video.container.video.id, True)
        elif goal.video.kind == "download" and results[video_op(goal)].sound is None:
            ref_atrack = results[video_op(goal)].stream  # the own sound of a video of a torrent release in its file
        dubs, streams, copied = [], [], {}
        for op in ops:
            way = op.subject[1]
            # Each voice-over is the file of its voice in the job, whatever it came from: the queue of the service and the window
            # name it by that file. A file of the library is shared by its voice-overs: all of one file the same inode.
            if way.kind == "download":
                taken = results[f"dub:{way.dub}"]
                path, stream = taken.sound_file, taken.stream
            else:
                path, stream = await self.files.source_of(way.container, way.dub, way.own)
            dub = await self._own(copied.get(path, path), os.path.join(folder, "dub", sanitize_name(way.voice) + os.path.splitext(path)[1]))
            if way.kind != "download":
                copied.setdefault(path, dub)
            dubs.append(dub)
            streams.append(stream)
        return await self.m.job(key, CONFORM_SUBMIT, body={
            "ref": ref, "dubs": dubs, "dub_atracks": streams, "out_dir": places["out_dir"],
            "cache_dir": places["cache_dir"], "label": label, "key": key,
            **({"ref_atrack": ref_atrack} if ref_atrack else {})})

    async def _cut(self, op, results):
        way = op.subject
        ref2 = (self.files.path(way.ref2.container.name) if way.ref2.kind == "asset"
                else results[f"row:{way.ref2.row.id}"].file)
        key = self._job_key("cut", op.slot, way.row.id, ref2)
        label = f"{self.series['name']} {label_of(op.slot)}"
        folder = os.path.join(self.conform_dir, f"{label_of(op.slot)} #{job_dir(key)}")
        places = await self.m.request(CONFORM_WORKSPACE, {"dub_folder": folder})
        self.keys.add(key)
        job_id = await self.m.find_job(key)
        if job_id:
            found = await self.m.attach(job_id)
        else:
            episode = await self._copy(ref2, folder)
            atrack = 0
            if way.ref2.kind == "asset":
                atrack = await self.files.stream_of(way.ref2.container, way.ref2.container.video.id, True)
            # The picture: a file of the library (its own sound by its tags) or this sync's download of its row.
            if way.picture is not None:
                picture = self.files.path(way.picture.name)
                picture_atrack = await self.files.stream_of(way.picture, way.picture.video.id, True)
            else:
                picture, picture_atrack = results[f"picture:{way.row.id}"].file, 0
            picture = await self._copy(picture, os.path.join(folder, "picture"))
            found = await self.m.job(key, CONFORM_REVERSE, body={
                "compilation": picture, "compilation_atrack": picture_atrack,
                "episodes": [{"path": episode, "atrack": atrack}], "out_dir": places["out_dir"], "label": label})
        if found is None:
            return None
        job_id, ended = found
        await self._shed(folder, places["out_dir"])
        result = next(iter(ended.get("results") or []), None)
        why = (result or {}).get("error") or ended.get("error") or reason("daemon_no_result")
        try:
            out = judged(result, cut=True)
        except Passing as passing:
            self.m.log.warning("%s: разбор не сделан: сбой сервиса в задаче %s: %s; %s", label, job_id, describe(why),
                               fate_of(passing))
            raise type(passing)(reason("daemon_job_failed", job=job_id, cause=why)) from None
        self.jobs.append((job_id, out is not None and ended.get("status") == "done"))
        if out is None:
            what = "критичные замечания" if result and result.get("ok") else why
            self.m.log.warning("%s: сервис выравнивания отказал в задаче %s, записано как неудача: %s", label, job_id, what)
            return None
        return Made(cut_video(way, op.slot), out)

    # --- the files -------------------------------------------------------------------------------------------------

    async def stage(self, made, group, results) -> list:
        """The containers of the group built aside; the steps of its journal with the cards they are of."""
        if not group.built:
            return await self.files.build(made, group, results)
        self._flag("packing", 1)
        keys = [card_id(self.series["id"], slot) for slot in group.built]

        def report_for(goal):
            key = card_id(self.series["id"], goal.slot)

            async def report(stage: str, figures: dict) -> None:
                # A new stage is told at once; the figures of one under way go on the beat of the clock of displays.
                if self.m.packing.report(key, stage, figures):
                    self.m.pack_publish()
            return report
        try:
            async with self.m.building:
                steps = await self.files.build(made, group, results, report_for)
        except BaseException as exc:
            # The card gives the code of a build that failed (BuildFailed); the words of the error are the log's.
            if isinstance(exc, Exception):
                self.m.log.warning("сериал %d: сборка не удалась: %s", self.series["id"], exc)
            self.m.packing.failed(keys, getattr(exc, "code", ""))
            self.m.pack_publish()
            raise
        finally:
            self._flag("packing", -1)
        self.m.pack_publish()
        return steps

    async def commit(self, before, staged=None) -> None:
        """A group of the sync is put in place (`staged`: the steps of its journal) and the table written; with nothing
        staged, the table and what the sync recorded are written. One at a time (the executor's lock): a stop of the
        sync waits for the one under way."""
        self.committing = staged is None
        held = sorted(self.containers.values(), key=lambda each: each.name)
        table = await compute.run(containers_json, held)
        if staged:
            steps = await storage.call(self.files.write_journal, staged, table)
            self.m.packing.journal(self.series["id"], steps)
            self.m.packing.journal_begins(self.series, self.run_mark, steps)
            self.m.pack_going()
            await self.m.carry_out(self.series["id"], self.files, steps, table, self.m.packing.step_done)
            names = [card.name for card in self.m.packing.cards.values()
                     if card.series_id == self.series["id"] and card.status == "apply"]
            sizes = await storage.call(self.files.sizes, names)
            self.m.packing.applied(self.series["id"], lambda name: sizes.get(name))
            self.m.pack_publish()
        await self.m.save(self.series["id"], self)
        await storage.call(self.files.finish)
        self.m._ask_recount(self.series["id"])  # the files of the series changed: every window is told now, not at the end
        if staged and any(step[0] == "publish" for step in staged):
            # A new file of the series: the card shows when the series last got one (catalog: last_scan_time).
            self.m.send_command(CATALOG_SERIES_TOUCH_SCAN_TIME, {"series_id": self.series["id"]})
            await self.m.count_changed(self.series["id"])


class PlannerModule(BaseModule):
    name = PLANNER

    def __init__(self, bus, db) -> None:
        self.repo = PlannerRepository(db)
        self._runs: dict[int, asyncio.Task] = {}
        self._syncs: dict[int, BusSync] = {}  # the sync under way of each series
        self._halting: set[int] = set()  # the series whose sync the user stops
        self._fetches: dict[str, asyncio.Future] = {}
        self._jobs: dict[str, asyncio.Future] = {}
        self._ended: dict[str, dict] = {}  # jobs that ended before anyone waited for them
        self._views: dict[int, tuple] = {}
        self._failures: dict[int, str] = {}  # why the last sync of a series broke, told with the end of its work
        self._names: dict[int, tuple[str, dict]] = {}  # the names the rule gave each series, with the card they are of
        # The projection of the view of each series: the last view counted, the series whose inputs changed since it,
        # and the count under way.
        self._shown: dict[int, PlannerView] = {}
        self._stale: set[int] = set()
        self._counting: dict[int, asyncio.Task] = {}
        self._members: dict[int, dict] = {}  # the compilations of the layout of a series by their row
        self._loaded: dict[int, tuple] = {}  # the failures and commands a running sync started from
        self._forgot: set[int] = set()  # series whose failures the user reset while their sync ran
        self._shed: dict[int, set] = {}  # the failures such a reset took out of what the sync under way writes
        self._seq = 0
        # The cards of the packing (core/contracts/packing.py): the containers the syncs build, by series and episode.
        self.packing = PackBook()
        self.building = asyncio.Semaphore(BUILDS_AT_ONCE)
        self._packing_going = asyncio.Event()  # a container is under way: the queue is told on the beat of core/clock
        super().__init__(bus)
        # The flags of the status of each series: what its sync is at, and what it stands at (contribute, _stand).
        self._activity: dict[int, dict[str, bool]] = {}
        self._standing: dict[int, dict[str, bool]] = {}

    def register(self) -> None:
        self.handle(PLANNER_SYNC, self.on_sync)
        self.handle(PLANNER_SYNC_CANCEL, self.on_sync_cancel, concurrent=True)
        self.handle(PLANNER_WORK_GET, self.on_work_get)
        self.handle(PLANNER_SERIES_GET, self.on_series_get, concurrent=True)
        self.handle(PLANNER_DELETE, self.on_delete)
        self.handle(PLANNER_DELETE_CANCEL, self.on_delete_cancel)
        self.handle(PLANNER_FAILURES_FORGET, self.on_failures_forget)
        self.handle(PLANNER_FILES_GET, self.on_files_get)
        self.handle(PLANNER_REFERENCES_GET, self.on_references_get)
        self.handle(PLANNER_DOWNLOADED_COUNTS, self.on_downloaded_counts)
        self.handle(PLANNER_SEASONS, self.on_seasons)
        self.handle(DOWNLOADS_FETCH_FINISHED, self.on_fetch_finished)
        self.handle(CONFORM_JOB_FINISHED, self.on_job_finished)
        self.handle(CONFORM_JOB_REMOVED, self.on_job_removed)
        self.handle(SYSTEM_STARTED, self.on_system_started)
        self.handle(SERIES_DELETED, self.on_series_deleted)
        for changed in sorted(set(VIEW_INPUTS.values()), key=lambda topic: topic.name):
            self.handle(changed, self.on_view_input_changed)
        self.handle(GATEWAY_SSE_CLIENTS, self.on_sse_clients)
        self.handle(PLANNER_PACK_GET, self.on_pack_get)
        self.handle(PLANNER_PACK_REMOVE, self.on_pack_remove)

    async def on_start(self) -> None:
        for card in await self.repo.pack_cards():
            self.packing.kept(card)
        self.spawn(self._pack_ticker(), name="planner:packing", lasting=True)

    # --- the sync -----------------------------------------------------------------------------------------------

    async def on_sync(self, env: Envelope) -> dict:
        series_id = int(env.payload["series_id"])
        download = bool(env.payload.get("download", True))
        scope = SyncScope.of(env.payload)  # a season or an episode of it; none: the whole series
        if series_id in self._runs and not self._runs[series_id].done():
            return {"accepted": False, "reason": "planner_sync_running"}
        await self.repo.begin_run(series_id, download, None if scope is None else scope.named())
        self._start(series_id, download, scope)
        return {"accepted": True}

    def _start(self, series_id: int, download: bool, scope: SyncScope | None = None) -> None:
        self._failures.pop(series_id, None)  # the failure told is of the sync before: this one has none yet
        self._runs[series_id] = self.spawn(self._sync(series_id, download, scope), name=f"planner.sync:{series_id}")
        self._announce_work(series_id, True)

    async def _sync(self, series_id: int, download: bool, scope: SyncScope | None = None) -> None:
        try:
            series = await self.request(CATALOG_SERIES_GET, {"series_id": series_id})
            await self.repo.drop_pack_cards(self.packing.sync_started(series_id))
            sync = self._syncs[series_id] = BusSync(self, series, download, scope)
            sync.run_mark = await self.repo.run_started(series_id)
            await self._recover(series)
            await self._load(series_id, sync)
            online, choices = await self._inputs(series)
            sync.voice_order = dict(choices.groups)
            sync.containers = assets_table(sync.containers.values(), online)
            await sync.prepare()
            synced = await sync.sync(online, choices)
            await self.repo.save_outcome(series_id, [
                {"slot": list(slot) if slot else None, "kind": kind, "voice": voice, "reason": reason}
                for slot, kind, voice, reason in synced.problems])
            self.log.info("сериал %d: синхронизация плана — операций %d, применено групп %d из %d, новых неудач %d",
                          series_id, len(synced.plan.ops), len(synced.applied), len(synced.plan.groups),
                          len(synced.replanned))
            await self.count_changed(series_id)
            if download:
                await sync.release(synced)
                await self._archive_releases(series, synced)
            try:
                await self.clear_finished(sync.jobs)
                if sync.conform_dir:
                    await self.sweep({series_id})
            except Exception:  # noqa: BLE001 - the folders of the jobs wait for the next sweep; the sync is done
                self.log.exception("сериал %d: папки задач сервиса выравнивания не разобраны", series_id)
            if sync.downloads_dir:
                await storage.call(shutil.rmtree, sync.downloads_dir, True)
            await storage.call(sync.files.sweep)
        except asyncio.CancelledError:
            if series_id not in self._halting:
                # The process stops: the sync stays written down and is taken up again at the start (system.started).
                self._runs.pop(series_id, None)
                self._syncs.pop(series_id, None)
                raise
            # The user stopped it: its jobs of the service go and what it made aside goes; the episodes it had applied
            # by then stay applied (each was put in place and written on its own). Only then it ends as any sync ends,
            # so the series is free once every work of it is off (ЭТАП_11, the stop).
            asyncio.current_task().uncancel()
            stopped = self._syncs.get(series_id)
            for key in sorted(stopped.keys if stopped is not None else ()):
                job_id = await self.find_job(key)
                if job_id:
                    await self._drop_job(job_id)
            await self.sweep({series_id})
            await self._discard(stopped)
            self.log.info("сериал %d: синхронизация остановлена пользователем; применённые до остановки серии остаются",
                          series_id)
        except Exception as exc:  # noqa: BLE001 - the run ends either way: the error is in the log, the sync can be asked again
            self.log.exception("сериал %d: синхронизация плана прервана", series_id)
            self._failures[series_id] = reason("planner_sync_failed", cause=f"{type(exc).__name__}: {exc}")
        await self.repo.end_run(series_id)
        stay, gone = self.packing.sync_ended(series_id)
        await self.repo.keep_pack_cards(stay)
        await self.repo.drop_pack_cards(gone)
        self.pack_publish()
        self._runs.pop(series_id, None)
        self._syncs.pop(series_id, None)
        self.contribute(series_id, {"downloading": False, "aligning": False, "packing": False})
        # The end is told with the view the tables give now, before the work is said to be over: a window holds the last
        # packet it got, the one of the run is the frozen plan the sync worked by, and whoever waits for the end of the
        # run (scan.run.changed) finds the window told its outcome already.
        try:
            await self._publish_view(series_id)
        except Exception:  # noqa: BLE001 - the sync is over either way; the window reads the view when it opens again
            self.log.exception("сериал %d: вид плана после синхронизации не разослан", series_id)
        self._announce_work(series_id, False)

    async def _load(self, series_id: int, sync: SeriesSync) -> None:
        """The asset of the series as the table holds it; a file the disk has not any more is gone from the table."""
        containers = {}
        for container in await self.repo.containers(series_id):
            if await storage.call(os.path.exists, os.path.join(sync.files.root, container.name)):
                containers[container.name] = await as_in_file(sync.files, container)
            else:
                self.log.warning("сериал %d: файла %s нет на диске — он уходит из таблицы актива", series_id, container.name)
        sync.containers = containers
        sync.failed = {failure_of_key(each) for each in await self.repo.failures(series_id)}
        sync.commands = frozenset(command_of_key(each) for each in await self.repo.commands(series_id))
        self._loaded[series_id] = (frozenset(sync.failed), sync.commands)
        self._forgot.discard(series_id)
        self._shed.pop(series_id, None)

    async def save(self, series_id: int, sync: SeriesSync) -> None:
        """The table, the failures and the commands after a sync. What the user said while it ran is kept: a command
        recorded meanwhile waits for the next sync, a reset of the failures leaves only those this sync recorded."""
        loaded_failures, loaded_commands = self._loaded.get(series_id, (frozenset(), frozenset()))
        now = frozenset(command_of_key(each) for each in await self.repo.commands(series_id))
        commands = set(sync.commands) | (now - loaded_commands)
        commands -= loaded_commands - now  # a command the user took back meanwhile
        if series_id in self._forgot:
            # The user reset the failures while the sync ran: those written by then are not written again by it.
            self._shed.setdefault(series_id, set()).update(loaded_failures)
            self._forgot.discard(series_id)
        failed = set(sync.failed) - self._shed.get(series_id, set())
        held = list(sync.containers.values())
        await self.repo.save(series_id, await compute.run(containers_json, held),
                             [key_of_failure(each) for each in sorted(failed, key=repr)],
                             [key_of_command(each) for each in sorted(commands, key=repr)])
        # A sync writes after every group it applies: what it wrote is what the next writing of it starts from.
        self._loaded[series_id] = (frozenset(failed), frozenset(sync.commands))

    async def _inputs(self, series: dict):
        series_id = series["id"]
        layout = await self.request(COMPOSITION_GET, CompositionGetRequest(series_id=series_id).model_dump(mode="json"))
        voices = await self.reply_of(VOICES_SETTINGS_GET, {})
        online, choices, self._members[series_id] = await compute.run(layout_inputs, layout, series, voices)
        return online, choices

    # --- the picture of the packing ------------------------------------------------------------------------------

    def pack_publish(self) -> None:
        self.publish_event(PLANNER_PACK_CHANGED, {"tasks": [card.model_dump(mode="json") for card in self.packing.view()]})
        self.publish_event(PLANNER_JOURNAL_CHANGED,
                           {"journals": [each.model_dump(mode="json") for each in self.packing.journals_view()]})

    def pack_going(self) -> None:
        self._packing_going.set()
        self.pack_publish()

    async def _pack_ticker(self) -> None:
        """While a container is under way the queue is told on the beat of the clock of displays (core/clock)."""
        while True:
            await self._packing_going.wait()
            await beat_while(self.packing.running, self.pack_publish)
            self._packing_going.clear()

    async def on_pack_get(self, env: Envelope) -> dict:
        return {"tasks": [card.model_dump(mode="json") for card in self.packing.view()],
                "journals": [each.model_dump(mode="json") for each in self.packing.journals_view()]}

    async def on_pack_remove(self, env: Envelope) -> dict:
        """The user removes a card of the packing that stayed after its sync."""
        key = str(env.payload["id"])
        card = self.packing.cards.get(key)
        if card is None or card.status not in ("done", "failed"):
            raise LookupError(reason("pack_card_not_found", key=key))
        self.packing.forget(key)
        await self.repo.drop_pack_cards([key])
        self.pack_publish()
        return {"removed": True}

    async def on_sse_clients(self, env: Envelope) -> None:
        """An interface connected: it gets the packing as it is, with no question of its own."""
        if (env.payload or {}).get("count"):
            self.pack_publish()

    # --- the journal ---------------------------------------------------------------------------------------------

    async def carry_out(self, series_id: int, files: Library, steps: list, table: list, done=None) -> None:
        """The steps of a journal, one after another; the journal of the series in the packing shows the step under way
        (the rewrite of a container of 2160p takes minutes) and how many are done, and goes when the last one is."""
        by_name = {each["name"]: each for each in table}
        try:
            for step in steps:
                self.packing.journal_step(series_id, step)
                await files.step(step, by_name)
                self.packing.journal_step_done(series_id)
                if done is not None:
                    done(step)
        finally:
            self.packing.journal_ends(series_id)
            self.pack_publish()

    async def _recover(self, series: dict) -> None:
        """A journal a stop cut short is carried out to the end, and the table it leads to is the asset."""
        files = Library(series["save_path"])
        journal = await storage.call(files.read_journal)
        if journal is None:
            return
        self.log.info("сериал %d: журнал применения, прерванный остановкой, доводится до конца", series["id"])
        self.packing.journal_begins(series, "", journal["steps"], kind="recover")
        self.pack_going()
        await self.carry_out(series["id"], files, journal["steps"], journal["table"])
        failures = await self.repo.failures(series["id"])
        commands = await self.repo.commands(series["id"])
        await self.repo.save(series["id"], journal["table"], failures, commands)
        await storage.call(files.finish)

    async def on_system_started(self, env: Envelope) -> None:
        # The status of a series lives in memory (catalog): what each stood at when it was last counted is told first of
        # all, and the full recount of every view goes after it, apart from the work of the module, and puts right what it
        # counts otherwise (the tables, the code of a new release).
        for series_id, flags in (await self.repo.standing()).items():
            self._standing[series_id] = flags
            self._tell_status(series_id)
        resumed = set()
        for run in await self.repo.runs():
            self.log.info("сериал %d: синхронизация плана, прерванная остановкой, продолжается", run["series_id"])
            self._start(run["series_id"], bool(run["download"]), SyncScope.of(run["scope"]))
            resumed.add(run["series_id"])
        # Records of jobs removed while the process was stopped take their folders now.
        try:
            await self.sweep()
        except Exception:  # noqa: BLE001 - the folders wait for the next sweep; the start goes on
            self.log.exception("папки задач сервиса выравнивания не разобраны")
        self.spawn(self._recount_all([series_id for series_id in await self.repo.series_with_assets()
                                      if series_id not in resumed]), name="planner.recount_all")

    async def _recount_all(self, series_ids: list[int]) -> None:
        for series_id in series_ids:
            try:
                await self._publish_view(series_id)
            except Exception:  # noqa: BLE001 - one series must not keep the others from telling theirs
                self.log.exception("сериал %d: статус при старте не посчитан", series_id)

    def names_of(self, series: dict) -> dict:
        """The names of files the rule of names gave a series, kept while its card stays the same: the rule reads what it is
        given (the card and the item of the episode, the key of each name), so another card is another set."""
        card = json.dumps(series, sort_keys=True, ensure_ascii=False, default=str)
        held = self._names.get(series["id"])
        if held is None or held[0] != card:
            held = self._names[series["id"]] = (card, {})
        return held[1]

    async def on_series_deleted(self, env: Envelope) -> None:
        series_id = int(env.payload["series_id"])
        self._names.pop(series_id, None)
        task = self._runs.pop(series_id, None)
        if task is not None:
            task.cancel()
        await self.repo.purge(series_id)
        try:
            root = (await self.request(CONFORM_WORKSPACE, {})).get("conform")
            if root:
                await storage.call(Kept.of(root, series_id).drop, timeout=storage.REMOVE_TIMEOUT_S)
        except (BusRequestError, asyncio.TimeoutError, OSError):
            self.log.warning("сериал %d: сохранённые выравнивания не убраны с удалением сериала", series_id)
        self.publish_event(SERIES_PURGED, {"series_id": series_id, "owner": self.name})

    # --- the workers' signals ------------------------------------------------------------------------------------

    async def fetch(self, key: str, job: dict) -> dict:
        future = self._fetches.setdefault(key, asyncio.get_running_loop().create_future())
        self.send_command(DOWNLOADS_FETCH, job)
        try:
            return await future
        finally:
            self._fetches.pop(key, None)

    async def on_fetch_finished(self, env: Envelope) -> None:
        future = self._fetches.get(env.payload.get("key") or "")
        if future is not None and not future.done():
            future.set_result(env.payload)

    async def job(self, key: str, topic: Topic, body: dict) -> tuple[str, dict] | None:
        """A job at the alignment service: its number and how it ended (the result of each of its files, judged by the
        caller), None when the service refused the job; `Passing` when it could not be asked or its answer never came.
        Every end but a success is written to the log with its reason: the service keeps the reason of a crash, and
        nobody else here tells it."""
        label = body.get("label") or key
        try:
            return await self._job(key, topic, body)
        except Passing as passing:
            self.log.warning("%s: выравнивание не сделано, повторится при следующей синхронизации: %s", label,
                             describe(str(passing.args[0])) if passing.args else "ответа сервиса нет")
            raise

    async def _job(self, key: str, topic: Topic, body: dict) -> tuple[str, dict] | None:
        label = body.get("label") or key
        try:
            if topic is CONFORM_SUBMIT:
                reply = SubmitReply.model_validate(await self.request(topic, body))
                outcome, refusal, job_id = reply.outcome, reply.reason, reply.job_id
            else:
                reply = await self.request(topic, body)
                outcome, refusal = SubmitOutcome(reply["outcome"]), reply.get("reason")
                job_id = next(iter((reply.get("jobs") or {}).values()), None)
        except (BusRequestError, asyncio.TimeoutError) as exc:
            if NO_HANDLER in str(exc):
                raise Passing from None
            outcome, refusal, job_id = SubmitOutcome.UNCERTAIN, str(exc), None
        if outcome is SubmitOutcome.UNCERTAIN and job_id is None:
            found = await self.request(CONFORM_JOB_FIND, {"key": key})
            job_id = found.get("job_id")
            if not job_id:
                raise Passing(reason("daemon_job_lost", cause=str(refusal)))
        elif outcome is SubmitOutcome.UNAVAILABLE:
            raise Passing(reason("conform_down", error=str(refusal)))
        elif outcome is SubmitOutcome.REJECTED:
            self.log.warning("%s: задача отклонена сервисом выравнивания, записано как неудача: %s", label,
                             describe(str(refusal)))
            return None
        return await self.attach(job_id)

    async def on_sync_cancel(self, env: Envelope) -> dict:
        """The user stops the sync of a series: every work of it stops — the downloads (work.withdrawn: the executor
        stops them with their programs and removes what they wrote), the jobs of the service (cancelled and taken out of
        its history), the build of the containers aside. The episodes it applied by then stay applied; nothing more of
        it is. A journal of the library under way is not cut: the stop waits for its end. {cancelled}: false when no
        sync of the series went."""
        series_id = int(env.payload["series_id"])
        task, sync = self._runs.get(series_id), self._syncs.get(series_id)
        if task is None or task.done():
            return {"cancelled": False}
        if sync is not None and sync.committing:
            await asyncio.wait({task})
            return {"cancelled": False}
        self._halting.add(series_id)
        # A journal of the library under way is carried out to its end, and no other begins: the stop holds the lock of
        # the journals while the work is taken off.
        lock = sync.applying if sync is not None else None
        if lock is not None:
            await lock.acquire()
        try:
            if task.done() or (sync is not None and sync.committing):
                await asyncio.wait({task})
                return {"cancelled": False}
            await self._withdraw_downloads(series_id)
            task.cancel()
            await asyncio.wait({task})  # the run takes its jobs of the service off before it tells its end
        finally:
            if lock is not None:
                lock.release()
            self._halting.discard(series_id)
        return {"cancelled": True}

    async def _withdraw_downloads(self, series_id: int) -> None:
        """The downloads of the series stop, and the stop is over only once their programs have: asked and waited for;
        without an executor that answers, told (work.withdrawn)."""
        try:
            await self.request(DOWNLOADS_WITHDRAW, {"series_id": series_id})
        except (BusRequestError, asyncio.TimeoutError) as exc:
            if NO_HANDLER not in str(exc):
                self.log.warning("сериал %d: загрузки не остановлены по запросу: %s", series_id, exc)
            self.publish_event(WORK_WITHDRAWN, WorkWithdrawn(series_id=series_id).model_dump(mode="json"))

    async def _drop_job(self, job_id: str) -> None:
        """A job of a stopped sync: stopped if it waits or runs, then its record leaves the history (its folder with it)."""
        for _ in range(2):  # the first stops a job under way, the second removes the record of a job that ended
            try:
                if not (await self.request(CONFORM_CANCEL, {"job_id": job_id})).get("done"):
                    return
            except (BusRequestError, asyncio.TimeoutError):
                self.log.warning("задача %s сервиса выравнивания не снята: сервис не ответил", job_id)
                return

    async def _archive_releases(self, series: dict, synced) -> None:
        """A release of a torrent source no longer watched gives up its files once a sync has built everything it was to
        build (stage 15, 3а): every group applied, no step failed. A page another series lists keeps them: that series may
        still take from it. A refusal or a silence leaves the files as they are; the next sync asks again."""
        if synced.failed or len(synced.applied) != len(synced.plan.groups):
            return
        try:
            pages = [source.url for source in map(SeriesSource.model_validate, series.get("sources", []))
                     if source.source_type == SourceType.TORRENT]
            if not pages:
                return
            listing = await self.request(CATALOG_SERIES_LIST, {})
            for url in pages:
                shared = any(other["id"] != series["id"] and any(each["url"] == url for each in other.get("sources") or [])
                             for other in listing)
                if shared:
                    continue
                for release in (await self.request(TORRENTS_RELEASES_GET, {"url": url})).get("releases") or []:
                    if not release["watched"] and release["state"] == "ready":
                        self.log.info("сериал %d: раздача %s %s без слежения собрана — её файлы удаляются", series["id"],
                                      url, release["release"])
                        self.send_command(TORRENTS_RELEASE_DROP_FILES, {"url": url, "release": release["release"]})
        except Exception:  # noqa: BLE001 - the files of a release stay until the next sync; nothing is lost by waiting
            self.log.exception("сериал %d: раздачи без слежения не разобраны", series["id"])

    async def _discard(self, sync: "BusSync | None") -> None:
        """What a stopped sync made aside goes: its downloads, the containers it built aside (no journal was written:
        a sync with a journal under way is not stopped)."""
        if sync is None:
            return
        if sync.downloads_dir:
            await storage.call(shutil.rmtree, sync.downloads_dir, True)
        await storage.call(sync.files.sweep)

    async def find_job(self, key: str) -> str | None:
        """The job of the service a stop left behind, by its key: taken up instead of being sent again."""
        try:
            return (await self.request(CONFORM_JOB_FIND, {"key": key})).get("job_id")
        except (BusRequestError, asyncio.TimeoutError):
            return None

    async def attach(self, job_id: str) -> tuple[str, dict]:
        """The end of a job: its event, or its state when it ended before anybody waited (a stop in between)."""
        future = self._jobs.setdefault(job_id, asyncio.get_running_loop().create_future())
        try:
            ended = self._ended.pop(job_id, None)
            if ended is None:
                try:
                    state = await self.request(CONFORM_JOB_GET, {"job_id": job_id})
                except (BusRequestError, asyncio.TimeoutError):
                    state = {}  # not told now: the end of the job comes as its event
                if state.get("status") in ENDED:
                    ended = state
            if ended is None:
                ended = await future
        finally:
            self._jobs.pop(job_id, None)
        if ended.get("removed") or ended.get("status") in ("cancelled", "gone"):
            raise Passing(reason("daemon_job_removed", job=job_id))
        return job_id, ended

    async def sweep(self, series_ids=None) -> None:
        """A folder of a job stays while the record of the job is in the service's history (the plots are shown from
        it); a record gone takes its folder. A series whose sync is under way is left to the end of its sync."""
        root = (await self.request(CONFORM_WORKSPACE, {})).get("conform")
        if not root:
            return
        known = await self.request(CONFORM_JOB_KEYS, {})
        if not known.get("reachable"):
            return
        keys, base = {job_dir(key) for key in known.get("keys") or []}, os.path.join(root, WORK)

        def sweep() -> None:
            for name in os.listdir(base) if os.path.isdir(base) else ():
                if not name.isdigit() or (series_ids is not None and int(name) not in series_ids):
                    continue
                if int(name) in busy:
                    continue
                series = os.path.join(base, name)
                for job in os.listdir(series):
                    if job.rsplit("#", 1)[-1].strip() not in keys:
                        shutil.rmtree(os.path.join(series, job), True)
                if not os.listdir(series):
                    os.rmdir(series)
        busy = {series_id for series_id, run in self._runs.items() if not run.done()} - set(series_ids or ())
        await storage.call(sweep)

    async def clear_finished(self, jobs: list[tuple[str, bool]]) -> None:
        """The setting conform.clear_finished: a job that ended with every result taken leaves the service's history at
        once (its folder goes with it); one that ended in an error stays for the owner."""
        if not jobs or not (await self.request(CONFORM_SETTINGS_GET, {})).get("clear_finished"):
            return
        for job_id, clean in jobs:
            if clean:
                await self.request(CONFORM_CANCEL, {"job_id": job_id})

    async def on_job_finished(self, env: Envelope) -> None:
        job_id = env.payload.get("job_id")
        future = self._jobs.get(job_id)
        if future is not None and not future.done():
            future.set_result(env.payload)
        else:
            self._ended[job_id] = env.payload

    async def on_job_removed(self, env: Envelope) -> None:
        job_id = env.payload.get("job_id")
        future = self._jobs.get(job_id)
        if future is not None and not future.done():
            future.set_result({"removed": True})
        try:
            await self.sweep()
        except Exception:  # noqa: BLE001 - the folder waits for the next sweep
            self.log.exception("папки задач сервиса выравнивания не разобраны")

    # --- the work of the run ---------------------------------------------------------------------------------

    def contribute(self, series_id: int, flags: dict[str, bool]) -> None:
        """What the sync is at (downloading, aligning, packing); the card is told it with what the series stands at."""
        self._activity.setdefault(series_id, {}).update(flags)
        self._tell_status(series_id)

    async def _stand(self, series_id: int, view: PlannerView) -> None:
        """What the series stands at, told by its view (`standing_of`): the card is told it with what the sync is at."""
        flags = standing_of(view)
        if flags != self._standing.get(series_id):
            # Kept for the next start: it tells the cards at once, before its recount reaches the series.
            await self.repo.save_standing(series_id, flags)
        self._standing[series_id] = flags
        self._tell_status(series_id)

    def _tell_status(self, series_id: int) -> None:
        flags = {**self._standing.get(series_id, {}), **self._activity.get(series_id, {})}
        self.publish_event(SERIES_STATUS_CONTRIBUTION, {"source": PLANNER, "series_id": series_id, "flags": flags})

    def _state(self, series_id: int) -> WorkState:
        self._seq += 1
        running = series_id in self._runs and not self._runs[series_id].done()
        return WorkState(owner=PLANNER, series_id=series_id, active=running, busy=(), seq=self._seq,
                         failure=None if running else self._failures.get(series_id))

    def _announce_work(self, series_id: int, active: bool) -> None:
        self._seq += 1
        self.publish_event(WORK_SERIES_STATE, WorkState(owner=PLANNER, series_id=series_id, active=active, busy=(), seq=self._seq,
                                                        failure=None if active else self._failures.get(series_id)).model_dump(mode="json"))

    async def on_work_get(self, env: Envelope) -> WorkState:
        """Whether the series has work: a sync written down is work even before this process takes it up again at its
        start (`system.started` reaches the modules in no set order); the table is the truth, the task follows it. The
        state is counted before the table is read: an end announced meanwhile is later and stays in force."""
        series_id = int(env.payload["series_id"])
        state = self._state(series_id)
        if not state.active and await self.repo.running(series_id):
            state = state.model_copy(update={"active": True})
        return state

    # --- the view and the user's words ----------------------------------------------------------------------------

    async def _publish_view(self, series_id: int, sync: SeriesSync | None = None, made: Plan | None = None,
                            inputs=None) -> PlannerView:
        """The view of a series told to every window. With the plan a sync froze, that plan is shown as it is; else the
        view is counted again from the tables, once for every change however many come meanwhile (`_recount`)."""
        if made is None:
            return await self._recount(series_id)
        view = await self._counted(series_id, sync, made, inputs)
        await self._told(series_id, view)
        return view

    async def _told(self, series_id: int, view: PlannerView) -> None:
        # The packet is made in the pool, before the view is kept: what is kept and what is told change together.
        packet = await compute.run(packet_of, view)
        self._shown[series_id] = view
        self.publish_event(PLANNER_SERIES_CHANGED, packet)
        await self._stand(series_id, view)

    def _ask_recount(self, series_id: int) -> asyncio.Task:
        """An input of the view of the series changed: the view is counted again. Changes that come while it is counted
        make one more count, with all of them, when this one ends: the last view is always that of the last change."""
        self._stale.add(series_id)
        task = self._counting.get(series_id)
        if task is None or task.done():
            task = self._counting[series_id] = asyncio.ensure_future(self._count(series_id))
            task.add_done_callback(self._count_ended)
        return task

    async def _recount(self, series_id: int) -> PlannerView:
        return await asyncio.shield(self._ask_recount(series_id))

    async def _count(self, series_id: int) -> PlannerView:
        view = None
        while series_id in self._stale:
            self._stale.discard(series_id)
            view = await self._counted(series_id)
            await self._told(series_id, view)
        return view

    def _count_ended(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            self.log.error("вид плана не посчитан: %s", task.exception(), exc_info=task.exception())

    async def _shown_view(self, series_id: int) -> PlannerView:
        """The view as it was last counted; counted now when there is none or an input changed since."""
        counting = self._counting.get(series_id)
        idle = counting is None or counting.done()
        if idle and series_id in self._shown and series_id not in self._stale:
            return self._shown[series_id]
        return await self._recount(series_id) if idle else await asyncio.shield(counting)

    async def _counted(self, series_id: int, sync: SeriesSync | None = None, made: Plan | None = None,
                       inputs=None) -> PlannerView:
        """The view of a series: the asset, the failures and the commands as the table holds them now, and the plan as
        the tables give it now (between syncs only the tables change, and the plan is counted again from them); while a
        sync runs, the plan it froze. The reference of every episode, and why, is told by that plan. Thousands of rows:
        everything but the reading of the tables is counted in the pool of computation."""
        running = series_id in self._runs and not self._runs[series_id].done()
        if made is not None:
            self._views[series_id] = (made, inputs)
        elif running and series_id in self._views:
            made, inputs = self._views[series_id]
        if sync is None or made is None:
            try:
                series = await self.request(CATALOG_SERIES_GET, {"series_id": series_id})
            except (BusRequestError, asyncio.TimeoutError):
                series = None
            fresh = BusSync(self, series, False) if series is not None else SeriesSync()
            fresh.containers = {each.name: each for each in await self.repo.containers(series_id)}
            fresh.failed = {failure_of_key(each) for each in await self.repo.failures(series_id)}
            fresh.commands = frozenset(command_of_key(each) for each in await self.repo.commands(series_id))
            if made is None and series is not None:
                try:
                    rows, choices = await self._inputs(series)
                    held = list(fresh.containers.values())
                    fresh.containers = await compute.run(assets_table, held, rows)
                    inputs = fresh.inputs(rows, choices)
                    made = await fresh.plan_of(inputs)
                except (BusRequestError, asyncio.TimeoutError) as exc:
                    self.log.warning("сериал %d: план для окна не посчитан: %s", series_id, exc)
            sync = sync or fresh
        # What the view reads of the sync, as it is now: the sync goes on while the view is counted in the pool.
        held_now = Held(dict(sync.containers), frozenset(sync.failed), frozenset(sync.commands))
        problems = await self.repo.outcome(series_id)
        return await compute.run(view_body, series_id, held_now, made, inputs, dict(self._members.get(series_id, {})),
                                 running, problems)

    async def on_references_get(self, env: Envelope) -> tuple[Reference, ...]:
        """The reference of every episode of a series, and why it is that one (calc/plan/marks), as the models themselves."""
        return (await self._shown_view(int(env.payload["series_id"]))).references

    async def on_view_input_changed(self, env: Envelope) -> None:
        """An input of the view changed (VIEW_INPUTS): the view of its series is counted again, the views of every series
        for an input of all of them (the settings of the voice-overs). A change that says it moved only the summaries of
        the sources moves nothing the plan reads."""
        payload = env.payload if isinstance(env.payload, dict) else {}
        if payload.get("summary_only"):
            return
        series_id = payload.get("series_id")
        targets = [int(series_id)] if series_id is not None else sorted(
            set(await self.repo.series_with_assets()) | set(self._shown))
        for each in targets:
            self._ask_recount(each)

    async def on_series_get(self, env: Envelope) -> PlannerView:
        return await self._shown_view(int(env.payload["series_id"]))

    async def on_delete(self, env: Envelope) -> dict:
        command = PlanDelete.model_validate(env.payload)
        series_id = command.series_id
        held = {data["name"]: data for data in await self.repo.assets(series_id)}
        if command.name not in held:
            raise ValueError(reason("planner_delete_no_file", name=command.name))  # «Удалить» is only for what is in fact on disk (Д-1)
        if command.kind == "track" and (command.dub is None or not any(
                track["dub"] == command.dub.model_dump() for track in held[command.name]["tracks"])):
            raise ValueError(reason("planner_delete_no_track", name=command.name))
        await self.repo.add_command(series_id, stored_command(command.model_dump()))
        await self._publish_view(series_id)
        return {"recorded": True}

    async def on_delete_cancel(self, env: Envelope) -> dict:
        command = PlanDelete.model_validate(env.payload)
        series_id = command.series_id
        dropped = await self.repo.drop_command(series_id, stored_command(command.model_dump()))
        await self._publish_view(series_id)
        return {"dropped": bool(dropped)}

    async def count_changed(self, series_id: int) -> None:
        """The series holds another number of files: told to whoever shows it."""
        held = (await self.repo.episodes(series_id)).get(series_id, set())
        self.publish_event(SERIES_DOWNLOADED_CHANGED,
                           {"series_id": series_id, "count": sum(1 for season, _ in held if season != 0)})

    async def on_downloaded_counts(self, env: Envelope) -> dict:
        """{counts: {series_id: n}}: the episodes of every series the library holds and shows, specials (season 0) aside."""
        return {"counts": {series_id: sum(1 for season, _ in episodes if season != 0)
                           for series_id, episodes in (await self.repo.episodes()).items()}}

    async def on_seasons(self, env: Envelope) -> dict:
        """{seasons: [..]}: the seasons the files of a series are of (the aggregate of seasons of TMDB)."""
        held = (await self.repo.episodes()).get(int(env.payload["series_id"]), set())
        return {"seasons": sorted({season for season, _ in held})}

    async def on_files_get(self, env: Envelope) -> dict:
        """The files of the asset of a series, relative to its folder (the move of the folder takes them), with a journal a
        stop cut short and what it put aside: they go with the folder and are carried out there."""
        series_id = int(env.payload["series_id"])
        names = [data["name"] for data in await self.repo.assets(series_id)]
        series = await self.request(CATALOG_SERIES_GET, {"series_id": series_id})
        files = Library(series["save_path"])
        journal = await storage.call(files.read_journal)
        if journal is not None:
            names.append(os.path.relpath(files.journal, files.root))
            names += [step[1] for step in journal["steps"] if step[0] == "publish"]
        return {"names": names}

    async def on_failures_forget(self, env: Envelope) -> dict:
        """The reset of failed attempts, of the whole series or of one level of it (PlanForget): the attempts of the other
        levels stay, so what the plan chose because they failed (the second reference of an episode whose first did not
        download) is not undone by the reset of another episode."""
        forget = PlanForget.model_validate(env.payload)
        series_id = forget.series_id
        if forget.whole:
            reset = await self.repo.forget_failures(series_id)
            if series_id in self._runs:
                self._forgot.add(series_id)
        else:
            gone = await self._failures_of(series_id, forget)
            reset = await self.repo.forget_failures(series_id, [key_of_failure(each) for each in gone])
            if series_id in self._runs:
                # What a sync under way holds of them is not written back by it.
                self._shed.setdefault(series_id, set()).update(gone)
        await self._publish_view(series_id)
        return {"reset": reset}

    async def _failures_of(self, series_id: int, forget: PlanForget) -> list[tuple]:
        """The failed attempts of the series that belong to the level, placed by the rows the sources give now."""
        series = await self.request(CATALOG_SERIES_GET, {"series_id": series_id})
        held = {failure_of_key(each) for each in await self.repo.failures(series_id)}
        rows, choices = await self._inputs(series)
        inputs = BusSync(self, series, False).inputs(rows, choices)
        return [each["key"] for each in placed(held, inputs.online) if forget.holds(each)]
