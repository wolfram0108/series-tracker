"""The executor of the plan of a series (stage 12, the table of an episode).

One sync: the plan is counted from the snapshot (the asset as it is, the tables now); the graph runs; an attempt that
fails is recorded and the plan is counted again from the same snapshot with the record, until no new failure comes; then
every group whose operations all succeeded is applied — the new table of the asset, the files of the journal — once, at
the end. The steps are the subclass's: the service does them through its modules, the simulator through its models."""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, replace
from typing import Callable

from .planner import Goal, Group, Op, Plan, plan, video_op
from .tables import Choices, Container, Inputs, OnlineRow, Slot, Track, Video

DOWNLOADS = ("download_video", "download_dub", "download_picture")


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
    # The steps of the final run that failed for the moment, with why: (slot, kind, voice-over, reason).
    problems: tuple[tuple, ...] = ()


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


def carried(op: Op, done: dict[str, tuple[object, Made]], results: dict[str, Made]) -> Made | None:
    """What a run before in this sync made that this step asks again: a download of the same row and a cut of the same
    compilation, an alignment of the same voice-over onto the same video. A build is always made anew of its parts."""
    before = done.get(op.id)
    if before is None or op.kind == "build":
        return None
    subject, made = before
    if op.kind == "align":
        goal, way = op.subject
        track = made.value
        return made if track.dub == way.dub and track.aligned_to == video_of(goal, results).timeline else None
    return made if subject == op.subject else None


def video_of(goal: Goal, results: dict[str, Made]) -> Video:
    if goal.video.kind == "asset":
        return goal.video.container.video
    return results[video_op(goal)].value


def problems_of(made: Plan, passing: set[str], reasons: dict[str, str]) -> tuple[tuple, ...]:
    """The steps that failed for the moment, as the window is told them: the episode, the kind of the step, the voice-over
    it was for (an alignment, a download of a voice-over) and the reason."""
    found = []
    for op in made.ops:
        if op.id not in passing:
            continue
        voice = op.subject[1].voice if isinstance(op.subject, tuple) else getattr(op.subject, "group", None)
        # A step several episodes share (the download of a compilation) is told to each episode that waits for it.
        slots = [op.slot] if op.slot is not None else sorted({each.slot for each in made.ops if op.id in each.needs
                                                               and each.slot is not None})
        found += [(slot, op.kind, voice, reasons.get(op.id, "")) for slot in slots]
    return tuple(found)


def failures(made: Plan, results: dict[str, Made], failed: set[str], passing: set[str]) -> list[tuple]:
    """The attempts that failed in a run, as the planner reads them, but those named in `passing`; a step that failed
    because a step before it did is no attempt of its own."""
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
                tracks.append(replace(track, episode=slot, tag="", file=""))  # a new file keys it by its row and holds it
            elif way.kind == "timeline":
                tracks.append(Track(way.dub, way.snapshot, slot, video.timeline))
            else:
                tracks.append(results[f"align:{slot}:{way.voice}"].value)
        built[goal.name] = Container(goal.name, video, tuple(tracks), goal.off, goal.own_at, goal.titles, goal.languages)
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
    the video to the file's new name, or to the file built anew around it; a file that stays hidden (a sound in it is still
    needed) keeps the commands on it under its hidden name; a track's command goes once the file is built anew (the track
    it named is gone from it)."""
    renamed = {name: made.goal(slot).name for slot, name in group.kept}
    rebuilt = {goal.video.container.name: goal.name for slot in group.built
               if (goal := made.goal(slot)).video.kind == "asset"}
    hidden = dict(group.hide)
    changed = set(group.remove) | set(hidden)
    kept = set()
    for each in commands:
        name = each[1]
        if name in hidden:
            kept.add((each[0], hidden[name], *each[2:]))
        elif each[0] == "video" and (name in renamed or name in rebuilt):
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
        self.reasons: dict[str, str] = {}  # why a step failed for the moment, by its id
        # Downloads that failed for this sync only (a network error, a player that does not answer): passed over in the
        # plans of this sync like the recorded ones (3.7: a row not downloaded — the next source), never written, tried
        # again by the next sync. An alignment or a cut the service crashed on or never answered is no such failure:
        # its episode waits for the service unchanged.
        self.skipped: set[tuple] = set()

    # --- the steps: the subclass's ------------------------------------------------------------------------------

    async def do(self, op: Op, results: dict[str, Made]) -> Made | None:
        """One step; None when it failed (recorded), `Passing` when it failed for this sync only."""
        raise NotImplementedError

    async def do_batch(self, ops: list[Op], results: dict[str, Made]) -> dict[str, Made | None | Passing]:
        """The steps of a batch that are ready, as one piece of work: the outcome of each by its id (a `Passing` for one
        that failed for this sync only). By default each is done on its own."""
        async def each(op: Op) -> Made | None | Passing:
            try:
                return await self.do(op, results)
            except Passing as passing:
                return passing
        outcomes = await asyncio.gather(*(each(op) for op in ops))
        return {op.id: outcome for op, outcome in zip(ops, outcomes)}

    async def stage(self, made: Plan, group: Group, results: dict[str, Made]) -> None:
        """The files of a group made aside before anything is applied (the new containers)."""

    async def commit(self, before: dict[str, Container]) -> None:
        """The end of the sync: the journal of the files, the table written."""

    async def left(self, made: Plan, group: Group, missing: list[str]) -> None:
        """A group not applied: a step of it did not succeed (`missing`); its files stay as they are."""

    async def namer_for(self, made: Plan) -> Callable | None:
        """The rule of names for the goals of a plan (the service asks it of the rules); None: the planner's own."""
        return self.namer

    async def trace(self, kind: str, data: dict) -> None:
        """What the sync does, told as it happens for the dump of the run: `plan` (the inputs and the plan made of them),
        `step` and `done` (a step of the graph begins and ends, with its outcome), `apply` (a group applied or left)."""

    # --- the sync -----------------------------------------------------------------------------------------------

    def inputs(self, online: tuple[OnlineRow, ...], choices: Choices) -> Inputs:
        return Inputs(online, tuple(sorted(self.containers.values(), key=lambda each: each.name)),
                      replace(choices, delete=self.commands), frozenset(self.failed | self.skipped))

    async def plan_of(self, inputs: Inputs) -> Plan:
        first = plan(inputs, self.namer)
        namer = await self.namer_for(first)
        return first if namer is self.namer else plan(inputs, namer)

    async def sync(self, online: tuple[OnlineRow, ...], choices: Choices) -> Synced:
        # A command is of a file: a file that is gone takes its command with it.
        self.commands = frozenset(each for each in self.commands if each[1] in self.containers)
        self.skipped = set()  # what failed for the moment in the last sync is tried again
        inputs = self.inputs(online, choices)
        before = dict(self.containers)
        replanned: list[tuple] = []
        problems: list[tuple] = []
        done: dict[str, tuple[object, Made]] = {}
        round_ = 0
        while True:
            made = await self.plan_of(inputs)
            round_ += 1
            await self.trace("plan", {"round": round_, "inputs": inputs, "plan": made})
            results, failed = await self.run(made, done)
            problems += [each for each in problems_of(made, self.passing, self.reasons) if each not in problems]
            subjects = {op.id: op.subject for op in made.ops}
            done = {key: (subjects[key], value) for key, value in results.items()}
            new = [key for key in failures(made, results, failed, self.passing) if key not in self.failed]
            moment = [key for key in failures(made, results, failed, set()) if key[0] == "download"
                      and key not in new and key not in self.failed and key not in self.skipped]
            if not new and not moment:
                break
            self.failed |= set(new)
            self.skipped |= set(moment)
            replanned += new
            await self.trace("replan", {"recorded": new, "for_this_sync": moment})
            inputs = self.inputs(online, choices)
        applied = []
        for group in made.groups:
            if any(op not in results for op in group.ops):
                missing = sorted(op for op in group.ops if op not in results)
                await self.trace("apply", {"slots": group.slots, "applied": False, "missing": missing})
                await self.left(made, group, missing)
                continue
            await self.trace("apply", {"slots": group.slots, "applied": True, "group": group})
            await self.stage(made, group, results)
            self.containers = table_after(self.containers, made, group, results)
            self.commands = commands_after(self.commands, made, group)
            applied.append(group.slots)
        await self.commit(before)
        return Synced(inputs, tuple(sorted(before.values(), key=lambda each: each.name)), made, tuple(failed),
                      tuple(applied), tuple(replanned), tuple(problems))

    async def run(self, made: Plan, done: dict[str, tuple[object, Made]] | None = None) -> tuple[dict[str, Made], set[str]]:
        """The graph: a step starts on the signal that every step before it ended, so independent work runs at once.
        `done` is what a run of this sync made before a new failure made the plan again: a step the new plan asks the
        same of is not done twice (`carried`)."""
        done = done or {}
        self.passing = set()
        self.reasons = {}
        results: dict[str, Made] = {}
        failed: set[str] = set()
        ended = {op.id: asyncio.Event() for op in made.ops}
        missing = {need for op in made.ops for need in op.needs} - set(ended)
        if missing:
            raise AssertionError(f"the graph names steps it does not have: {sorted(missing)}")

        # A batch starts once every step of it is ready or left behind (a step before it failed): the ready ones go as
        # one piece of work, and each step takes its own outcome out of it.
        sizes = Counter(op.batch for op in made.ops if op.batch)
        arrived: dict[str, list[Op | None]] = {key: [] for key in sizes}
        settled = {key: asyncio.Event() for key in sizes}
        outcomes: dict[str, Made | None | Passing] = {}

        async def batch_of(op: Op, ready: bool) -> None:
            kept = carried(op, done, results) if ready else None
            if kept is not None:
                outcomes[op.id] = kept
            arrived[op.batch].append(op if ready and kept is None else None)
            if len(arrived[op.batch]) == sizes[op.batch]:
                try:
                    members = [each for each in arrived[op.batch] if each is not None]
                    if members:
                        outcomes.update(await self.do_batch(members, results))
                finally:
                    settled[op.batch].set()
            await settled[op.batch].wait()

        async def one(op: Op) -> None:
            try:
                for need in op.needs:
                    await ended[need].wait()
                ready = not any(need in failed for need in op.needs)
                await self.trace("step", {"op": op.id, "kind": op.kind, "slot": op.slot, "ready": ready})
                if op.batch:
                    await batch_of(op, ready)
                if not ready:
                    failed.add(op.id)
                    await self.trace("done", {"op": op.id, "outcome": "left", "why": sorted(need for need in op.needs if need in failed)})
                    return
                if op.batch:
                    outcome = outcomes.get(op.id, Passing("партия не выполнена"))
                elif (kept := carried(op, done, results)) is not None:
                    outcome = kept
                else:
                    try:
                        outcome = await self.do(op, results)
                    except Passing as passing:
                        outcome = passing
                if isinstance(outcome, Passing):
                    self.passing.add(op.id)
                    self.reasons[op.id] = str(outcome.args[0]) if outcome.args else ""
                    outcome = None
                if outcome is None:
                    failed.add(op.id)
                    await self.trace("done", {"op": op.id, "outcome": "passing" if op.id in self.passing else "failed",
                                              "why": self.reasons.get(op.id, "")})
                else:
                    results[op.id] = outcome
                    await self.trace("done", {"op": op.id, "outcome": "made", "made": outcome})
            finally:
                ended[op.id].set()
        await asyncio.gather(*(one(op) for op in made.ops))
        return results, failed
