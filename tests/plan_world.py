"""A world for the planner of a series (core/plan): sources, a library and an alignment service with no real files.

The world knows what the planner never sees: the true episode behind every video (`content`). Downloading puts a file
into the asset, cutting takes a single episode out of a compilation, and aligning succeeds only when the voice-over and
the video are the same episode — so a rule that numbers a row wrongly fails where the real service fails. Failures of
downloads and alignments are set by a scenario.

`sync()` is one synchronization: read the sources (a source whose read fails keeps its last table), plan, run the graph,
apply every group whose operations all succeeded. After each sync `check()` holds the invariants of the model."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Iterable

from core.plan.planner import OFF, Goal, Plan, plan, voice_of
from core.plan.tables import Choices, Container, Inputs, OnlineRow, RowId, Slot, Track, Video


DOWNLOADS = ("download_video", "download_dub", "download_compilation")


def truth_of(season: int, episode: int) -> int:
    """What a video truly is: one number for a season and an episode."""
    return season * 1000 + episode


@dataclass
class SiteRow:
    """A row as a source has it, with the true episode behind its video (a compilation holds a range of them)."""

    row: OnlineRow
    content: tuple[int, ...]


@dataclass
class Step:
    """What one sync saw and did."""

    title: str
    online: tuple[OnlineRow, ...]
    before: tuple[Container, ...]
    plan: Plan
    failed: tuple[str, ...]
    applied: tuple[tuple[Slot, ...], ...]
    after: tuple[Container, ...]
    choices: Choices
    # The choices after the sync: an applied «Удалить» is spent.
    choices_after: Choices | None = None
    # The downloads that failed, as the final plan of the sync knew them, and the rows passed over by replanning.
    failed_rows: frozenset[tuple] = frozenset()
    replanned: tuple[tuple, ...] = ()


@dataclass
class World:
    choices: Choices = field(default_factory=lambda: Choices(players=("vk", "cvh", "kodik"), sources=(1, 2, 3)))
    site: dict[int, list[SiteRow]] = field(default_factory=dict)  # source id -> its table now
    read_fails: set[int] = field(default_factory=set)  # sources whose next reads fail
    download_fails: set[RowId] = field(default_factory=set)
    failed: set[tuple] = field(default_factory=set)  # attempts that failed: the planner passes over them
    align_fails: set[tuple[Slot, str]] = field(default_factory=set)
    # The answers of the service by (episode, voice-over): "mismatch" and "critical" fail and are recorded, "suspect" is
    # taken, "lost" (the answer never came) fails for this sync only. A cut by episode: "critical" or "lost".
    answers: dict[tuple[Slot, str], str] = field(default_factory=dict)
    cut_answers: dict[Slot, str] = field(default_factory=dict)
    transient: set[RowId] = field(default_factory=set)  # downloads that fail for this sync only (a network error)
    containers: dict[str, Container] = field(default_factory=dict)
    online: dict[int, tuple[OnlineRow, ...]] = field(default_factory=dict)  # source id -> last complete read
    content: dict[tuple, tuple[int, ...]] = field(default_factory=dict)  # video key or ("dub", row) -> true episodes
    history: list[Step] = field(default_factory=list)

    # --- the sources -------------------------------------------------------------------------------------------

    def put(self, source_id: int, key: str, episode: int, *, group: str | None, player: str = "vk", height: int = 720,
            season: int = 1, end: int | None = None, video: str = "", content: Iterable[int] | None = None,
            name: str = "") -> OnlineRow:
        """A row of a source: what the rules give it (season, episode or range, group) and what it truly is."""
        row = OnlineRow(RowId(source_id, key, video or key), season, episode, end or episode, player, group, height,
                        source=name or f"источник {source_id}", link=f"https://site/{source_id}/{key}")
        truth = tuple(content) if content is not None else tuple(truth_of(season, number)
                                                                 for number in range(episode, (end or episode) + 1))
        table = [each for each in self.site.setdefault(source_id, []) if each.row.id.row_key != key]
        self.site[source_id] = table + [SiteRow(row, truth)]
        return row

    def drop(self, source_id: int, key: str) -> None:
        """The source forgets the row."""
        self.site[source_id] = [each for each in self.site.get(source_id, []) if each.row.id.row_key != key]

    def renumber(self, source_id: int, key: str, episode: int) -> None:
        """A rule gives the row another number; what the video truly is does not change."""
        for each in self.site.get(source_id, []):
            if each.row.id.row_key == key:
                each.row = replace(each.row, start=episode, end=episode)

    def move(self, source_id: int, key: str, season: int, episode: int) -> None:
        """A rule gives the row another season and number."""
        for each in self.site.get(source_id, []):
            if each.row.id.row_key == key:
                span = each.row.end - each.row.start
                each.row = replace(each.row, season=season, start=episode, end=episode + span)

    def lose_file(self, name: str) -> None:
        """The file is gone from the disk by no doing of the service (deleted by hand, a disk failed)."""
        self.containers.pop(name, None)

    def calm(self) -> None:
        """The faults are over and the user resets the failed attempts."""
        self.download_fails.clear()
        self.transient.clear()
        self.align_fails.clear()
        self.answers.clear()
        self.cut_answers.clear()
        self.read_fails.clear()
        self.reset_failures()

    def settle(self, syncs: int = 4) -> Step:
        """After `calm`, the series comes to rest: within a few syncs the plan has nothing left to do."""
        for number in range(syncs):
            step = self.sync(f"успокоение {number + 1}")
            self.check(step)
            if not step.plan.ops and not any(group.remove or group.hide for group in step.plan.groups):
                return step
        raise AssertionError(f"the series does not come to rest: {[op.id for op in step.plan.ops][:8]}")

    def choose(self, **changes) -> None:
        self.choices = replace(self.choices, **changes)

    def delete(self, *command) -> None:
        self.choose(delete=self.choices.delete | {tuple(command)})

    # --- one synchronization -------------------------------------------------------------------------------------

    def inputs(self) -> Inputs:
        rows = tuple(row for source in sorted(self.online) for row in self.online[source])
        return Inputs(rows, tuple(sorted(self.containers.values(), key=lambda each: each.name)), self.choices,
                      frozenset(self.failed))

    def sync(self, title: str = "") -> Step:
        for source_id, table in self.site.items():
            if source_id not in self.read_fails:
                self.online[source_id] = tuple(each.row for each in table)
                for each in table:
                    self.content.setdefault(("site", each.row.id), each.content)
        # The snapshot: the plan is counted from what is on disk and the tables now; nothing on disk changes until the end.
        # A download that fails is no failure of the plan: the row is passed over and the plan is counted again from the
        # same snapshot. An alignment or a cut that fails is: its group is not applied.
        inputs = self.inputs()
        before = inputs.containers
        replanned: list[tuple] = []
        while True:
            made = plan(inputs)
            results, failed = self._run(made)
            new = [key for key in self._failures(made, results, failed) if key not in self.failed]
            if not new:
                break
            self.failed |= set(new)
            replanned += new
            inputs = self.inputs()
        applied = []
        for group in made.groups:
            if any(op not in results for op in group.ops):
                continue
            self._apply(made, group, results)
            applied.append(group.slots)
        step = Step(title, inputs.online, before, made, tuple(failed), tuple(applied),
                    tuple(sorted(self.containers.values(), key=lambda each: each.name)), inputs.choices, self.choices,
                    inputs.failed, tuple(replanned))
        self.history.append(step)
        return step

    def _failures(self, made: Plan, results, failed) -> list[tuple]:
        """The attempts that failed in a run, as the planner reads them; a step that failed because a step before it did
        is no attempt of its own."""
        found = []
        for op in made.ops:
            if op.id not in failed or op.id in self._passing or any(need in failed for need in op.needs):
                continue
            if op.kind in DOWNLOADS:
                found.append(("download", op.subject.id))
            elif op.kind == "align":
                goal, way = op.subject
                found.append(("align", way.dub, self._video_of(goal, results).timeline))
            elif op.kind == "cut":
                found.append(("cut", op.subject.row.id, op.subject.timeline))
        return found

    def reset_failures(self) -> None:
        """The user's word: the failed attempts are tried again."""
        self.failed.clear()

    def _run(self, made: Plan) -> tuple[dict[str, object], list[str]]:
        self._passing: set[str] = set()  # steps that failed for this sync only: no record, tried again next time
        results: dict[str, object] = {}
        failed: list[str] = []
        pending = list(made.ops)
        while pending:
            ready = [op for op in pending if all(need in results or need in failed for need in op.needs)]
            for op in ready:
                pending.remove(op)
                if any(need in failed for need in op.needs):
                    failed.append(op.id)
                    continue
                outcome = self._do(op, results)
                if outcome is None:
                    failed.append(op.id)
                else:
                    results[op.id] = outcome
            if not ready:
                raise AssertionError(f"the graph has a cycle or a missing step: {[op.id for op in pending]}")
        return results, failed

    def _truth(self, key: tuple) -> tuple[int, ...]:
        return self.content[key]

    def _do(self, op, results):
        if op.kind in DOWNLOADS and op.subject.id in self.transient:
            self._passing.add(op.id)
            return None
        if op.kind == "download_video":
            row = op.subject
            if row.id in self.download_fails:
                return None
            video = Video(row.id, row.snapshot(), row.slot, row.id)
            self.content[video.key] = self._truth(("site", row.id))
            return video
        if op.kind == "download_compilation":
            return None if op.subject.id in self.download_fails else self._truth(("site", op.subject.id))
        if op.kind == "cut":
            way = op.subject
            ref2 = way.ref2.container.video if way.ref2.kind == "asset" else results[f"ref2:{op.slot}"]
            inside = results[f"compilation:{way.row.id}"]
            truth = self.content[ref2.key]
            answer = self.cut_answers.get(op.slot)
            if answer == "lost":
                self._passing.add(op.id)
                return None
            if answer == "critical" or not set(truth) <= set(inside):
                return None  # the single episode is not in the compilation: nothing matches
            video = Video(way.row.id, way.snapshot, op.slot, ref2.timeline, cut_of=way.row.id)
            self.content[video.key] = truth
            return video
        if op.kind == "download_dub":
            row = op.subject
            if row.id in self.download_fails:
                return None
            self.content[("dub", row.id)] = self._truth(("site", row.id))
            return row
        if op.kind == "align":
            goal, way = op.subject
            video = self._video_of(goal, results)
            dub = (self.content[("dub", way.dub)] if way.kind == "download"
                   else self.content[way.container.video.key] if way.own
                   else self.content[("track", way.container.name, way.dub)])
            answer = self.answers.get((goal.slot, way.voice))
            if answer == "lost":
                self._passing.add(op.id)
                return None
            if (goal.slot, way.voice) in self.align_fails or answer in ("mismatch", "critical") or \
                    dub != self.content[video.key]:
                return None
            return Track(way.dub, way.snapshot, goal.slot, video.timeline), dub
        if op.kind == "build":
            return op.subject
        raise AssertionError(op.kind)

    def _video_of(self, goal: Goal, results) -> Video:
        if goal.video.kind == "asset":
            return goal.video.container.video
        return results[f"video:{goal.slot}" if goal.video.kind == "download" else f"cut:{goal.slot}"]

    def _apply(self, made: Plan, group, results) -> None:
        built: dict[str, Container] = {}
        for slot in group.built:
            goal = made.goal(slot)
            video = replace(self._video_of(goal, results), episode=slot)
            tracks, truths = [], {}
            for way in goal.tracks:
                if way.kind == "reuse" and way.own:
                    tracks.append(Track(way.dub, way.snapshot, slot, way.container.video.timeline))
                    truths[way.dub] = self.content[way.container.video.key]
                elif way.kind == "reuse":
                    track = next(each for each in way.container.tracks if each.dub == way.dub)
                    tracks.append(replace(track, episode=slot))
                    truths[way.dub] = self.content[("track", way.container.name, way.dub)]
                else:
                    track, truth = results[f"align:{slot}:{way.voice}"]
                    tracks.append(track)
                    truths[way.dub] = truth
            built[goal.name] = Container(goal.name, video, tuple(tracks), goal.off)
            for dub, truth in truths.items():
                self.content[("track", goal.name, dub)] = truth
        renamed: dict[str, Container] = {}
        for slot, name in group.kept:
            goal = made.goal(slot)
            if goal.name != name:
                old = self.containers.pop(name)
                renamed[goal.name] = replace(old, name=goal.name, off=goal.off, video=replace(old.video, episode=slot),
                                             tracks=tuple(replace(each, episode=slot) for each in old.tracks))
                self._move_truth(name, goal.name, old)
        for name in group.remove:
            self.containers.pop(name, None)
        for name, new in group.hide:
            old = self.containers.pop(name)
            self.containers[new] = replace(old, name=new, off=True)
            self._move_truth(name, new, old)
        self.containers.update(renamed)
        self.containers.update(built)
        files = set(group.remove) | set(group.hide) | {name for _, name in group.kept}
        self.choose(delete=frozenset(each for each in self.choices.delete if each[1] not in files))

    def _move_truth(self, old: str, new: str, container: Container) -> None:
        for track in container.tracks:
            self.content[("track", new, track.dub)] = self.content[("track", old, track.dub)]

    # --- the invariants of the model -----------------------------------------------------------------------------

    def check(self, step: Step | None = None) -> None:
        step = step or self.history[-1]
        again = plan(Inputs(step.online, step.before, step.choices, step.failed_rows))
        assert repr(again) == repr(step.plan), "the plan is not a function of its inputs"
        online = {row.id: row for row in step.online}

        def slot_of_track(track: Track) -> Slot:
            row = online.get(track.dub)
            return row.slot if row is not None and not row.compilation else track.episode

        for container in step.after:
            voices = [voice_of(track.group, track.dub) for track in container.tracks]
            assert len(voices) == len(set(voices)), f"{container.name}: two tracks of one voice-over"
            own = voice_of(container.video.snapshot.group, container.video.timeline)
            assert own not in voices, f"{container.name}: the own sound is also a track"
            for track in container.tracks:
                assert track.aligned_to == container.video.timeline, f"{container.name}: a track out of sync"
        # Nothing goes but what the goal holds anew or what the user deleted.
        standing = {_slot_of_video(each.video, online) for each in step.after}
        deleted = step.choices.delete
        for container in step.before:
            commanded = ("video", container.name) in deleted
            if not commanded and not any(each.video.key == container.video.key for each in step.after):
                assert _slot_of_video(container.video, online) in standing, \
                    f"the video of {container.name} went with nothing in its place"
            for track in container.tracks:
                if commanded or ("track", container.name, track.dub) in deleted:
                    continue
                voice, here = voice_of(track.group, track.dub), slot_of_track(track)
                # It lives on: the same track anywhere, or its voice-over in the container of its episode — as a track
                # or as the own sound of the video.
                lives = any(each.dub == track.dub or (voice_of(each.group, each.dub) == voice
                                                      and _slot_of_video(other.video, online) == here)
                            for other in step.after for each in other.tracks) or any(
                    voice_of(other.video.snapshot.group, other.video.timeline) == voice
                    and _slot_of_video(other.video, online) == here for other in step.after)
                assert lives, f"the track {voice} of {container.name} went with nothing in its place"
        # The own sound of a video that went lives on: the same video elsewhere, or its voice-over in the container of its
        # episode (as the own sound or as a track).
        for container in step.before:
            if ("video", container.name) in deleted or any(each.video.key == container.video.key for each in step.after):
                continue
            voice = voice_of(container.video.snapshot.group, container.video.timeline)
            here = _slot_of_video(container.video, online)
            lives = any(_slot_of_video(other.video, online) == here and (
                voice_of(other.video.snapshot.group, other.video.timeline) == voice
                or any(voice_of(each.group, each.dub) == voice for each in other.tracks)) for other in step.after)
            assert lives, f"the own sound {voice} of {container.name} went with nothing in its place"
        # A group whose work failed changes none of its files.
        for group in step.plan.groups:
            if group.slots in step.applied:
                continue
            for name in set(group.remove) | {old for old, _ in group.hide} | {name for _, name in group.kept}:
                assert any(each.name == name for each in step.after), f"{name} changed though its group failed"
        # Every file holds one episode: its tracks are the same episode as its video (the service took them so).
        for container in step.after:
            truth = self.content.get(container.video.key)
            for track in container.tracks:
                assert self.content.get(("track", container.name, track.dub)) == truth, \
                    f"{container.name}: the track {track.group} is another episode than the video"
        # One visible file per episode, by the number the file stands under (what the media server sees).
        visible = Counter(each.video.episode for each in step.after if not each.off)  # the number its file was built for
        assert all(count == 1 for count in visible.values()), f"two visible files of an episode: {visible}"
        # When every group was applied the library shows exactly the goals; anything else is hidden.
        if len(step.applied) == len(step.plan.groups):
            shown = {each.name for each in step.after if not each.off}
            assert shown == {goal.name for goal in step.plan.goals if not goal.off}, shown
        if not step.failed:
            fixpoint = plan(Inputs(step.online, step.after, step.choices_after or step.choices, step.failed_rows))
            assert not fixpoint.ops, f"after a sync that did everything the plan still has work: {[op.id for op in fixpoint.ops]}"
            assert not any(group.remove or group.hide for group in fixpoint.groups), "after a sync files still move"


def _slot_of_video(video: Video, online: dict[RowId, OnlineRow]) -> Slot:
    row = online.get(video.timeline if video.cut_of is not None else video.id)
    return row.slot if row is not None and not row.compilation else video.episode


class Chaos:
    """Random changes and faults of a series, one per call: the sources (new rows, rows gone, wrong numbers, numbers that
    overlap, another season, a whole source renumbered, another video behind a row, a higher one), the user (episodes,
    groups and compilations switched off and on, the order of the reference, the order of the voice-overs, «Удалить»),
    the service (answers of the alignment, a cut that fails, downloads that fail for good or for once, a read that fails)
    and the disk (a file gone)."""

    GROUPS = ("A", "B", "C", "D")
    ACTIONS = ("put_ref", "put_ref", "put_dub", "put_dub", "put_comp", "drop", "renumber", "move_season", "mass_shift",
               "wrong_number", "higher", "swap_video", "delete_video", "delete_track", "off", "on", "off_group",
               "off_compilation", "reference_order", "shuffle_voices", "read_fail", "download_fail", "transient",
               "answer", "cut_answer", "lose_file", "reset")

    def __init__(self, world: World, rng, seasons=(1, 2), episodes=(1, 2, 3, 4), reference: str = "AniStar",
                 groups: tuple[str, ...] | None = None, actions: tuple[str, ...] | None = None) -> None:
        self.world, self.rng, self.seasons, self.episodes, self.reference = world, rng, seasons, episodes, reference
        self.groups = groups or self.GROUPS
        self.actions = actions or self.ACTIONS
        self.serial = 0

    def _rows(self) -> list[tuple[int, SiteRow]]:
        return [(source, each) for source, rows in sorted(self.world.site.items()) for each in rows]

    def _key(self, prefix: str) -> str:
        self.serial += 1
        return f"{prefix}{self.serial}"

    def step(self) -> str:
        world, rng = self.world, self.rng
        action = rng.choice(self.actions)
        season, episode = rng.choice(self.seasons), rng.choice(self.episodes)
        rows = self._rows()
        if action == "put_ref":
            source = rng.choice((1, 3))
            world.put(source, self._key("r"), episode, season=season, group=self.reference,
                      player="vk" if source == 1 else "cvh", height=rng.choice((480, 720, 1080)))
        elif action == "put_dub":
            world.put(2, self._key("d"), episode, season=season, group=rng.choice(self.groups),
                      player=rng.choice(("kodik", "vk")))
        elif action == "put_comp":
            start = rng.choice(self.episodes[:-1])
            world.put(4, self._key("c"), start, end=start + rng.choice((1, 2)), season=season,
                      group=rng.choice((self.reference, "A")), height=rng.choice((1080, 2160)))
        elif action == "wrong_number":
            # A row numbered as one episode that truly is another (the site or a rule mixed them up).
            other = rng.choice([number for number in self.episodes if number != episode])
            world.put(rng.choice((1, 2, 3)), self._key("w"), episode, season=season,
                      group=rng.choice((self.reference,) + self.groups), player=rng.choice(("vk", "cvh", "kodik")),
                      content=(truth_of(season, other),))
        elif action == "drop" and rows:
            source, each = rng.choice(rows)
            world.drop(source, each.row.id.row_key)
        elif action == "renumber" and rows:
            source, each = rng.choice(rows)
            if not each.row.compilation:
                world.renumber(source, each.row.id.row_key, episode)
        elif action == "move_season" and rows:
            source, each = rng.choice(rows)
            world.move(source, each.row.id.row_key, rng.choice(self.seasons), each.row.start)
        elif action == "mass_shift" and rows:
            source = rng.choice(sorted({source for source, _ in rows}))
            shift = rng.choice((-1, 1))
            for each in world.site.get(source, []):
                if each.row.start + shift >= 1:
                    each.row = replace(each.row, start=each.row.start + shift, end=each.row.end + shift)
        elif action in ("higher", "swap_video") and rows:
            source, each = rng.choice(rows)
            row = each.row
            if action == "higher":
                world.put(source, row.id.row_key, row.start, end=row.end, season=row.season, group=row.group,
                          player=row.player, height=row.height + 360, video=row.id.video, content=each.content)
            else:
                world.put(source, row.id.row_key, row.start, end=row.end, season=row.season, group=row.group,
                          player=row.player, height=row.height, video=self._key("v"))
        elif action == "delete_video" and world.containers:
            world.delete("video", rng.choice(sorted(world.containers)))
        elif action == "delete_track" and world.containers:
            container = world.containers[rng.choice(sorted(world.containers))]
            if container.tracks:
                world.delete("track", container.name, rng.choice(container.tracks).dub)
        elif action == "off":
            world.choose(off_episodes=world.choices.off_episodes | {(season, episode)})
        elif action == "on":
            world.choose(off_episodes=world.choices.off_episodes - {(season, episode)})
        elif action == "off_group":
            world.choose(off_groups=world.choices.off_groups ^ {rng.choice(self.groups)})
        elif action == "off_compilation" and rows:
            compilations = [each.row.id for _, each in rows if each.row.compilation]
            if compilations:
                world.choose(off_compilations=world.choices.off_compilations ^ {rng.choice(compilations)})
        elif action == "reference_order":
            variants = sorted({(each.row.id.source_id, each.row.player) for _, each in rows}) or [(1, "vk")]
            order = tuple(rng.sample(variants, min(len(variants), rng.choice((1, 2)))))
            world.choose(reference={**world.choices.reference, (season, episode): order})
        elif action == "shuffle_voices":
            voices = list(self.groups)
            rng.shuffle(voices)
            world.choose(groups=tuple(voices))
        elif action == "read_fail":
            world.read_fails = {rng.choice((1, 2, 3, 4))}
        elif action == "download_fail" and rows:
            world.download_fails.add(rng.choice(rows)[1].row.id)
        elif action == "transient" and rows:
            world.transient = {rng.choice(rows)[1].row.id}
        elif action == "answer":
            world.answers[((season, episode), rng.choice(self.groups))] = rng.choice(("mismatch", "critical", "suspect", "lost"))
        elif action == "cut_answer":
            world.cut_answers[(season, episode)] = rng.choice(("critical", "lost"))
        elif action == "lose_file" and world.containers:
            world.lose_file(rng.choice(sorted(world.containers)))
        elif action == "reset":
            world.calm()
        return action

    def history(self, steps: int) -> None:
        """`steps` changes and faults, a sync and the checks after each; then the faults stop and the series must rest."""
        for number in range(steps):
            action = self.step()
            self.world.check(self.world.sync(f"{number}: {action}"))
            self.world.read_fails.clear()
            self.world.transient.clear()
        self.world.calm()
        self.world.settle()
