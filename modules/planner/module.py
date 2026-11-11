"""Module planner: the plan of a video series and its sync (stage 12, the table of an episode).

Owns the table of the asset of every video series (plan_assets: the containers of its library, each with the provenance of
its video and tracks), the attempts that failed (plan_failures), the commands «Удалить» that wait (plan_commands) and the
syncs under way (plan_runs). The planner itself is core/plan: a pure function of the tables and the priorities; this module
reads the inputs over the bus, runs the executor (core/plan/executor.SeriesSync) with the service's own workers, and keeps
the table.

Inputs, read when a sync starts and frozen until it ends: the layout of the composition (`composition.get`: its rows, the
levers of every episode and compilation, the order of the voice-overs), the settings of the voice-overs (`voices.settings.get`),
the card of the series (`catalog.series.get`: the order of its sources, its folder, the episodes switched off) and the asset.

Steps of the graph, each through its owner:
  download (a video, a voice-over, a compilation): the command `downloads.fetch` into the work folder of the alignment
      service; its end is the event `downloads.fetch.finished` of the key.
  align: one `conform.submit` per episode — its reference once and every voice-over of it that came, once none of them
      is still being downloaded (a voice-over that did not come holds none back); a voice-over out of a file of the library
      by its track (`dub_atracks`); its end is `conform.job.finished`. A result is taken by `ok`.
  cut: `conform.reverse` with the single video of the episode; its end is `conform.job.finished`.
  A job is keyed by its sync (plan_runs.started_at) and what it does: a sync taken up after a stop finds its job by the
      key (conform.job.find) and takes its end instead of sending it again. When a job ends, what it was given (copies,
      cache) goes; its folder with what it wrote (tracks, plots) stays while its record is in the service's history
      (conform.job.keys), and goes with the record (conform.job.removed, the end of a sync, the start). The setting
      conform.clear_finished takes a job that ended with every result out of the history at the end of its sync.
  build and apply: here, by core/plan/files (the container with its provenance, the journal in the folder of the series).
A failure the service judged is recorded and the plan is counted again from the same snapshot; a failure of the moment
(the network, a service that is down, an answer that never came) is no record and is tried at the next sync.

Queries:
  planner.sync {series_id, download?} -> {accepted}: the sync of a series is recorded and starts; it is a work of the run
      of the sync (scan), told by `work.series.state` (owner `planner`). Without download no step runs: only what needs no
      work is applied (names, `.off`, removals the plan owes).
  planner.sync.cancel {series_id} -> {cancelled}: the user stops the sync of the series: its downloads (work.withdrawn),
      its jobs of the service (cancelled, taken out of the history), the build aside stop; the episodes it applied by
      then stay applied (each group of the plan is put in place and written as soon as its own steps have ended),
      nothing more of it is, and it is not taken up again at the start. A journal of the library under way is not cut:
      the stop waits for its end.
  planner.work.get {series_id} -> WorkState.
  planner.series.get {series_id} -> PlannerView: the asset, the goal of every episode, the failures and the commands.
  planner.delete {series_id, kind: video | track, name, dub?} -> {recorded}: the command «Удалить» of a file or of a track
      in it, carried out by the next sync (a video goes only for another video of its episode).
  planner.delete.cancel {series_id, kind, name, dub?} -> {dropped}.
  planner.failures.forget {series_id} -> {reset}: the user's reset of the failed attempts.
  planner.pack.get {} -> {tasks, journals}: the cards of the packing and the journals being carried out. planner.pack.remove {id} -> {removed}: the user removes a card
      that stayed after its sync.
  planner.files.get {series_id} -> {names}: the files of the asset, relative to the folder of the series.
  planner.downloaded_counts {} -> {counts: {series_id: n}}: the episodes every series holds and shows, specials aside.
  planner.seasons {series_id} -> {seasons}: the seasons its files are of.
  planner.references.get {series_id} -> [Reference]: the reference of every episode and why it is that one, told by the
      plan (core/plan/marks): the window shows the choice the sync makes.
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
from dataclasses import replace

from core import NO_HANDLER, BaseModule, BusRequestError, compute, storage
from core import container as mkv
from core.clock import beat_while
from core.dump import plain
from core.contracts import (WORK_WITHDRAWN, CompositionReply, DownloadNeed, DownloadVariant, PlanDelete, PlannerView, SeriesSource, SourceType, SubmitOutcome,
                            SubmitReply)
from core.contracts.work import WORK_STATE, WorkState, WorkWithdrawn
from core.envelope import Envelope
from core.ffprobe import END_SLACK_S, duration_of
from core.fsnames import sanitize_name
from core.ignored import parse_memory
from core.plan.executor import DOWNLOADS, Made, Passing, SeriesSync, judged, video_of
from core.plan.files import Library, as_in_file, container_json, container_of, label_of
from core.plan.inputs import assets_against, choices_of, online_of
from core.plan.marks import references_of
from core.plan.planner import Plan, name_of
from core.plan.tables import RowId, Track, Video
from core.voice_names import Groups

from .packing import PackBook, card_id
from .repository import PlannerRepository
from .view import view_of

PLANNER = "planner"
# A safeguard of the copy of a video into the work folder of the alignment service: a 4K episode over the network.
COPY_TIMEOUT_S = 1800.0
ENDED = ("done", "failed", "cancelled", "gone")  # the states of a job that ended (conform.job.get)
WORK = "planner"  # the folder of the planner's work inside the work folder of the alignment service


def _row_key(row: RowId) -> list:
    return [row.source_id, row.row_key, row.video]


def _command(payload: dict) -> list:
    command = [payload["kind"], payload["name"]]
    if payload["kind"] == "track":
        dub = payload["dub"]
        command.append([dub["source_id"], dub["row_key"], dub.get("video", "")])
    return command


def _decoded(command: list) -> tuple:
    return (command[0], command[1], RowId(*command[2])) if command[0] == "track" else (command[0], command[1])


def _encoded(command: tuple) -> list:
    return [command[0], command[1], _row_key(command[2])] if command[0] == "track" else [command[0], command[1]]


def _failure_decoded(key: list) -> tuple:
    return (key[0], *(RowId(*each) for each in key[1:]))


def _failure_encoded(key: tuple) -> list:
    return [key[0], *(_row_key(each) for each in key[1:])]


# Every download but the video of the reference is taken not above this height (the owner's rule, stage 10).
OTHER_MAX_HEIGHT = 720

def _sizes(files: Library, names: list[str]) -> dict[str, float]:
    """The size (MB) of each file of the library named, as it lies after the journal."""
    out = {}
    for name in names:
        path = files.path(name)
        if os.path.isfile(path):
            out[name] = round(os.path.getsize(path) / 1024 ** 2, 1)
    return out


class BusSync(SeriesSync):
    """The sync of one series with the service's workers (see the module)."""

    def __init__(self, module: "PlannerModule", series: dict, download: bool) -> None:
        super().__init__()
        self.m = module
        self.series = series
        self.download = download
        self.files = Library(series["save_path"])
        self.downloads_dir = ""
        self.conform_dir = ""
        self.download_names: dict[str, str] = {}  # the name of the file of a download, by the step that took it
        self.names: dict[tuple, str] = {}
        self.told = False  # the window was shown the plan this sync froze
        self.flags: dict[str, int] = {}  # the steps under way by what they are, for the card of the series
        self.run_mark = ""  # when the sync under way began (plan_runs): the same after a stop, so its jobs are found again
        self.jobs: list[tuple[str, bool]] = []  # the jobs of the service this sync took the end of: (number, all ok)
        # The keys of every job of the service this sync gave out or took up, written before it is sent: a stop finds
        # each by its key (an answer to the sending that has not come yet is not waited for) and cancels it.
        self.keys: set[str] = set()
        self.committing = False  # the end of the sync is being written: a stop waits for it
        # A VK video whose stream request fails may be handed to the downloader itself (as the service always did).
        self.vk_sources = {each.id for each in map(SeriesSource.model_validate, series.get("sources", []))
                           if each.source_type == SourceType.VK_VIDEO}
        # The marks of the sources as the window «Выход» shows them: the place in the list and the colour of each.
        ordered = sorted(map(SeriesSource.model_validate, series.get("sources", [])), key=lambda each: (each.position, each.id))
        self.source_marks = {each.id: (place, each.color) for place, each in enumerate(ordered, start=1)}
        self.voice_order: dict[int, tuple[str, ...]] = {}  # the order of the voice-overs of each season (choices.groups)

    async def plan_of(self, inputs):
        """The plan of the sync; the first one of it is the plan the window shows while the sync runs."""
        made = await super().plan_of(inputs)
        if self.download and not self.told:
            self.told = True
            await self.m._publish_view(self.series["id"], self, made, inputs)
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
        self.m.publish_event("planner.sync.trace", {"series_id": self.series["id"], "run": self.run_mark, "kind": kind,
                                                    "download": self.download, "data": body})

    async def prepare(self) -> None:
        folders = await self.m.request("conform.workspace", {})
        if folders.get("downloads") and folders.get("conform"):
            self.downloads_dir = os.path.join(folders["downloads"], WORK, str(self.series["id"]))
            self.conform_dir = os.path.join(folders["conform"], WORK, str(self.series["id"]))

    async def namer_for(self, made: Plan):
        """The series' own rule of names (`rules.format_filename`), asked once for every goal of the plan."""
        wanted = {}
        for goal in made.goals:
            video = goal.video
            cut = video.kind == "cut" or (video.container is not None and video.container.video.cut_of is not None)
            wanted[(goal.slot, video.snapshot.group, video.snapshot.height, cut, goal.off)] = (goal.slot, video, cut)
        for key, (slot, video, cut) in wanted.items():
            if key in self.names:
                continue
            item = {"season": slot[0], "episode_start": slot[1], "episode_end": None,
                    "part": slot[2] if len(slot) > 2 else None, "resolution": video.snapshot.height,
                    "voiceover_tag": video.snapshot.group, "source_url": video.snapshot.link}
            if cut:
                item["cut"] = json.dumps({"height": video.snapshot.height, "voice_group": video.snapshot.group})
            try:
                made_name = (await self.m.request("rules.format_filename", {"series": self.series, "media_item": item}))["filename"]
                name = os.path.splitext(made_name)[0] + ".mkv" + (".off" if key[-1] else "")
            except (BusRequestError, KeyError, asyncio.TimeoutError):
                name = name_of(slot, video.snapshot, key[-1])
            self.names[key] = name

        def namer(slot, video, off):
            cut = video.kind == "cut" or (video.container is not None and video.container.video.cut_of is not None)
            return self.names.get((slot, video.snapshot.group, video.snapshot.height, cut, off)) or \
                name_of(slot, video.snapshot, off)
        return namer

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
            raise Passing(f"внутренняя ошибка: {exc}") from None
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
            return {op.id: Passing(f"внутренняя ошибка: {exc}") for op in ops}
        finally:
            self._flag("aligning", -1)

    def _key(self, op_id: str) -> str:
        return f"plan:{self.series['id']}:{hashlib.md5(op_id.encode()).hexdigest()[:12]}"

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
            height = int((await self.m.request("voices.settings.get", {})).get("cut_max_height") or 0)
            return height or None
        if op.kind == "download_dub":
            return OTHER_MAX_HEIGHT
        return None

    async def _download(self, op):
        row = op.subject
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
                                   self.series["id"], op.id, finished.get("error"))
                raise Passing(f"загрузка не удалась: {finished.get('error')}")
            self.m.log.warning("сериал %d: загрузка %s не удалась, записано как неудача: %s", self.series["id"], op.id,
                               finished.get("error"))
            return None
        if op.kind == "download_video":
            return Made(Video(row.id, row.snapshot(), row.slot, row.id), path, retimed=await self._retimed(row.id, path))
        if op.kind == "download_dub":
            return Made(row, path)
        return Made(None, path)

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

    async def _copy(self, source: str, folder: str) -> str:
        await storage.call(os.makedirs, folder, exist_ok=True)
        target = os.path.join(folder, os.path.basename(source))
        await storage.call(shutil.copyfile, source, target, timeout=COPY_TIMEOUT_S, owns=(folder,))
        return target

    def _job_key(self, *what) -> str:
        """The key of a job of the service: the sync it belongs to and what it does. A stop and the start after it make
        the same job of the same sync, which is found by its key instead of being sent again; the next sync is another."""
        return hashlib.md5("|".join(map(str, (self.series["id"], self.run_mark, *what))).encode()).hexdigest()[:12]

    async def _align(self, ops, results):
        goal = ops[0].subject[0]
        timeline = video_of(goal, results).timeline
        label = f"{self.series['name']} {label_of(goal.slot)}"
        key = self._job_key("align", goal.slot, timeline,
                            sorted((op.subject[1].voice, repr(op.subject[1].dub)) for op in ops))
        folder = os.path.join(self.conform_dir, key)
        places = await self.m.request("conform.workspace", {"dub_folder": folder})
        of_dub = {}
        for op in ops:  # the name the file of each voice-over has in the job (the job taken up again has the same)
            way = op.subject[1]
            if way.kind == "download":
                name = os.path.basename(results[f"dub:{way.dub}"].file)
            else:
                source = (await self.files.source_of(way.container, way.dub, way.own))[0]
                name = sanitize_name(way.voice) + os.path.splitext(source)[1]
            of_dub[name] = op
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
            why = (result or {}).get("error") or ended.get("error") or "сервис не дал результата"
            try:
                made = judged(result, cut=False)
            except Passing:
                self.m.log.warning("%s %s: выравнивание не сделано, повторится при следующей синхронизации: сбой "
                                   "сервиса в задаче %s: %s", label, way.voice, job_id, why)
                outcomes[op.id] = Passing(f"сбой сервиса в задаче {job_id}: {why}")
                continue
            if made is None:
                what = "критичные замечания" if result and result.get("ok") else why
                self.m.log.warning("%s %s: сервис выравнивания отказал в задаче %s, записано как неудача: %s", label,
                                   way.voice, job_id, what)
                outcomes[op.id] = None
            else:
                outcomes[op.id] = Made(Track(way.dub, way.snapshot, goal.slot, timeline), made)
        clean = ended.get("status") == "done" and all(isinstance(each, Made) for each in outcomes.values())
        self.jobs.append((job_id, clean))
        return outcomes

    async def _submit_align(self, goal, ops, results, key, folder, places, label):
        video_path = self.files.video_path(goal, results)
        ref = await self._copy(video_path, os.path.join(folder, "ref"))
        ref_atrack = 0
        if goal.video.kind == "asset":
            ref_atrack = await self.files.stream_of(goal.video.container, goal.video.container.video.id, True)
        dubs, streams, copied = [], [], {}
        for op in ops:
            way = op.subject[1]
            if way.kind == "download":
                dub, stream = results[f"dub:{way.dub}"].file, 0
                if not dub.startswith(self.downloads_dir):
                    dub = await self._copy(dub, os.path.join(folder, "dub"))
            else:  # out of a file of the library: copied once, each voice-over a name of its own (a link to the copy)
                path, stream = await self.files.source_of(way.container, way.dub, way.own)
                dub = os.path.join(folder, "dub", sanitize_name(way.voice) + os.path.splitext(path)[1])
                if path in copied:
                    await storage.call(os.link, copied[path], dub)
                else:
                    await storage.call(os.makedirs, os.path.dirname(dub), exist_ok=True)
                    await storage.call(shutil.copyfile, path, dub, timeout=COPY_TIMEOUT_S, owns=(folder,))
                    copied[path] = dub
            dubs.append(dub)
            streams.append(stream)
        return await self.m.job(key, "conform.submit", body={
            "ref": ref, "dubs": dubs, "dub_atracks": streams, "out_dir": places["out_dir"],
            "cache_dir": places["cache_dir"], "label": label, "key": key,
            **({"ref_atrack": ref_atrack} if ref_atrack else {})})

    async def _cut(self, op, results):
        way = op.subject
        ref2 = (self.files.path(way.ref2.container.name) if way.ref2.kind == "asset"
                else results[f"row:{way.ref2.row.id}"].file)
        key = self._job_key("cut", op.slot, way.row.id, ref2)
        label = f"{self.series['name']} {label_of(op.slot)}"
        folder = os.path.join(self.conform_dir, f"{label_of(op.slot)} #{key}")
        places = await self.m.request("conform.workspace", {"dub_folder": folder})
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
            found = await self.m.job(key, "conform.reverse", body={
                "compilation": picture, "compilation_atrack": picture_atrack,
                "episodes": [{"path": episode, "atrack": atrack}], "out_dir": places["out_dir"], "label": label})
        if found is None:
            return None
        job_id, ended = found
        await self._shed(folder, places["out_dir"])
        result = next(iter(ended.get("results") or []), None)
        why = (result or {}).get("error") or ended.get("error") or "сервис не дал результата"
        try:
            out = judged(result, cut=True)
        except Passing:
            self.m.log.warning("%s: разбор не сделан, повторится при следующей синхронизации: сбой сервиса в задаче %s: %s",
                               label, job_id, why)
            raise Passing(f"сбой сервиса в задаче {job_id}: {why}") from None
        self.jobs.append((job_id, out is not None and ended.get("status") == "done"))
        if out is None:
            what = "критичные замечания" if result and result.get("ok") else why
            self.m.log.warning("%s: сервис выравнивания отказал в задаче %s, записано как неудача: %s", label, job_id, what)
            return None
        return Made(Video(way.row.id, way.snapshot, op.slot, way.timeline, cut_of=way.row.id), out)

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
        table = [container_json(each) for each in sorted(self.containers.values(), key=lambda each: each.name)]
        if staged:
            steps = await storage.call(self.files.write_journal, staged, table)
            self.m.packing.journal(self.series["id"], steps)
            self.m.packing.journal_begins(self.series, self.run_mark, steps)
            self.m.pack_going()
            await self.m.carry_out(self.series["id"], self.files, steps, table, self.m.packing.step_done)
            names = [card.name for card in self.m.packing.cards.values()
                     if card.series_id == self.series["id"] and card.status == "apply"]
            sizes = await storage.call(_sizes, self.files, names)
            self.m.packing.applied(self.series["id"], lambda name: sizes.get(name))
            self.m.pack_publish()
        await self.m.save(self.series["id"], self)
        await storage.call(self.files.finish)
        if staged and any(step[0] == "publish" for step in staged):
            # A new file of the series: the card shows when the series last got one (catalog: last_scan_time).
            self.m.send_command("catalog.series.touch_scan_time", {"series_id": self.series["id"]})
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
        self._members: dict[int, dict] = {}  # the compilations of the layout of a series by their row
        self._loaded: dict[int, tuple] = {}  # the failures and commands a running sync started from
        self._forgot: set[int] = set()  # series whose failures the user reset while their sync ran
        self._shed: dict[int, set] = {}  # the failures such a reset took out of what the sync under way writes
        self._seq = 0
        # The cards of the packing (core/contracts/packing.py): the containers the syncs build, by series and episode.
        self.packing = PackBook()
        self._packing_going = asyncio.Event()  # a container is under way: the queue is told on the beat of core/clock
        super().__init__(bus)
        # The flags of the status of each series: what its sync is at, and what it stands at (contribute, _stand).
        self._activity: dict[int, dict[str, bool]] = {}
        self._standing: dict[int, dict[str, bool]] = {}

    def register(self) -> None:
        self.handle("planner.sync", self.on_sync)
        self.handle("planner.sync.cancel", self.on_sync_cancel, concurrent=True)
        self.handle("planner.work.get", self.on_work_get)
        self.handle("planner.series.get", self.on_series_get, concurrent=True)
        self.handle("planner.delete", self.on_delete)
        self.handle("planner.delete.cancel", self.on_delete_cancel)
        self.handle("planner.failures.forget", self.on_failures_forget)
        self.handle("planner.files.get", self.on_files_get)
        self.handle("planner.references.get", self.on_references_get)
        self.handle("planner.downloaded_counts", self.on_downloaded_counts)
        self.handle("planner.seasons", self.on_seasons)
        self.handle("downloads.fetch.finished", self.on_fetch_finished)
        self.handle("conform.job.finished", self.on_job_finished)
        self.handle("conform.job.removed", self.on_job_removed)
        self.handle("system.started", self.on_system_started)
        self.handle("series.deleted", self.on_series_deleted)
        self.handle("composition.changed", self.on_layout_changed)
        self.handle("series.updated", self.on_series_updated)
        self.handle("voices.settings.changed", self.on_voice_settings_changed)
        self.handle("gateway.sse.clients", self.on_sse_clients)
        self.handle("planner.pack.get", self.on_pack_get)
        self.handle("planner.pack.remove", self.on_pack_remove)

    async def on_start(self) -> None:
        for card in await self.repo.pack_cards():
            self.packing.kept(card)
        self.spawn(self._pack_ticker(), name="planner:packing", lasting=True)

    # --- the sync -----------------------------------------------------------------------------------------------

    async def on_sync(self, env: Envelope) -> dict:
        series_id = int(env.payload["series_id"])
        download = bool(env.payload.get("download", True))
        if series_id in self._runs and not self._runs[series_id].done():
            return {"accepted": False, "reason": "planner_sync_running"}
        await self.repo.begin_run(series_id, download)
        self._start(series_id, download)
        return {"accepted": True}

    def _start(self, series_id: int, download: bool) -> None:
        self._runs[series_id] = self.spawn(self._sync(series_id, download), name=f"planner.sync:{series_id}")
        self._announce_work(series_id, True)

    async def _sync(self, series_id: int, download: bool) -> None:
        try:
            series = await self.request("catalog.series.get", {"series_id": series_id})
            await self.repo.drop_pack_cards(self.packing.sync_started(series_id))
            sync = self._syncs[series_id] = BusSync(self, series, download)
            sync.run_mark = await self.repo.run_started(series_id)
            await self._recover(series)
            await self._load(series_id, sync)
            online, choices = await self._inputs(series)
            sync.voice_order = dict(choices.groups)
            sync.containers = {each.name: each for each in assets_against(sync.containers.values(), online)}
            await sync.prepare()
            synced = await sync.sync(online, choices)
            await self.repo.save_outcome(series_id, [
                {"slot": list(slot) if slot else None, "kind": kind, "voice": voice, "reason": reason}
                for slot, kind, voice, reason in synced.problems])
            self.log.info("сериал %d: синхронизация плана — операций %d, применено групп %d из %d, новых неудач %d",
                          series_id, len(synced.plan.ops), len(synced.applied), len(synced.plan.groups),
                          len(synced.replanned))
            await self.count_changed(series_id)
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
        except Exception:  # noqa: BLE001 - the run ends either way: the error is in the log, the sync can be asked again
            self.log.exception("сериал %d: синхронизация плана прервана", series_id)
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
        for data in await self.repo.assets(series_id):
            container = container_of(data)
            if await storage.call(os.path.exists, os.path.join(sync.files.root, container.name)):
                containers[container.name] = await as_in_file(sync.files, container)
            else:
                self.log.warning("сериал %d: файла %s нет на диске — он уходит из таблицы актива", series_id, container.name)
        sync.containers = containers
        sync.failed = {_failure_decoded(each) for each in await self.repo.failures(series_id)}
        sync.commands = frozenset(_decoded(each) for each in await self.repo.commands(series_id))
        self._loaded[series_id] = (frozenset(sync.failed), sync.commands)
        self._forgot.discard(series_id)
        self._shed.pop(series_id, None)

    async def save(self, series_id: int, sync: SeriesSync) -> None:
        """The table, the failures and the commands after a sync. What the user said while it ran is kept: a command
        recorded meanwhile waits for the next sync, a reset of the failures leaves only those this sync recorded."""
        loaded_failures, loaded_commands = self._loaded.get(series_id, (frozenset(), frozenset()))
        now = frozenset(_decoded(each) for each in await self.repo.commands(series_id))
        commands = set(sync.commands) | (now - loaded_commands)
        commands -= loaded_commands - now  # a command the user took back meanwhile
        if series_id in self._forgot:
            # The user reset the failures while the sync ran: those written by then are not written again by it.
            self._shed.setdefault(series_id, set()).update(loaded_failures)
            self._forgot.discard(series_id)
        failed = set(sync.failed) - self._shed.get(series_id, set())
        await self.repo.save(series_id, [container_json(each) for each in sync.containers.values()],
                             [_failure_encoded(each) for each in sorted(failed, key=repr)],
                             [_encoded(each) for each in sorted(commands, key=repr)])
        # A sync writes after every group it applies: what it wrote is what the next writing of it starts from.
        self._loaded[series_id] = (frozenset(failed), frozenset(sync.commands))

    async def _inputs(self, series: dict):
        series_id = series["id"]
        layout = await compute.run(CompositionReply.model_validate,
                                   await self.request("composition.get", {"series_id": series_id}))
        self._members[series_id] = {(each.ref.source_id, each.ref.row_key): each.ref for each in layout.compilations}
        voices = await self.request("voices.settings.get", {})
        places = {each.source_id: " / ".join(each.place) for each in layout.sources}
        online = online_of(layout.rows, places)
        ignored_seasons = set()
        try:
            ignored_seasons = {int(each) for each in json.loads(series.get("ignored_seasons") or "[]")}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        try:
            ignored = parse_memory(json.loads(series.get("ignored_episodes") or "{}"))
        except (json.JSONDecodeError, TypeError):
            ignored = {}
        slots = {row.slot[:2] for row in online if not row.compilation}
        off = {slot for slot in slots if slot[0] in ignored_seasons or slot[1] in ignored.get(slot[0], set())}
        order = {each.season: [span.label for span in each.voices] for each in layout.voice_orders}
        sources = [SeriesSource.model_validate(each).id for each in series.get("sources", [])]
        choices = choices_of(layout.rows, online, [(each.season, each.episode, each.levers) for each in layout.settings],
                             [(each.compilation, each.levers) for each in layout.levels if each.compilation],
                             players=voices.get("priority") or [], player_groups=Groups.of(voices.get("player_groups") or {}),
                             sources=sources, voice_order=order, off_episodes=off, titles=layout.voice_titles,
                             languages=voices.get("voice_languages") or {})
        return online, choices

    # --- the picture of the packing ------------------------------------------------------------------------------

    def pack_publish(self) -> None:
        self.publish_event("planner.pack.changed", {"tasks": [card.model_dump(mode="json") for card in self.packing.view()]})
        self.publish_event("planner.journal.changed",
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
            raise LookupError(f"карточка упаковки {key} не найдена или упаковка идёт")
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
        resumed = set()
        for run in await self.repo.runs():
            self.log.info("сериал %d: синхронизация плана, прерванная остановкой, продолжается", run["series_id"])
            self._start(run["series_id"], bool(run["download"]))
            resumed.add(run["series_id"])
        # Records of jobs removed while the process was stopped take their folders now.
        try:
            await self.sweep()
        except Exception:  # noqa: BLE001 - the folders wait for the next sweep; the start goes on
            self.log.exception("папки задач сервиса выравнивания не разобраны")
        # The status of a series lives in memory (catalog): every series with files tells it again at the start.
        for series_id in await self.repo.series_with_assets():
            if series_id not in resumed:
                try:
                    await self._publish_view(series_id)
                except Exception:  # noqa: BLE001 - one series must not keep the others from telling theirs
                    self.log.exception("сериал %d: статус при старте не посчитан", series_id)

    async def on_series_deleted(self, env: Envelope) -> None:
        series_id = int(env.payload["series_id"])
        task = self._runs.pop(series_id, None)
        if task is not None:
            task.cancel()
        await self.repo.purge(series_id)
        self.publish_event("series.purged", {"series_id": series_id, "owner": self.name})

    # --- the workers' signals ------------------------------------------------------------------------------------

    async def fetch(self, key: str, job: dict) -> dict:
        future = self._fetches.setdefault(key, asyncio.get_running_loop().create_future())
        self.send_command("downloads.fetch", job)
        try:
            return await future
        finally:
            self._fetches.pop(key, None)

    async def on_fetch_finished(self, env: Envelope) -> None:
        future = self._fetches.get(env.payload.get("key") or "")
        if future is not None and not future.done():
            future.set_result(env.payload)

    async def job(self, key: str, topic: str, body: dict) -> tuple[str, dict] | None:
        """A job at the alignment service: its number and how it ended (the result of each of its files, judged by the
        caller), None when the service refused the job; `Passing` when it could not be asked or its answer never came.
        Every end but a success is written to the log with its reason: the service keeps the reason of a crash, and
        nobody else here tells it."""
        label = body.get("label") or key
        try:
            return await self._job(key, topic, body)
        except Passing as passing:
            self.log.warning("%s: выравнивание не сделано, повторится при следующей синхронизации: %s", label,
                             passing.args[0] if passing.args else "ответа сервиса нет")
            raise

    async def _job(self, key: str, topic: str, body: dict) -> tuple[str, dict] | None:
        label = body.get("label") or key
        try:
            if topic == "conform.submit":
                reply = SubmitReply.model_validate(await self.request(topic, body))
                outcome, reason, job_id = reply.outcome, reply.reason, reply.job_id
            else:
                reply = await self.request(topic, body)
                outcome, reason = SubmitOutcome(reply["outcome"]), reply.get("reason")
                job_id = next(iter((reply.get("jobs") or {}).values()), None)
        except (BusRequestError, asyncio.TimeoutError) as exc:
            if NO_HANDLER in str(exc):
                raise Passing from None
            outcome, reason, job_id = SubmitOutcome.UNCERTAIN, str(exc), None
        if outcome is SubmitOutcome.UNCERTAIN and job_id is None:
            found = await self.request("conform.job.find", {"key": key})
            job_id = found.get("job_id")
            if not job_id:
                raise Passing(f"задачу не удалось ни поставить, ни найти: {reason}")
        elif outcome is SubmitOutcome.UNAVAILABLE:
            raise Passing(f"сервис недоступен: {reason}")
        elif outcome is SubmitOutcome.REJECTED:
            self.log.warning("%s: задача отклонена сервисом выравнивания, записано как неудача: %s", label, reason)
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
            await self.request("downloads.withdraw", {"series_id": series_id})
        except (BusRequestError, asyncio.TimeoutError) as exc:
            if NO_HANDLER not in str(exc):
                self.log.warning("сериал %d: загрузки не остановлены по запросу: %s", series_id, exc)
            self.publish_event(WORK_WITHDRAWN, WorkWithdrawn(series_id=series_id).model_dump(mode="json"))

    async def _drop_job(self, job_id: str) -> None:
        """A job of a stopped sync: stopped if it waits or runs, then its record leaves the history (its folder with it)."""
        for _ in range(2):  # the first stops a job under way, the second removes the record of a job that ended
            try:
                if not (await self.request("conform.cancel", {"job_id": job_id})).get("done"):
                    return
            except (BusRequestError, asyncio.TimeoutError):
                self.log.warning("задача %s сервиса выравнивания не снята: сервис не ответил", job_id)
                return

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
            return (await self.request("conform.job.find", {"key": key})).get("job_id")
        except (BusRequestError, asyncio.TimeoutError):
            return None

    async def attach(self, job_id: str) -> tuple[str, dict]:
        """The end of a job: its event, or its state when it ended before anybody waited (a stop in between)."""
        future = self._jobs.setdefault(job_id, asyncio.get_running_loop().create_future())
        try:
            ended = self._ended.pop(job_id, None)
            if ended is None:
                try:
                    state = await self.request("conform.job.get", {"job_id": job_id})
                except (BusRequestError, asyncio.TimeoutError):
                    state = {}  # not told now: the end of the job comes as its event
                if state.get("status") in ENDED:
                    ended = state
            if ended is None:
                ended = await future
        finally:
            self._jobs.pop(job_id, None)
        if ended.get("removed") or ended.get("status") in ("cancelled", "gone"):
            raise Passing(f"задача {job_id} снята в сервисе")
        return job_id, ended

    async def sweep(self, series_ids=None) -> None:
        """A folder of a job stays while the record of the job is in the service's history (the plots are shown from
        it); a record gone takes its folder. A series whose sync is under way is left to the end of its sync."""
        root = (await self.request("conform.workspace", {})).get("conform")
        if not root:
            return
        known = await self.request("conform.job.keys", {})
        if not known.get("reachable"):
            return
        keys, base = set(known.get("keys") or []), os.path.join(root, WORK)

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
        if not jobs or not (await self.request("conform.settings.get", {})).get("clear_finished"):
            return
        for job_id, clean in jobs:
            if clean:
                await self.request("conform.cancel", {"job_id": job_id})

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

    def _stand(self, series_id: int, view: dict) -> None:
        """What the series stands at, told by its view: ready — a file of it is in the library; waiting — the plan has
        work for the next sync; error — an episode lacks what a recorded attempt was for (`covered` of the view: another
        source that gave it leaves no error), or work of the last sync failed for the moment and nothing gave it."""
        work = any(goal["video"]["kind"] != "asset" or any(track["kind"] != "reuse" for track in goal["tracks"])
                   for goal in view["episodes"]) or bool(view["commands"])
        self._standing[series_id] = {"ready": bool(view["assets"]), "waiting": work and not view["running"],
                                     "error": any(not each["covered"] for each in view["failures"])
                                     or bool(view["problems"])}
        self._tell_status(series_id)

    def _tell_status(self, series_id: int) -> None:
        flags = {**self._standing.get(series_id, {}), **self._activity.get(series_id, {})}
        self.publish_event("series.status.contribution", {"source": PLANNER, "series_id": series_id, "flags": flags})

    def _state(self, series_id: int) -> WorkState:
        self._seq += 1
        running = series_id in self._runs and not self._runs[series_id].done()
        return WorkState(owner=PLANNER, series_id=series_id, active=running, busy=[], seq=self._seq)

    def _announce_work(self, series_id: int, active: bool) -> None:
        self._seq += 1
        self.publish_event(WORK_STATE, WorkState(owner=PLANNER, series_id=series_id, active=active, busy=[],
                                                 seq=self._seq).model_dump())

    async def on_work_get(self, env: Envelope) -> dict:
        """Whether the series has work: a sync written down is work even before this process takes it up again at its
        start (`system.started` reaches the modules in no set order); the table is the truth, the task follows it. The
        state is counted before the table is read: an end announced meanwhile is later and stays in force."""
        series_id = int(env.payload["series_id"])
        state = self._state(series_id)
        if not state.active and await self.repo.running(series_id):
            state = state.model_copy(update={"active": True})
        return state.model_dump()

    # --- the view and the user's words ----------------------------------------------------------------------------

    async def _publish_view(self, series_id: int, sync: SeriesSync | None = None, made: Plan | None = None,
                            inputs=None) -> dict:
        view = PlannerView.model_validate(await self._view(series_id, sync, made, inputs)).model_dump(mode="json")
        self.publish_event("planner.series.changed", view)
        self._stand(series_id, view)
        return view

    async def _view(self, series_id: int, sync: SeriesSync | None = None, made: Plan | None = None, inputs=None) -> dict:
        """The view of a series: the asset, the failures and the commands as the table holds them now, and the plan as
        the tables give it now (between syncs only the tables change, and the plan is counted again from them); while a
        sync runs, the plan it froze. The reference of every episode, and why, is told by that plan."""
        running = series_id in self._runs and not self._runs[series_id].done()
        if made is not None:
            self._views[series_id] = (made, inputs)
        elif running and series_id in self._views:
            made, inputs = self._views[series_id]
        if sync is None or made is None:
            try:
                series = await self.request("catalog.series.get", {"series_id": series_id})
            except (BusRequestError, asyncio.TimeoutError):
                series = None
            fresh = BusSync(self, series, False) if series is not None else SeriesSync()
            fresh.containers = {data["name"]: container_of(data) for data in await self.repo.assets(series_id)}
            fresh.failed = {_failure_decoded(each) for each in await self.repo.failures(series_id)}
            fresh.commands = frozenset(_decoded(each) for each in await self.repo.commands(series_id))
            if made is None and series is not None:
                try:
                    rows, choices = await self._inputs(series)
                    fresh.containers = {each.name: each for each in assets_against(fresh.containers.values(), rows)}
                    inputs = fresh.inputs(rows, choices)
                    made = await fresh.plan_of(inputs)
                except (BusRequestError, asyncio.TimeoutError) as exc:
                    self.log.warning("сериал %d: план для окна не посчитан: %s", series_id, exc)
            sync = sync or fresh
        # Online is what the sources give now: a row that stands from an earlier read is not.
        online = frozenset(row.id for row in inputs.online if row.current) if inputs is not None else None
        references = references_of(inputs, made, self._members.get(series_id, {})) \
            if inputs is not None and made is not None else []
        return {**view_of(series_id, sync, made, running=running, online=online,
                          rows=inputs.online if inputs is not None else ()),
                "problems": await self.repo.outcome(series_id),
                "references": [each.model_dump(mode="json") for each in references]}

    async def on_references_get(self, env: Envelope) -> list[dict]:
        """The reference of every episode of a series, and why it is that one (core/plan/marks)."""
        return (await self._view(int(env.payload["series_id"])))["references"]

    async def on_layout_changed(self, env: Envelope) -> None:
        """The layout or the levers of a series changed: its plan is counted again from the tables, and the window told."""
        if env.payload.get("summary_only"):
            return
        await self._publish_view(int(env.payload["series_id"]))

    async def on_series_updated(self, env: Envelope) -> None:
        """The user switched an episode or a season off or on (the memory of the series): the plan is counted again."""
        if {"ignored_seasons", "ignored_episodes"} & set(env.payload or {}):
            await self._publish_view(int(env.payload["series_id"]))

    async def on_voice_settings_changed(self, env: Envelope) -> None:
        for series_id in await self.repo.series_with_assets():
            await self._publish_view(series_id)

    async def on_series_get(self, env: Envelope) -> dict:
        return PlannerView.model_validate(await self._view(int(env.payload["series_id"]))).model_dump(mode="json")

    async def on_delete(self, env: Envelope) -> dict:
        command = PlanDelete.model_validate(env.payload)
        series_id = command.series_id
        held = {data["name"]: data for data in await self.repo.assets(series_id)}
        if command.name not in held:
            raise ValueError("planner_delete_no_file")  # «Удалить» is only for what is in fact on disk (Д-1)
        if command.kind == "track" and (command.dub is None or not any(
                track["dub"] == command.dub.model_dump() for track in held[command.name]["tracks"])):
            raise ValueError("planner_delete_no_track")
        await self.repo.add_command(series_id, _command(command.model_dump()))
        await self._publish_view(series_id)
        return {"recorded": True}

    async def on_delete_cancel(self, env: Envelope) -> dict:
        command = PlanDelete.model_validate(env.payload)
        series_id = command.series_id
        dropped = await self.repo.drop_command(series_id, _command(command.model_dump()))
        await self._publish_view(series_id)
        return {"dropped": bool(dropped)}

    async def count_changed(self, series_id: int) -> None:
        """The series holds another number of files: told to whoever shows it."""
        counts = (await self.on_downloaded_counts(env=None))["counts"]
        self.publish_event("series.downloaded.changed", {"series_id": series_id, "count": counts.get(series_id, 0)})

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
        series = await self.request("catalog.series.get", {"series_id": series_id})
        files = Library(series["save_path"])
        journal = await storage.call(files.read_journal)
        if journal is not None:
            names.append(os.path.relpath(files.journal, files.root))
            names += [step[1] for step in journal["steps"] if step[0] == "publish"]
        return {"names": names}

    async def on_failures_forget(self, env: Envelope) -> dict:
        series_id = int(env.payload["series_id"])
        reset = await self.repo.forget_failures(series_id)
        if series_id in self._runs:
            self._forgot.add(series_id)
        await self._publish_view(series_id)
        return {"reset": reset}
