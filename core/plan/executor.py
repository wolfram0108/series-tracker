"""The executor of the plan of a series (stage 12, the table of an episode).

One sync: the plan is counted from the snapshot (the asset as it is, the tables now) and its graph runs, every step on the
signal that the steps before it ended. The plan is of the series, the work is of each episode: a group — the episodes
and files that share work — is applied (its containers built aside, its journal carried out, the table written) as soon as
its own steps have ended, whatever the other episodes still do. An attempt that fails is recorded and the plan is counted
again at once from the asset as it is by then, with the record: a step the new plan asks the same work of is not done
twice, its episode goes on with the next way while the others run. A group is applied only by a plan made after
everything known by then. The sync ends when every group is applied or left. The steps are the subclass's: the service
does them through its modules, the simulator through its models."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Callable

from ..reasons import reason
from .planner import Goal, Group, Op, Plan, plan, video_op
from .tables import Choices, Container, Inputs, OnlineRow, RowId, Slot, Track, Video

DOWNLOADS = ("download_video", "download_dub", "download_picture")


class Passing(Exception):
    """A step failed for this sync only (the network, an answer that never came): no record, tried again next time."""


class Crashed(Passing):
    """The service took this very pair (a voice-over onto a video, a picture onto an episode) and fell on it: it judged
    nothing of the pair, so nothing is recorded, but the attempt is passed over for this sync like a download that did not
    come, and the plan goes on with the next source (3.7). Waiting for the service would only repeat the same fall."""


@dataclass(frozen=True)
class Made:
    """What a step made: `value` (a Video, a Track, the row of a voice-over, None for a compilation) and its file.
    `retimed`: the video downloaded is of another length than the file of its row the library holds — its timeline is
    another one, and what the library holds aligned to that row is no longer in step with it."""

    value: object
    file: object = None
    retimed: bool = False


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
    and turned down (`refused`) is a failure (None, recorded). A pair the service fell on (its result is there, with no
    file and no decision of its gates) said nothing of the voice-over: it is `Crashed`, passed over for this sync with the
    next source taken. No result at all (the job did not come to the pair, the answer never came) says nothing of the pair
    either, and the service may only be late: `Passing`, the episode waits for it unchanged."""
    if result is None:
        raise Passing
    if result.get("ok") and result.get("out_path"):
        return None if cut and result.get("critical") else result["out_path"]
    if result.get("refused"):
        return None
    raise Crashed


def same_work(before: Op, op: Op) -> bool:
    """Whether a step of a new plan asks the work a step of this sync already does or did: a download of the same row and
    a cut of the same compilation, an alignment of the same voice-over onto the same video. A build is always made anew
    of its parts."""
    if op.kind == "build" or before.kind != op.kind:
        return False
    if op.kind == "align":
        (was, old), (goal, way) = before.subject, op.subject
        return old.dub == way.dub and was.video.key == goal.video.key and was.video.timeline == goal.video.timeline
    return before.subject == op.subject


@dataclass(eq=False)
class Work:
    """A step of this sync, under way or ended: the task resolves to what it made (None: it failed or was left)."""

    op: Op
    task: "asyncio.Future"
    left: bool = False  # a step before it failed: it was not tried
    batch: "Batch | None" = None  # the piece of work it goes in, for a step of a batch


@dataclass(eq=False)
class Batch:
    """The steps of one batch that go as one piece of work, across the plans of a sync: until it is sent, its members are
    the steps of the plan in force (a step a later plan replaced leaves it, the step that replaced it joins), so a plan
    made again while an episode waits for its video sends the episode as one job still. Once sent it changes no more."""

    members: list[Work]
    arrived: dict[int, bool] = field(default_factory=dict)  # id of a member -> it was ready
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    outcomes: dict = field(default_factory=dict)
    sent: bool = False

    def holds(self, entry: Work) -> bool:
        return any(member is entry for member in self.members)

    def complete(self) -> bool:
        return not self.sent and all(id(member) in self.arrived for member in self.members)


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


def outstanding(found: list[tuple], made: Plan, applied: list) -> tuple[tuple, ...]:
    """What the outcome of a sync still lacks: a step that failed for the moment is no problem once another way gave what
    it was for — its episode was applied, holding the voice-over the step was for (the next source of 3.7)."""
    done = {slot for slots in applied for slot in slots}
    kept = []
    for slot, kind, voice, reason in found:
        goal = made.goal(slot) if slot is not None else None
        given = goal is not None and slot in done and (
            voice is None or voice == goal.video.snapshot.group or voice in {way.voice for way in goal.tracks})
        if not given:
            kept.append((slot, kind, voice, reason))
    return tuple(kept)


def attempt_of(op: Op, results: dict[str, Made]) -> tuple | None:
    """The attempt a step is, as the planner reads its failure: a download of a row, an alignment of a voice-over onto a
    timeline, a cut of a compilation onto one; None for a step whose failure the planner has no record of (a build)."""
    if op.kind in DOWNLOADS:
        return ("download", op.subject.id)
    if op.kind == "align":
        goal, way = op.subject
        return ("align", way.dub, video_of(goal, results).timeline)
    if op.kind == "cut":
        return ("cut", op.subject.row.id, op.subject.timeline)
    return None


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
        # Attempts that failed for this sync only: a download (a network error, a player that does not answer) and an
        # alignment or a cut the service fell on (`Crashed`). They are passed over in the plans of this sync like the
        # recorded ones (3.7: a row that did not come — the next source), never written, tried again by the next sync
        # that still lacks what they were for. An alignment or a cut whose answer never came is no such failure: its
        # episode waits for the service unchanged.
        self.skipped: set[tuple] = set()
        # The rows whose video this sync downloaded anew to another length (`Made.retimed`): the tracks the library holds
        # aligned to them are given to the plans of the sync as aligned to the file before, which is no longer the video.
        self.retimed: set[RowId] = set()
        self.applying = asyncio.Lock()  # one group of the sync is put in place at a time

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

    async def stage(self, made: Plan, group: Group, results: dict[str, Made]) -> object:
        """The files of a group made aside before anything of it is applied (the new containers); what it returns is
        given to `commit` (the steps that put them in place). The groups of a sync are staged side by side."""

    async def commit(self, before: dict[str, Container], staged: object = None) -> None:
        """A group is applied — `staged` is what its `stage` made: the journal of its files, the table written — one
        group at a time; and, with nothing staged, the end of the sync: what it recorded is written."""

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
        return Inputs(online, tuple(sorted((self._as_held(each) for each in self.containers.values()),
                                           key=lambda each: each.name)),
                      replace(choices, delete=self.commands), frozenset(self.failed | self.skipped))

    def _as_held(self, container: Container) -> Container:
        """A file as the plans of this sync see it: a track aligned to a row whose video came anew to another length is
        aligned to the file before (`<video>@before`), not to that row; so is a video cut onto the timeline of that row."""
        def before(row: RowId) -> RowId:
            return replace(row, video=f"{row.video}@before")

        video = container.video
        moved = video.cut_of is not None and video.timeline in self.retimed
        if not moved and not any(track.aligned_to in self.retimed for track in container.tracks):
            return container
        return replace(container, video=replace(video, timeline=before(video.timeline)) if moved else video,
                       tracks=tuple(replace(track, aligned_to=before(track.aligned_to))
                                    if track.aligned_to in self.retimed else track for track in container.tracks))

    async def plan_of(self, inputs: Inputs) -> Plan:
        first = plan(inputs, self.namer)
        namer = await self.namer_for(first)
        return first if namer is self.namer else plan(inputs, namer)

    async def sync(self, online: tuple[OnlineRow, ...], choices: Choices) -> Synced:
        # A command is of a file: a file that is gone takes its command with it.
        self.commands = frozenset(each for each in self.commands if each[1] in self.containers)
        self.skipped = set()  # what failed for the moment in the last sync is tried again
        self.retimed = set()
        self.passing = set()
        self.reasons = {}
        self.applying = getattr(self, "applying", None) or asyncio.Lock()  # one journal of the library at a time
        before = dict(self.containers)
        replanned: list[tuple] = []
        problems: list[tuple] = []
        applied: list[tuple[Slot, ...]] = []
        unbuilt: set[tuple[Slot, ...]] = set()  # the groups whose containers could not be built in this sync
        build_failed: set[str] = set()
        work: dict[str, Work] = {}  # the steps of the plan in force, by their ids
        every: list[Work] = []  # every step this sync began, the ones a later plan no longer asks among them
        batches: dict[str, Batch] = {}  # the batch of each key not sent yet, or the last one sent
        sends: list[asyncio.Task] = []  # the pieces of work of batches a new plan completed (no member was arriving)
        news: list[tuple[str, tuple]] = []  # what the steps learned since the plan in force was made
        told = asyncio.Event()  # a step learned something: the plan is made again
        applies: dict[asyncio.Task, tuple[Plan, Group]] = {}
        watchers: dict[asyncio.Task, Group] = {}
        round_ = 0
        made: Plan | None = None
        inputs = self.inputs(online, choices)

        def results_of(steps: dict[str, Work]) -> dict[str, Made]:
            """What the steps of a plan have made by now."""
            return {key: entry.task.result() for key, entry in steps.items()
                    if entry.task is not None and entry.task.done() and not entry.task.cancelled()
                    and entry.task.exception() is None and entry.task.result() is not None}

        def begin(plan_: Plan) -> dict[str, Work]:
            """The steps of a plan: one that asks the work a step of this sync does or did keeps that step, with what
            the steps before it are; any other begins anew."""
            by_id = {op.id: op for op in plan_.ops}
            missing = {need for op in plan_.ops for need in op.needs} - set(by_id)
            if missing:
                raise AssertionError(f"the graph names steps it does not have: {sorted(missing)}")
            kept: dict[str, bool] = {}

            def keeps(op: Op) -> bool:
                if op.id not in kept:
                    entry = work.get(op.id)
                    kept[op.id] = entry is not None and same_work(entry.op, op) and all(keeps(by_id[need]) for need in op.needs) \
                        and not (entry.task.done() and (entry.task.cancelled() or entry.task.exception() is not None))
                return kept[op.id]

            fresh = [op for op in plan_.ops if not keeps(op)]
            steps = {op.id: work[op.id] for op in plan_.ops if kept[op.id]}
            for op in fresh:
                steps[op.id] = Work(op, None)
                every.append(steps[op.id])
            regroup(plan_, steps)
            for op in fresh:
                steps[op.id].task = asyncio.ensure_future(one(steps[op.id], [steps[need] for need in op.needs], plan_, steps))
            return steps

        def regroup(plan_: Plan, steps: dict[str, Work]) -> None:
            """A batch starts once every step of it in the plan in force is ready or left behind (a step before it
            failed): the ready ones go as one piece of work, and each step takes its own outcome out of it. A batch not
            sent yet takes the steps of the new plan; the steps of one sent have their outcome there, and the new steps
            of its key make the next batch."""
            for key in dict.fromkeys(op.batch for op in plan_.ops if op.batch):
                members = [steps[op.id] for op in plan_.ops if op.batch == key]
                batch = batches.get(key)
                if batch is None or batch.sent:
                    members = [each for each in members if each.batch is None or not each.batch.sent]
                    if not members:
                        continue
                    batch = batches[key] = Batch(members)
                else:
                    batch.members = members
                for each in members:
                    each.batch = batch
                if batch.complete():  # the steps it still waited for were replaced by steps that already arrived
                    sends.append(asyncio.ensure_future(send(batch)))

        async def send(batch: Batch) -> None:
            batch.sent = True
            try:
                ready = [member.op for member in batch.members if batch.arrived[id(member)]]
                if ready:
                    batch.outcomes.update(await self.do_batch(ready, results_of(work)))
            finally:
                batch.settled.set()

        async def batch_of(entry: Work, ready: bool) -> bool:
            """The step arrives at its batch and waits for it; False for a step a later plan replaced (it is not sent)."""
            batch = entry.batch
            if batch is None or not batch.holds(entry):
                return False
            batch.arrived[id(entry)] = ready
            if batch.complete():
                await send(batch)
            await batch.settled.wait()
            return True

        async def one(entry: Work, needs: list[Work], plan_: Plan, steps: dict[str, Work]) -> Made | None:
            op = entry.op
            for need in needs:
                await asyncio.wait({need.task})
            ready = all(need.task.exception() is None and need.task.result() is not None for need in needs)
            await self.trace("step", {"op": op.id, "kind": op.kind, "slot": op.slot, "ready": ready})
            sent = await batch_of(entry, ready) if op.batch else False
            if not ready:
                entry.left = True
                await self.trace("done", {"op": op.id, "outcome": "left", "why": sorted(
                    need.op.id for need in needs if need.task.exception() is not None or need.task.result() is None)})
                return None
            if op.batch:
                outcome = (entry.batch.outcomes.get(op.id, Passing(reason("batch_not_done"))) if sent
                           else Passing(reason("step_replaced")))
            else:
                try:
                    outcome = await self.do(op, results_of(steps))
                except Passing as passing:
                    outcome = passing
            current = work.get(op.id) is entry  # a later plan asks other work of this step: what it learned is not told
            if isinstance(outcome, Passing):
                if current:
                    self.passing.add(op.id)
                    self.reasons[op.id] = str(outcome.args[0]) if outcome.args else ""
                    problems.extend(each for each in problems_of(plan_, {op.id}, self.reasons) if each not in problems)
                    passed_over = op.kind in DOWNLOADS or isinstance(outcome, Crashed)
                    attempt = attempt_of(op, results_of(steps)) if passed_over else None
                    if attempt is not None and attempt not in self.failed and attempt not in self.skipped:
                        news.append(("moment", attempt))
                        told.set()
                await self.trace("done", {"op": op.id, "outcome": "passing", "why": str(outcome.args[0]) if outcome.args else ""})
                return None
            if outcome is None:
                attempt = attempt_of(op, results_of(steps))
                if current and attempt is not None and attempt not in self.failed:
                    news.append(("recorded", attempt))
                    told.set()
                await self.trace("done", {"op": op.id, "outcome": "failed", "why": ""})
                return None
            if current and outcome.retimed and outcome.value.id not in self.retimed:
                # A video that came to another length: the plan is made again on the file as it is now (the step of the
                # download tells it; the planner has no rule for it — the tracks before are simply not aligned to it).
                news.append(("retimed", outcome.value.id))
                told.set()
            await self.trace("done", {"op": op.id, "outcome": "made", "made": outcome})
            return outcome

        async def ended(group: Group) -> Group:
            """The steps of a group, and the steps they need, have ended."""
            tasks = {work[each].task for each in group.ops}
            if tasks:
                await asyncio.wait(tasks)
            return group

        async def apply(plan_: Plan, group: Group, results: dict[str, Made]) -> bool:
            """A group whose steps all succeeded: its files are made aside (side by side with the other groups'), then
            put in place and the table written, one group at a time."""
            try:
                staged = await self.stage(plan_, group, results)
            except Exception as exc:  # noqa: BLE001 - a container that cannot be built leaves its group, not the sync
                # Its build is a step that failed for the moment: the next sync builds it again.
                build_failed.update(f"build:{slot}" for slot in group.built)
                problems.extend((slot, "build", None, getattr(exc, "code", "") or str(exc)) for slot in group.built)
                await self.trace("apply", {"slots": group.slots, "applied": False, "missing": ["build"], "error": str(exc)})
                await self.left(plan_, group, [f"build: {exc}"])
                return False
            async with self.applying:
                await self.trace("apply", {"slots": group.slots, "applied": True, "group": group})
                table = table_after(self.containers, plan_, group, results)
                commands = commands_after(self.commands, plan_, group)
                changed = bool(staged) or table != self.containers or commands != self.commands
                self.containers, self.commands = table, commands
                if changed:
                    await self.commit(before, staged)
            return True

        def take_news() -> bool:
            """What the steps learned is recorded; True when the plan has to be made again."""
            recorded = [key for kind, key in news if kind == "recorded" and key not in self.failed]
            moment = [key for kind, key in news if kind == "moment" and key not in self.failed and key not in self.skipped]
            retimed = sorted({key for kind, key in news if kind == "retimed"} - self.retimed, key=repr)
            news.clear()
            told.clear()
            if not recorded and not moment and not retimed:
                return False
            recorded, moment = list(dict.fromkeys(recorded)), list(dict.fromkeys(moment))
            self.failed |= set(recorded)
            self.skipped |= set(moment)
            self.retimed |= set(retimed)
            replanned.extend(recorded)
            self.note = {"recorded": recorded, "for_this_sync": moment, "retimed": retimed}
            return True

        stale, waiting = True, False
        try:
            while True:
                if stale:
                    for task in watchers:
                        task.cancel()
                    watchers.clear()
                    inputs = self.inputs(online, choices)
                    made = await self.plan_of(inputs)
                    round_ += 1
                    # The rows of the sources are those of the first plan for the whole sync: they are told once.
                    await self.trace("plan", {"round": round_, "inputs": inputs if round_ == 1 else replace(inputs, online=()),
                                              "plan": made})
                    work = begin(made)
                    busy = {slot for _, group in applies.values() for slot in group.slots}
                    stale = waiting = False
                    for group in made.groups:
                        if busy & set(group.slots):
                            waiting = True  # its episodes are being applied by the plan before: looked at after that
                        elif group.slots not in unbuilt:
                            watchers[asyncio.ensure_future(ended(group))] = group
                if not watchers and not applies:
                    break
                signal = asyncio.ensure_future(told.wait())
                try:
                    finished, _ = await asyncio.wait({signal, *watchers, *applies}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    signal.cancel()
                for task in [task for task in finished if task in applies]:
                    _plan, group = applies.pop(task)
                    if task.result():
                        if group.slots not in applied:
                            applied.append(group.slots)
                    else:
                        unbuilt.add(group.slots)
                    stale = stale or waiting
                if take_news():
                    # A group is applied only by a plan made after everything known: the plan is made again first.
                    await self.trace("replan", self.note)
                    stale = True
                    continue
                for task in [task for task in finished if task in watchers]:
                    group = watchers.pop(task)
                    task.result()
                    results = results_of(work)
                    if all(op in results for op in group.ops):
                        applies[asyncio.ensure_future(apply(made, group, results))] = (made, group)
            # The steps a later plan no longer asked end on their own: nothing of the sync works after it.
            await asyncio.wait({*(entry.task for entry in every), *sends}) if every or sends else None
            for task in [*(entry.task for entry in every), *sends]:
                task.result()
        except BaseException:
            for task in [*watchers, *applies, *sends, *(entry.task for entry in every if entry.task is not None)]:
                task.cancel()
            await asyncio.gather(*watchers, *applies, *sends, *(entry.task for entry in every if entry.task is not None),
                                 return_exceptions=True)
            raise
        results = results_of(work)
        failed = {op.id for op in made.ops if op.id not in results} | build_failed
        for group in made.groups:
            missing = sorted(op for op in group.ops if op not in results)
            if not missing:
                continue  # applied, or its containers could not be built (told when that happened)
            await self.trace("apply", {"slots": group.slots, "applied": False, "missing": missing})
            await self.left(made, group, missing)
        async with self.applying:
            await self.commit(before)
        return Synced(inputs, tuple(sorted(before.values(), key=lambda each: each.name)), made, tuple(failed),
                      tuple(applied), tuple(replanned), outstanding(problems, made, applied))
