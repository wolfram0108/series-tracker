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
  align: `conform.submit` (the video copied into the work folder; a voice-over out of a container by its track,
      `dub_atracks`); its end is `conform.job.finished`. A result is taken by `ok` and an empty `critical`.
  cut: `conform.reverse` with the single video of the episode; its end is `conform.job.finished`.
  build and apply: here, by core/plan/files (the container with its provenance, the journal in the folder of the series).
A failure the service judged is recorded and the plan is counted again from the same snapshot; a failure of the moment
(the network, a service that is down, an answer that never came) is no record and is tried at the next sync.

Queries:
  planner.sync {series_id, download?} -> {accepted}: the sync of a series is recorded and starts; it is a work of the run
      of the sync (scan), told by `work.series.state` (owner `planner`). Without download no step runs: only what needs no
      work is applied (names, `.off`, removals the plan owes).
  planner.work.get {series_id} -> WorkState.
  planner.series.get {series_id} -> PlannerView: the asset, the goal of every episode, the failures and the commands.
  planner.delete {series_id, kind: video | track, name, dub?} -> {recorded}: the command «Удалить» of a file or of a track
      in it, carried out by the next sync (a video goes only for another video of its episode).
  planner.delete.cancel {series_id, kind, name, dub?} -> {dropped}.
  planner.failures.forget {series_id} -> {reset}: the user's reset of the failed attempts.
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
Events in: downloads.fetch.finished, conform.job.finished, conform.job.removed, system.started (syncs under way are taken up
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
import uuid
from dataclasses import replace

from core import NO_HANDLER, BaseModule, BusRequestError, storage
from core import container as mkv
from core.contracts import (CompositionReply, PlanDelete, PlannerView, SeriesSource, SourceType, SubmitOutcome,
                            SubmitReply)
from core.contracts.work import WORK_STATE, WorkState
from core.envelope import Envelope
from core.ignored import parse_memory
from core.plan.executor import DOWNLOADS, Made, Passing, SeriesSync, judged, video_of
from core.plan.files import Library, as_in_file, container_json, container_of, label_of
from core.plan.inputs import assets_against, choices_of, online_of
from core.plan.marks import references_of
from core.plan.planner import Plan, name_of
from core.plan.tables import RowId, Track, Video
from core.voice_names import Groups

from .repository import PlannerRepository
from .view import view_of

PLANNER = "planner"
# A safeguard of the copy of a video into the work folder of the alignment service: a 4K episode over the network.
COPY_TIMEOUT_S = 1800.0
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
        self.names: dict[tuple, str] = {}
        self.pending: list[tuple] = []  # the steps of the journal of this sync
        self.told = False  # the window was shown the plan this sync froze
        self.flags: dict[str, int] = {}  # the steps under way by what they are, for the card of the series
        # A VK video whose stream request fails may be handed to the downloader itself (as the service always did).
        self.vk_sources = {each.id for each in map(SeriesSource.model_validate, series.get("sources", []))
                           if each.source_type == SourceType.VK_VIDEO}

    async def plan_of(self, inputs):
        """The plan of the sync; the first one of it is the plan the window shows while the sync runs."""
        made = await super().plan_of(inputs)
        if self.download and not self.told:
            self.told = True
            await self.m._publish_view(self.series["id"], self, made, inputs)
        return made

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
            if op.kind == "align":
                return await self._align(op, results)
            if op.kind == "cut":
                return await self._cut(op, results)
            if op.kind == "build":
                return Made(op.subject)
        except Passing:
            raise
        except Exception:  # noqa: BLE001 - one step that breaks is a step of the moment, not the end of the sync
            self.m.log.exception("сериал %d: шаг %s не выполнен", self.series["id"], op.id)
            raise Passing from None
        raise AssertionError(op.kind)

    def _key(self, op_id: str) -> str:
        return f"plan:{self.series['id']}:{hashlib.md5(op_id.encode()).hexdigest()[:12]}"

    async def _download(self, op):
        row = op.subject
        key = self._key(op.id)
        path = os.path.join(self.downloads_dir, key.split(":")[-1] + ".mp4")
        await storage.call(os.makedirs, self.downloads_dir, exist_ok=True)
        finished = await self.m.fetch(key, {"key": key, "url": row.link, "path": path, "series_id": self.series["id"],
                                            "whole": op.kind == "download_video",
                                            "ytdlp": row.id.source_id in self.vk_sources})
        if not finished.get("ok"):
            if finished.get("transient"):
                self.m.log.warning("сериал %d: загрузка %s не удалась, повторится при следующей синхронизации: %s",
                                   self.series["id"], op.id, finished.get("error"))
                raise Passing
            self.m.log.warning("сериал %d: загрузка %s не удалась, записано как неудача: %s", self.series["id"], op.id,
                               finished.get("error"))
            return None
        if op.kind == "download_video":
            return Made(Video(row.id, row.snapshot(), row.slot, row.id), path)
        if op.kind == "download_dub":
            return Made(row, path)
        return Made(None, path)

    async def _copy(self, source: str, folder: str) -> str:
        await storage.call(os.makedirs, folder, exist_ok=True)
        target = os.path.join(folder, os.path.basename(source))
        await storage.call(shutil.copyfile, source, target, timeout=COPY_TIMEOUT_S, owns=(folder,))
        return target

    async def _align(self, op, results):
        goal, way = op.subject
        video_path = self.files.video_path(goal, results)
        label = f"{self.series['name']} {label_of(goal.slot)} {way.voice}"
        key = uuid.uuid4().hex[:12]
        folder = os.path.join(self.conform_dir, key)
        ref = await self._copy(video_path, os.path.join(folder, "ref"))
        ref_atrack = 0
        if goal.video.kind == "asset":
            ref_atrack = await self.files.stream_of(goal.video.container, goal.video.container.video.id, True)
        if way.kind == "download":
            dub, stream = results[f"dub:{goal.slot}:{way.dub}"].file, 0
            if not dub.startswith(self.downloads_dir):
                dub = await self._copy(dub, os.path.join(folder, "dub"))
        else:
            dub = await self._copy(self.files.path(way.container.name), os.path.join(folder, "dub"))
            stream = await self.files.stream_of(way.container, way.dub, way.own)
        places = await self.m.request("conform.workspace", {"dub_folder": folder})
        out = await self.m.job(key, "conform.submit", {
            "ref": ref, "dubs": [dub], "dub_atracks": [stream], "out_dir": places["out_dir"],
            "cache_dir": places["cache_dir"], "label": label, "key": key,
            **({"ref_atrack": ref_atrack} if ref_atrack else {})})
        if out is None:
            return None
        return Made(Track(way.dub, way.snapshot, goal.slot, video_of(goal, results).timeline), out)

    async def _cut(self, op, results):
        way = op.subject
        ref2 = (self.files.path(way.ref2.container.name) if way.ref2.kind == "asset" else results[f"ref2:{op.slot}"].file)
        key = uuid.uuid4().hex[:12]
        label = f"{self.series['name']} {label_of(op.slot)}"
        folder = os.path.join(self.conform_dir, f"{label_of(op.slot)} #{key}")
        episode = await self._copy(ref2, folder)
        atrack = 0
        if way.ref2.kind == "asset":
            atrack = await self.files.stream_of(way.ref2.container, way.ref2.container.video.id, True)
        places = await self.m.request("conform.workspace", {"dub_folder": folder})
        out = await self.m.job(key, "conform.reverse", {
            "compilation": results[f"compilation:{way.row.id}"].file, "episodes": [{"path": episode, "atrack": atrack}],
            "out_dir": places["out_dir"], "label": label})
        if out is None:
            return None
        return Made(Video(way.row.id, way.snapshot, op.slot, way.timeline, cut_of=way.row.id), out)

    # --- the files -------------------------------------------------------------------------------------------------

    async def stage(self, made, group, results) -> None:
        if not group.built:
            self.pending += await self.files.build(made, group, results)
            return
        self._flag("packing", 1)
        try:
            self.pending += await self.files.build(made, group, results)
        finally:
            self._flag("packing", -1)

    async def commit(self, before) -> None:
        table = [container_json(each) for each in sorted(self.containers.values(), key=lambda each: each.name)]
        if self.pending:
            steps = await storage.call(self.files.write_journal, self.pending, table)
            self.pending = []
            await self.m.carry_out(self.series["id"], self.files, steps, table)
        await self.m.save(self.series["id"], self)
        await storage.call(self.files.finish)


class PlannerModule(BaseModule):
    name = PLANNER

    def __init__(self, bus, db) -> None:
        self.repo = PlannerRepository(db)
        self._runs: dict[int, asyncio.Task] = {}
        self._fetches: dict[str, asyncio.Future] = {}
        self._jobs: dict[str, asyncio.Future] = {}
        self._ended: dict[str, dict] = {}  # jobs that ended before anyone waited for them
        self._views: dict[int, tuple] = {}
        self._members: dict[int, dict] = {}  # the compilations of the layout of a series by their row
        self._loaded: dict[int, tuple] = {}  # the failures and commands a running sync started from
        self._forgot: set[int] = set()  # series whose failures the user reset while their sync ran
        self._seq = 0
        super().__init__(bus)
        # The flags of the status of each series: what its sync is at, and what it stands at (contribute, _stand).
        self._activity: dict[int, dict[str, bool]] = {}
        self._standing: dict[int, dict[str, bool]] = {}

    def register(self) -> None:
        self.handle("planner.sync", self.on_sync)
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
            sync = BusSync(self, series, download)
            await self._recover(series)
            await self._load(series_id, sync)
            online, choices = await self._inputs(series)
            sync.containers = {each.name: each for each in assets_against(sync.containers.values(), online)}
            await sync.prepare()
            synced = await sync.sync(online, choices)
            await self.repo.save_outcome(series_id, [
                {"slot": list(slot) if slot else None, "kind": kind, "voice": voice, "reason": reason}
                for slot, kind, voice, reason in synced.problems])
            # A new file of the series: the card shows when the series last got one (catalog: last_scan_time).
            if any(group.built for group in synced.plan.groups if group.slots in synced.applied):
                self.send_command("catalog.series.touch_scan_time", {"series_id": series_id})
            self.log.info("сериал %d: синхронизация плана — операций %d, применено групп %d из %d, новых неудач %d",
                          series_id, len(synced.plan.ops), len(synced.applied), len(synced.plan.groups),
                          len(synced.replanned))
            counts = (await self.on_downloaded_counts(env=None))["counts"]
            self.publish_event("series.downloaded.changed", {"series_id": series_id, "count": counts.get(series_id, 0)})
            if sync.conform_dir:
                await storage.call(shutil.rmtree, sync.conform_dir, True)
            if sync.downloads_dir:
                await storage.call(shutil.rmtree, sync.downloads_dir, True)
        except asyncio.CancelledError:
            # The process stops: the sync stays written down and is taken up again at the start (system.started).
            self._runs.pop(series_id, None)
            raise
        except Exception:  # noqa: BLE001 - the run ends either way: the error is in the log, the sync can be asked again
            self.log.exception("сериал %d: синхронизация плана прервана", series_id)
        await self.repo.end_run(series_id)
        self._runs.pop(series_id, None)
        self.contribute(series_id, {"downloading": False, "aligning": False, "packing": False})
        self._announce_work(series_id, False)
        # The end is told with the view the tables give now: a window holds the last packet it got, and the one of the
        # run is the frozen plan the sync worked by.
        try:
            await self._publish_view(series_id)
        except Exception:  # noqa: BLE001 - the sync is over either way; the window reads the view when it opens again
            self.log.exception("сериал %d: вид плана после синхронизации не разослан", series_id)

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

    async def save(self, series_id: int, sync: SeriesSync) -> None:
        """The table, the failures and the commands after a sync. What the user said while it ran is kept: a command
        recorded meanwhile waits for the next sync, a reset of the failures leaves only those this sync recorded."""
        loaded_failures, loaded_commands = self._loaded.pop(series_id, (frozenset(), frozenset()))
        now = frozenset(_decoded(each) for each in await self.repo.commands(series_id))
        commands = set(sync.commands) | (now - loaded_commands)
        commands -= loaded_commands - now  # a command the user took back meanwhile
        failed = set(sync.failed) - (set(loaded_failures) if series_id in self._forgot else set())
        self._forgot.discard(series_id)
        await self.repo.save(series_id, [container_json(each) for each in sync.containers.values()],
                             [_failure_encoded(each) for each in sorted(failed, key=repr)],
                             [_encoded(each) for each in sorted(commands, key=repr)])

    async def _inputs(self, series: dict):
        series_id = series["id"]
        layout = CompositionReply.model_validate(await self.request("composition.get", {"series_id": series_id}))
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
        order = [span.label for each in sorted(layout.voice_orders, key=lambda item: item.season) for span in each.voices]
        sources = [SeriesSource.model_validate(each).id for each in series.get("sources", [])]
        choices = choices_of(layout.rows, online, [(each.season, each.episode, each.levers) for each in layout.settings],
                             [(each.compilation, each.levers) for each in layout.levels if each.compilation],
                             players=voices.get("priority") or [], player_groups=Groups.of(voices.get("player_groups") or {}),
                             sources=sources, voice_order=order, off_episodes=off)
        return online, choices

    # --- the journal ---------------------------------------------------------------------------------------------

    async def carry_out(self, series_id: int, files: Library, steps: list, table: list) -> None:
        by_name = {each["name"]: each for each in table}
        for step in steps:
            await files.step(step, by_name)

    async def _recover(self, series: dict) -> None:
        """A journal a stop cut short is carried out to the end, and the table it leads to is the asset."""
        files = Library(series["save_path"])
        journal = await storage.call(files.read_journal)
        if journal is None:
            return
        self.log.info("сериал %d: журнал применения, прерванный остановкой, доводится до конца", series["id"])
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

    async def job(self, key: str, topic: str, body: dict) -> str | None:
        """A job at the alignment service: the path of its one result, None when the service judged it a failure;
        `Passing` when it could not be asked or its answer never came. Every end but a success is written to the log with
        its reason: the service keeps the reason of a crash, and nobody else here tells it."""
        label = body.get("label") or key
        try:
            return await self._job(key, topic, body)
        except Passing as passing:
            self.log.warning("%s: выравнивание не сделано, повторится при следующей синхронизации: %s", label,
                             passing.args[0] if passing.args else "ответа сервиса нет")
            raise

    async def _job(self, key: str, topic: str, body: dict) -> str | None:
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
        ended = self._ended.pop(job_id, None)
        if ended is None:
            future = self._jobs.setdefault(job_id, asyncio.get_running_loop().create_future())
            try:
                ended = await future
            finally:
                self._jobs.pop(job_id, None)
        if ended.get("removed"):
            raise Passing(f"задача {job_id} снята в сервисе")
        results = ended.get("results") or []
        result = results[0] if results else None
        why = (result or {}).get("error") or ended.get("error") or "сервис не дал результата"
        try:
            made = judged(result, cut=topic == "conform.reverse")
        except Passing:
            raise Passing(f"сбой сервиса в задаче {job_id}: {why}") from None
        if made is None:
            what = "критичные замечания" if result and result.get("ok") else why
            self.log.warning("%s: сервис выравнивания отказал в задаче %s, записано как неудача: %s", label, job_id, what)
        return made

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

    # --- the work of the run ---------------------------------------------------------------------------------

    def contribute(self, series_id: int, flags: dict[str, bool]) -> None:
        """What the sync is at (downloading, aligning, packing); the card is told it with what the series stands at."""
        self._activity.setdefault(series_id, {}).update(flags)
        self._tell_status(series_id)

    def _stand(self, series_id: int, view: dict) -> None:
        """What the series stands at, told by its view: ready — a file of it is in the library; waiting — the plan has
        work for the next sync; error — an attempt failed (recorded), or work of the last sync failed for the moment."""
        work = any(goal["video"]["kind"] != "asset" or any(track["kind"] != "reuse" for track in goal["tracks"])
                   for goal in view["episodes"]) or bool(view["commands"])
        self._standing[series_id] = {"ready": bool(view["assets"]), "waiting": work and not view["running"],
                                     "error": bool(view["failures"] or view["problems"])}
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
        online = frozenset(row.id for row in inputs.online) if inputs is not None else None
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
