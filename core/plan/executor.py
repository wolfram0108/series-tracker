"""The executor of the plan of a series (stage 12, the table of an episode).

One sync: the plan is counted from the snapshot (the asset as it is, the tables now); the graph runs; an attempt that
fails is recorded and the plan is counted again from the same snapshot with the record, until no new failure comes; then
every group whose operations all succeeded is applied — the new table of the asset, the files of the journal — once, at
the end. The steps are the subclass's: the service does them through its modules, the simulator through its models."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Callable

from .planner import Goal, Group, Op, Plan, plan
from .tables import Choices, Container, Inputs, OnlineRow, Slot, Track, Video

DOWNLOADS = ("download_video", "download_dub", "download_compilation")


class Passing(Exception):
    """A step failed for this sync only (the network, an answer that never came): no record, tried again next time."""


@dataclass(frozen=True)
class Made:
    """What a step made: `value` (a Video, a Track, the row of a voice-over, None for a compilation) and its file."""

    value: object
    file: object = None


@dataclass(frozen=True)
class Synced:
    """What one sync saw and did."""

    inputs: Inputs  # the inputs of the final plan
    before: tuple[Container, ...]
    plan: Plan
    failed: tuple[str, ...]  # the steps that failed in the final run
    applied: tuple[tuple[Slot, ...], ...]
    replanned: tuple[tuple, ...]  # the attempts recorded in this sync


def judged(result: dict | None, *, cut: bool) -> str | None:
    """The file a job of the alignment service made, by the service's own verdict: an aligned track is taken by `ok` (what
    it found critical is a mark, the track is in step); a cut needs `ok` and nothing critical. A pair the service looked at
    and turned down (`refused`) is a failure (None, recorded); any other end — a crash in the service, no result — said
    nothing of the pair and is a failure of the moment (`Passing`)."""
    if result is None:
        raise Passing
    if result.get("ok") and result.get("out_path"):
        return None if cut and result.get("critical") else result["out_path"]
    if result.get("refused"):
        return None
    raise Passing


def video_of(goal: Goal, results: dict[str, Made]) -> Video:
    if goal.video.kind == "asset":
        return goal.video.container.video
    return results[f"video:{goal.slot}" if goal.video.kind == "download" else f"cut:{goal.slot}"].value


def failures(made: Plan, results: dict[str, Made], failed: set[str], passing: set[str]) -> list[tuple]:
    """The attempts that failed in a run, as the planner reads them; a step that failed because a step before it did, or
    for this sync only, is no attempt of its own."""
    found = []
    for op in made.ops:
        if op.id not in failed or op.id in passing or any(need in failed for need in op.needs):
            continue
        if op.kind in DOWNLOADS:
            found.append(("download", op.subject.id))
        elif op.kind == "align":
            goal, way = op.subject
            found.append(("align", way.dub, video_of(goal, results).timeline))
        elif op.kind == "cut":
            found.append(("cut", op.subject.row.id, op.subject.timeline))
    return found


def table_after(containers: dict[str, Container], made: Plan, group: Group, results: dict[str, Made]) -> dict[str, Container]:
    """The asset once a group is applied: the built containers, the kept ones under their names, the hidden ones."""
    after = dict(containers)
    built: dict[str, Container] = {}
    for slot in group.built:
        goal = made.goal(slot)
        video = replace(video_of(goal, results), episode=slot)
        tracks = []
        for way in goal.tracks:
            if way.kind == "reuse" and way.own:
                tracks.append(Track(way.dub, way.snapshot, slot, way.container.video.timeline))
            elif way.kind == "reuse":
                track = next(each for each in way.container.tracks if each.dub == way.dub)
                tracks.append(replace(track, episode=slot, tag=""))  # a new file keys it by its row
            else:
                tracks.append(results[f"align:{slot}:{way.voice}"].value)
        built[goal.name] = Container(goal.name, video, tuple(tracks), goal.off)
    renamed: dict[str, Container] = {}
    for slot, name in group.kept:
        goal = made.goal(slot)
        if goal.name != name:
            old = after.pop(name)
            renamed[goal.name] = replace(old, name=goal.name, off=goal.off, video=replace(old.video, episode=slot),
                                         tracks=tuple(replace(each, episode=slot) for each in old.tracks))
    for name in group.remove:
        after.pop(name, None)
    for name, new in group.hide:
        old = after.pop(name)
        after[new] = replace(old, name=new, off=True)
    after.update(renamed)
    after.update(built)
    return after


def commands_after(commands: frozenset, made: Plan, group: Group) -> frozenset:
    """A command «Удалить» goes with its file. A deleted video waits for another video of its episode: its command follows
    the video to the file's new name, or to the file built anew around it; a track's command goes once the file is built
    anew (the track it named is gone from it)."""
    renamed = {name: made.goal(slot).name for slot, name in group.kept}
    rebuilt = {goal.video.container.name: goal.name for slot in group.built
               if (goal := made.goal(slot)).video.kind == "asset"}
    changed = set(group.remove) | {name for name, _ in group.hide}
    kept = set()
    for each in commands:
        name = each[1]
        if each[0] == "video" and (name in renamed or name in rebuilt):
            kept.add(("video", renamed.get(name) or rebuilt[name]))
        elif each[0] == "track" and name in renamed:
            kept.add(("track", renamed[name], *each[2:]))
        elif name not in changed and name not in rebuilt:
            kept.add(each)
    return frozenset(kept)


class SeriesSync:
    """The sync of one series. `containers` is the asset, `failed` the attempts recorded, `commands` the commands
    «Удалить» waiting; a subclass does the steps (`do`), makes the files of a group (`stage`) and writes the result
    (`commit`)."""

    def __init__(self, namer: Callable | None = None) -> None:
        self.namer = namer
        self.containers: dict[str, Container] = {}
        self.failed: set[tuple] = set()
        self.commands: frozenset = frozenset()
        self.passing: set[str] = set()

    # --- the steps: the subclass's ------------------------------------------------------------------------------

    async def do(self, op: Op, results: dict[str, Made]) -> Made | None:
        """One step; None when it failed (recorded), `Passing` when it failed for this sync only."""
        raise NotImplementedError

    async def stage(self, made: Plan, group: Group, results: dict[str, Made]) -> None:
        """The files of a group made aside before anything is applied (the new containers)."""

    async def commit(self, before: dict[str, Container]) -> None:
        """The end of the sync: the journal of the files, the table written."""

    async def namer_for(self, made: Plan) -> Callable | None:
        """The rule of names for the goals of a plan (the service asks it of the rules); None: the planner's own."""
        return self.namer

    # --- the sync -----------------------------------------------------------------------------------------------

    def inputs(self, online: tuple[OnlineRow, ...], choices: Choices) -> Inputs:
        return Inputs(online, tuple(sorted(self.containers.values(), key=lambda each: each.name)),
                      replace(choices, delete=self.commands), frozenset(self.failed))

    async def plan_of(self, inputs: Inputs) -> Plan:
        first = plan(inputs, self.namer)
        namer = await self.namer_for(first)
        return first if namer is self.namer else plan(inputs, namer)

    async def sync(self, online: tuple[OnlineRow, ...], choices: Choices) -> Synced:
        # A command is of a file: a file that is gone takes its command with it.
        self.commands = frozenset(each for each in self.commands if each[1] in self.containers)
        inputs = self.inputs(online, choices)
        before = dict(self.containers)
        replanned: list[tuple] = []
        while True:
            made = await self.plan_of(inputs)
            results, failed = await self.run(made)
            new = [key for key in failures(made, results, failed, self.passing) if key not in self.failed]
            if not new:
                break
            self.failed |= set(new)
            replanned += new
            inputs = self.inputs(online, choices)
        applied = []
        for group in made.groups:
            if any(op not in results for op in group.ops):
                continue
            await self.stage(made, group, results)
            self.containers = table_after(self.containers, made, group, results)
            self.commands = commands_after(self.commands, made, group)
            applied.append(group.slots)
        await self.commit(before)
        return Synced(inputs, tuple(sorted(before.values(), key=lambda each: each.name)), made, tuple(failed),
                      tuple(applied), tuple(replanned))

    async def run(self, made: Plan) -> tuple[dict[str, Made], set[str]]:
        """The graph: a step starts on the signal that every step before it ended, so independent work runs at once."""
        self.passing = set()
        results: dict[str, Made] = {}
        failed: set[str] = set()
        ended = {op.id: asyncio.Event() for op in made.ops}
        missing = {need for op in made.ops for need in op.needs} - set(ended)
        if missing:
            raise AssertionError(f"the graph names steps it does not have: {sorted(missing)}")

        async def one(op: Op) -> None:
            try:
                for need in op.needs:
                    await ended[need].wait()
                if any(need in failed for need in op.needs):
                    failed.add(op.id)
                    return
                try:
                    outcome = await self.do(op, results)
                except Passing:
                    self.passing.add(op.id)
                    outcome = None
                if outcome is None:
                    failed.add(op.id)
                else:
                    results[op.id] = outcome
            finally:
                ended[op.id].set()
        await asyncio.gather(*(one(op) for op in made.ops))
        return results, failed
