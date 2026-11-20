"""A world for the planner of a series (core/plan): sources, a library and an alignment service with no real files.

The world knows what the planner never sees: the true episode behind every video (`content`). Downloading puts a file
into the asset, cutting takes a single episode out of a compilation, and aligning succeeds only when the voice-over and
the video are the same episode — so a rule that numbers a row wrongly fails where the real service fails. Failures of
downloads and alignments are set by a scenario.

`sync()` is one synchronization: read the sources (a source whose read fails keeps its last table), plan, run the graph,
apply every group whose operations all succeeded. After each sync `check()` holds the invariants of the model."""
from __future__ import annotations

import asyncio
import random
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable

from core.contracts import PlannerView, SyncScope
from core.plan.executor import DOWNLOADS, Crashed, Made, Passing, SeriesSync, video_of
from core.plan.planner import OFF, Goal, Plan, cut_video, own_track, plan, restricted, switched_off, voice_of
from core.plan.tables import Choices, Container, Inputs, OnlineRow, RowId, Slot, Track, Video, episode_of
from core.variant_queue import Same
from modules.planner.view import shown_of, view_of


def truth_of(season: int, episode: int, part: int | None = None) -> int:
    """What a video truly is: one number for a season, an episode and its part."""
    return season * 1000 + episode + (part or 0) * 100000


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
    # What the outcome of the sync still lacks (the window and the card of the series show it as an error).
    problems: tuple[tuple, ...] = ()
    # The asset the last plan of the sync was made on: the groups applied before it are in it as they were applied.
    planned_on: tuple[Container, ...] = ()
    # The places the sync covered (a season, an episode of it), none for the whole series.
    holds: Callable[[Slot], bool] | None = None


@dataclass
class World(SeriesSync):
    choices: Choices = field(default_factory=lambda: Choices(players=("vk", "cvh", "kodik"), sources=(1, 2, 3)))
    site: dict[int, list[SiteRow]] = field(default_factory=dict)  # source id -> its table now
    read_fails: set[int] = field(default_factory=set)  # sources whose next reads fail
    download_fails: set[RowId] = field(default_factory=set)
    failed: set[tuple] = field(default_factory=set)  # attempts that failed: the planner passes over them
    align_fails: set[tuple[Slot, str]] = field(default_factory=set)
    # The answers of the service by (episode, voice-over): "mismatch" fails and is recorded; "suspect" and "critical" are
    # taken (marks of the track); "crash" (the service fell on the pair and judged nothing: the source is passed over for
    # this sync, the next one is taken) and "lost" (the answer never came: the episode waits) fail for this sync only.
    # A cut by episode: "critical" (no cut: recorded), "crash" or "lost".
    answers: dict[tuple[Slot, str], str] = field(default_factory=dict)
    cut_answers: dict[Slot, str] = field(default_factory=dict)
    crashes: set[RowId] = field(default_factory=set)  # the voice-over rows the service falls on, whatever they are laid onto
    # A safeguard, not a way to wait: a sync that does not end in this many seconds is a hang, and fails as one.
    guard: float = 60.0
    # The downloads that take time: a row held waits at a gate that opens once every held row is waiting at it, so the plans
    # made again by then are made while the other downloads still run. A fall of the service listed in `crash_after` comes
    # only after the held row it names began to wait (the falls of several episodes come one after another, as jobs end).
    slow: set[RowId] = field(default_factory=set)
    crash_after: dict[Slot, RowId] = field(default_factory=dict)
    _gate: object = None
    _waiting: int = 0
    _reached: dict = field(default_factory=dict)
    transient: set[RowId] = field(default_factory=set)  # downloads that fail for this sync only (a network error)
    containers: dict[str, Container] = field(default_factory=dict)
    online: dict[int, tuple[OnlineRow, ...]] = field(default_factory=dict)  # source id -> last complete read
    content: dict[tuple, tuple[int, ...]] = field(default_factory=dict)  # video key or ("dub", row) -> true episodes
    history: list[Step] = field(default_factory=list)
    # The state of the service's own sync (core/plan/executor.SeriesSync) the world runs.
    namer: object = None
    commands: frozenset = frozenset()
    passing: set = field(default_factory=set)
    skipped: set = field(default_factory=set)
    # The downloads made in the sync under way, by the row: a row is downloaded once in a sync (Э-7).
    fetched: Counter = field(default_factory=Counter)

    # --- the sources -------------------------------------------------------------------------------------------

    def put(self, source_id: int, key: str, episode: int, *, group: str, player: str = "vk", height: int = 720,
            season: int = 1, end: int | None = None, video: str = "", content: Iterable[int] | None = None,
            name: str = "", part: int | None = None) -> OnlineRow:
        """A row of a source: what the rules give it (season, episode or range, part, group) and what it truly is."""
        row = OnlineRow(RowId(source_id, key, video or key), season, episode, end or episode, player, group, height,
                        source=name or f"источник {source_id}", link=f"https://site/{source_id}/{key}", part=part)
        truth = tuple(content) if content is not None else tuple(truth_of(season, number, part)
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
                each.row = each.row.replaced(start=episode, end=episode)

    def move(self, source_id: int, key: str, season: int, episode: int) -> None:
        """A rule gives the row another season and number."""
        for each in self.site.get(source_id, []):
            if each.row.id.row_key == key:
                span = each.row.end - each.row.start
                each.row = each.row.replaced(season=season, start=episode, end=episode + span)

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
        self.crashes.clear()
        self.slow.clear()
        self.crash_after.clear()
        self.read_fails.clear()
        self.reset_failures()

    def settle(self, syncs: int = 4) -> Step:
        """After `calm`, the series comes to rest: within a few syncs the plan has nothing left to do."""
        for number in range(syncs):
            step = self.sync(f"успокоение {number + 1}")  # the whole series: the scope of an earlier sync is not kept
            self.check(step)
            if not step.plan.ops and not any(group.remove or group.hide for group in step.plan.groups):
                return step
        raise AssertionError(f"the series does not come to rest: {[op.id for op in step.plan.ops][:8]}")

    def choose(self, **changes) -> None:
        self.choices = replace(self.choices, **changes)

    def delete(self, *command) -> None:
        self.choose(delete=self.choices.delete | {tuple(command)})

    # --- one synchronization -------------------------------------------------------------------------------------

    def sync(self, title: str = "", scope: tuple[int, int | None] | None = None) -> Step:
        """One sync of the series, or of a season (and an episode of it) when `scope` names them."""
        self.holds = None if scope is None else SyncScope(season=scope[0], episode=scope[1]).holds
        for source_id, table in self.site.items():
            if source_id not in self.read_fails:
                self.online[source_id] = tuple(each.row for each in table)
                for each in table:
                    self.content.setdefault(("site", each.row.id), each.content)
        # The layout lists the rows source by source, as they come: the place of a row is its place in that list.
        rows = tuple(row.replaced(position=place) for place, row in
                     enumerate(row for source in sorted(self.online) for row in self.online[source]))
        self.commands = self.choices.delete
        self.fetched = Counter()
        self._gate, self._waiting, self._reached = None, 0, {}
        synced = asyncio.run(asyncio.wait_for(SeriesSync.sync(self, rows, self.choices), self.guard))
        twice = [row for row, count in self.fetched.items() if count > 1]
        assert not twice, f"rows downloaded twice in one sync: {twice}"
        inputs = synced.inputs
        self.choose(delete=self.commands)
        step = Step(title, inputs.online, synced.before, synced.plan, tuple(sorted(synced.failed)), synced.applied,
                    tuple(sorted(self.containers.values(), key=lambda each: each.name)), inputs.choices, self.choices,
                    inputs.failed, synced.replanned, synced.problems, inputs.containers, self.holds)
        self.history.append(step)
        return step

    def reset_failures(self) -> None:
        """The user's word: the failed attempts are tried again."""
        self.failed.clear()

    def _truth(self, key: tuple) -> tuple[int, ...]:
        return self.content[key]

    async def _hold(self, row: RowId) -> None:
        self._reached.setdefault(row, asyncio.Event()).set()
        self._waiting += 1
        gate = self._gate = self._gate or asyncio.Event()
        if self._waiting >= len(self.slow):
            gate.set()
        await gate.wait()

    async def _after(self, slot: Slot) -> None:
        row = self.crash_after.get(slot)
        if row is not None:
            await self._reached.setdefault(row, asyncio.Event()).wait()

    async def do(self, op, results):
        if op.kind in DOWNLOADS and op.subject.id in self.slow:
            await self._hold(op.subject.id)
        if op.kind in DOWNLOADS and op.subject.id in self.transient:
            raise Passing
        if op.kind in DOWNLOADS and op.subject.id not in self.download_fails:
            self.fetched[(op.kind, op.subject.id)] += 1
        if op.kind == "download_video":
            row = op.subject
            if row.id in self.download_fails:
                return None
            video = Video(row.id, row.snapshot(), row.slot, row.timeline)
            self.content[video.key] = self._truth(("site", row.id))
            return Made(video)
        if op.kind == "download_picture":
            return None if op.subject.id in self.download_fails else Made(None, self._truth(("site", op.subject.id)))
        if op.kind == "cut":
            way = op.subject
            ref2 = way.ref2.container.video if way.ref2.kind == "asset" else results[f"row:{way.ref2.row.id}"].value
            inside = (self.content[way.picture.video.key] if way.picture is not None
                      else results[f"picture:{way.row.id}"].file)
            truth = self.content[ref2.key]
            answer = self.cut_answers.get(op.slot)
            if answer == "lost":
                raise Passing
            if answer == "crash":
                raise Crashed
            if answer == "critical" or not set(truth) <= set(inside):
                return None  # the single episode is not in the compilation: nothing matches
            video = cut_video(way, op.slot)
            self.content[video.key] = truth
            return Made(video)
        if op.kind == "download_dub":
            row = op.subject
            if row.id in self.download_fails:
                return None
            self.content[("dub", row.id)] = self._truth(("site", row.id))
            return Made(row)
        if op.kind == "align":
            goal, way = op.subject
            video = video_of(goal, results)
            dub = (self.content[("dub", way.dub)] if way.kind == "download"
                   else self.content[way.container.video.key] if way.own
                   else self.content[("track", way.container.name, way.dub)])
            answer = self.answers.get((goal.slot, way.voice))
            if answer == "lost":
                raise Passing
            if answer == "crash" or way.dub in self.crashes:
                await self._after(goal.slot)
                raise Crashed
            if (goal.slot, way.voice) in self.align_fails or answer == "mismatch" or \
                    not set(self.content[video.key]) <= set(dub):
                return None  # a compilation as a voice-over holds the episode among others (Д-19)
            return Made(Track(way.dub, way.snapshot, goal.slot, video.timeline), self.content[video.key])
        if op.kind == "build":
            return Made(op.subject)
        raise AssertionError(op.kind)

    def aligned_truth(self, aligned: Made) -> tuple[int, ...]:
        """What an aligned track truly is: the world keeps it in the result itself."""
        return aligned.file

    async def stage(self, made: Plan, group, results) -> None:
        """What every file of the group truly holds once it is applied (the world's own knowledge)."""
        moved: dict[tuple, tuple[int, ...]] = {}
        for slot in group.built:
            goal = made.goal(slot)
            for way in goal.tracks:
                if way.kind == "reuse" and way.own:
                    truth = self.content[way.container.video.key]
                elif way.kind == "reuse":
                    truth = self.content[("track", way.container.name, way.dub)]
                elif way.kind == "timeline":  # the sound of the timeline's own download: the episode it truly is
                    truth = self.content[("row", way.dub)]
                else:
                    truth = self.aligned_truth(results[f"align:{slot}:{way.voice}"])
                moved[("track", goal.name, way.dub)] = truth
        for slot, name in group.kept:
            goal = made.goal(slot)
            if goal.name != name:
                for track in self.containers[name].tracks:
                    moved[("track", goal.name, track.dub)] = self.content[("track", name, track.dub)]
        for name, new in group.hide:
            for track in self.containers[name].tracks:
                moved[("track", new, track.dub)] = self.content[("track", name, track.dub)]
        self.content.update(moved)

    # --- the invariants of the model -----------------------------------------------------------------------------

    def check(self, step: Step | None = None) -> None:
        step = step or self.history[-1]
        again = plan(Inputs(step.online, step.planned_on, step.choices, step.failed_rows))
        if step.holds is not None:
            again = restricted(again, step.holds)
        assert repr(again) == repr(step.plan), "the plan is not a function of its inputs"
        # Whatever the plan is, the window is told it in the terms of its contract.
        PlannerView.model_validate(view_of(0, self, shown_of(step.plan), running=False))
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
        # Nothing goes but what the goal holds anew or what the user said: a voice-over taken off the episode, or the
        # track «Удалить» names (Д-1). A deleted video takes only itself: the sounds of its file live on, made anew.
        standing = {_slot_of_video(each.video, online) for each in step.after}
        deleted = step.choices.delete
        for container in step.before:
            commanded = ("video", container.name) in deleted
            if not commanded and not any(each.video.key == container.video.key for each in step.after):
                assert _slot_of_video(container.video, online) in standing, \
                    f"the video of {container.name} went with nothing in its place"
            for track in container.tracks:
                voice, here = voice_of(track.group, track.dub), slot_of_track(track)
                if switched_off(step.choices, track, here) or ("track", container.name, track.dub) in deleted:
                    continue
                # It lives on: the same track anywhere, or its voice-over in the container of its episode — as a track
                # or as the own sound of the video.
                lives = any(each.dub == track.dub or (voice_of(each.group, each.dub) == voice
                                                      and _slot_of_video(other.video, online) == here)
                            for other in step.after for each in other.tracks) or any(
                    voice_of(other.video.snapshot.group, other.video.timeline) == voice
                    and _slot_of_video(other.video, online) == here for other in step.after)
                assert lives, f"the track {voice} of {container.name} went with nothing in its place"
        # The own sound of a video that went lives on — a deleted one's too: the same video elsewhere, or its voice-over in
        # the container of its episode (as the own sound or as a track).
        for container in step.before:
            if any(each.video.key == container.video.key for each in step.after):
                continue
            voice = voice_of(container.video.snapshot.group, container.video.timeline)
            here = _slot_of_video(container.video, online)
            if switched_off(step.choices, own_track(container), here):
                continue
            lives = any(_slot_of_video(other.video, online) == here and (
                voice_of(other.video.snapshot.group, other.video.timeline) == voice
                or any(voice_of(each.group, each.dub) == voice for each in other.tracks)) for other in step.after)
            assert lives, f"the own sound {voice} of {container.name} went with nothing in its place"
        # The markup reference (stage 14): an episode with its row lies on its timeline, unless the episode takes no new
        # work (switched off, or a deleted video that waits); a cut is only of a picture higher than its timeline; the
        # sound of the timeline is never aligned to it.
        for goal in step.plan.goals:
            video = goal.video
            if goal.markup is not None and not goal.off and not (
                    video.container is not None and video.container.name in deleted_names(step.choices)):
                assert video.timeline == goal.markup.id, f"{goal.name}: not on the timeline of the markup reference"
            if video.kind == "cut":
                ref2 = online.get(video.timeline)
                # A timeline video that cannot be had (its download failed for good) is no height the cut must beat.
                assert (ref2 is None or video.row.height > ref2.height or not ref2.playable
                        or ("download", ref2.id) in step.failed_rows), f"{goal.name}: a cut no higher than its timeline"
        # Variants the user declared equal (Р-99): the video a file holds is not given up for a video of its group that is no
        # higher, unless the user took the held one out (denied, deleted) or the episode takes no new work.
        for goal in step.plan.goals:
            same = step.choices.same.get(episode_of(goal.slot))
            if same is None or goal.off or goal.markup is not None or goal.video.kind != "download":
                continue
            new = goal.video.row.variant
            for held in step.before:
                shot = held.video.snapshot
                variant = (shot.variant_label, held.video.id.source_id, shot.player)
                assert not (_slot_of_video(held.video, online) == goal.slot and held.video.cut_of is None and not held.off
                            and ("video", held.name) not in deleted and variant != new and new in same.members(variant)
                            and variant not in step.choices.reference_deny.get(episode_of(goal.slot), frozenset())
                            and goal.video.row.height <= shot.height), \
                    f"{goal.name}: the video {variant} of the file is given up for {new} of its own group"
        # A deleted track is never a source of itself: «Удалить» is its removal, not an alignment anew.
        for goal in step.plan.goals:
            for way in goal.tracks:
                assert not (way.container is not None and not way.own
                            and ("track", way.container.name, way.dub) in deleted and way.kind != "reuse"), \
                    f"{goal.name}: the deleted track {way.voice} is made anew out of its own file"
        for op in step.plan.ops:
            if op.kind == "align":
                goal, way = op.subject
                assert not (way.kind == "download" and way.dub == goal.video.timeline), \
                    f"{goal.name}: the sound of the timeline is aligned to itself"
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
        # A sync of a season or of an episode leaves the files of the other places as they were: no group of its plan names
        # them, and nothing of them is made, renamed, removed or hidden.
        if step.holds is not None:
            named = {name for group in step.plan.groups
                     for name in (*group.remove, *(old for old, _ in group.hide), *(name for _, name in group.kept))}
            for container in step.before:
                if container.name not in named and not step.holds(_slot_of_video(container.video, online)):
                    assert container in step.after, f"{container.name}: a file of another place changed in a sync of a part"
        # When every group was applied the library shows exactly the goals; anything else is hidden. (A sync of a part
        # applies the groups it covers: the rest of the library is for the sync of the series.)
        if step.holds is None and all(group.slots in step.applied for group in step.plan.groups):
            shown = {each.name for each in step.after if not each.off}
            assert shown == {goal.name for goal in step.plan.goals if not goal.off}, shown
        if not step.failed:
            fixpoint = plan(Inputs(step.online, step.after, step.choices_after or step.choices, step.failed_rows))
            covered = None  # the places of the groups a sync of a part carried out: what it leaves for the others is no work of its own
            if step.holds is not None:
                covered = {slot for group in step.plan.groups for slot in group.slots}
                fixpoint = restricted(fixpoint, lambda slot: slot in covered)
            assert not fixpoint.ops, f"after a sync that did everything the plan still has work: {[op.id for op in fixpoint.ops]}"
            assert not any(group.remove or group.hide for group in fixpoint.groups), "after a sync files still move"
            # The sounds of every visible file, the own sound of its video among them, follow the order of its season.
            order = (step.choices_after or step.choices).groups
            markups = {goal.name: goal.markup for goal in fixpoint.goals}
            for container in step.after:
                if container.off or (covered is not None and _slot_of_video(container.video, online) not in covered):
                    continue
                places = {voice: place for place, voice in enumerate(order.get(_slot_of_video(container.video, online)[0], ()))}
                sounds = [voice_of(track.group, track.dub) for track in container.tracks]
                sounds.insert(container.own_at, voice_of(container.video.snapshot.group, container.video.timeline))
                # The sound of the markup reference is the last one, out of the order of the season (Э-5).
                markup = markups.get(container.name)
                if markup is not None and voice_of(markup.group, markup.id) in sounds:
                    assert sounds[-1] == voice_of(markup.group, markup.id), \
                        f"{container.name}: the sound of the markup reference is not the last one: {sounds}"
                    sounds = sounds[:-1]
                ranks = [places[voice] for voice in sounds if voice in places]
                assert ranks == sorted(ranks), f"{container.name}: the sounds {sounds} are out of the order of the season"
                # Every sound of a visible file bears the name the user gave its group, and only that one.
                labels = {track.group or "—" for track in container.tracks} | {container.video.snapshot.group or "—"}
                # A deleted video that stays for want of another one takes no new work: what the file holds stays as it is.
                if container.name in deleted_names(step.choices_after or step.choices):
                    continue
                titles = (step.choices_after or step.choices).titles
                assert dict(container.titles) == {label: title for label, title in titles.items() if label in labels}, \
                    f"{container.name}: the titles {container.titles} are not the user's {titles}"
                # Every sound bears the language the user gave its group, and only that one.
                languages = (step.choices_after or step.choices).languages
                assert dict(container.languages) == {label: code for label, code in languages.items() if label in labels}, \
                    f"{container.name}: the languages {container.languages} are not the user's {languages}"


def deleted_names(choices: Choices) -> set[str]:
    return {each[1] for each in choices.delete if each[0] == "video"}


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
               "off_compilation", "reference_order", "shuffle_voices", "title", "language", "read_fail", "download_fail", "transient",
               "answer", "cut_answer", "lose_file", "reset", "reference_deny", "voice_order", "voice_deny", "voice_off",
               "member", "levers_reset", "put_parts", "markup", "markup_deny", "put_high")

    def __init__(self, world: World, rng, seasons=(1, 2), episodes=(1, 2, 3, 4), reference: str = "AniStar",
                 groups: tuple[str, ...] | None = None, actions: tuple[str, ...] | None = None) -> None:
        self.world, self.rng, self.seasons, self.episodes, self.reference = world, rng, seasons, episodes, reference
        self.groups = groups or self.GROUPS
        self.actions = actions or self.ACTIONS
        self.serial = 0
        # What each sync covers is drawn apart from the changes: the histories of a seed are the same whatever the scopes.
        self.scoper = random.Random(str(rng.getstate()))

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
                    each.row = each.row.replaced(start=each.row.start + shift, end=each.row.end + shift)
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
            variants = sorted({each.row.variant for _, each in rows}) or [(self.reference, 1, "vk")]
            order = tuple(rng.sample(variants, min(len(variants), rng.choice((1, 2)))))
            world.choose(reference={**world.choices.reference, (season, episode): order})
        elif action == "reference_deny" and rows:
            world.choose(reference_deny={**world.choices.reference_deny,
                                         (season, episode): frozenset({rng.choice(rows)[1].row.variant})})
        elif action == "voice_order" and rows:
            row = rng.choice(rows)[1].row
            if row.group:
                world.choose(voice_order={**world.choices.voice_order, ((season, episode), row.group): ((row.id.source_id, row.player),)})
        elif action == "voice_deny" and rows:
            world.choose(voice_deny={**world.choices.voice_deny, (season, episode): frozenset({rng.choice(rows)[1].row.variant})})
        elif action == "voice_off":
            world.choose(voice_off={**world.choices.voice_off, (season, episode): frozenset({rng.choice(self.groups)})})
        elif action == "member":
            compilations = [each.row.id for _, each in rows if each.row.compilation]
            word = rng.choice(compilations + [None]) if compilations else None
            world.choose(member={**world.choices.member, (season, episode): word})
        elif action == "put_parts":
            # An episode in two parts: a reference and a voice-over of each part.
            for part in (1, 2):
                world.put(rng.choice((1, 3)), self._key("p"), episode, season=season, group=self.reference,
                          player=rng.choice(("vk", "cvh")), part=part)
                world.put(2, self._key("q"), episode, season=season, group=rng.choice(self.groups), player="kodik", part=part)
        elif action in ("markup", "markup_deny"):
            # The owner gives the series or an episode a queue of the markup reference of its own, leaves a variant out of
            # it, or takes the queue back: the markup follows the reference again (stage 14).
            variants = sorted({each.row.variant for _, each in rows if not each.row.compilation}) or [(self.reference, 3, "cvh")]
            variant = rng.choice(variants)
            if action == "markup_deny":
                world.choose(markup_deny={**world.choices.markup_deny, (season, episode): frozenset({variant})})
            else:
                kind = rng.choice(("back", "series", "episode"))
                world.choose(markup={} if kind == "back" else {**world.choices.markup, (season, episode): (variant,)}
                             if kind == "episode" else {(each, number): (variant,) for each in self.seasons
                                                        for number in self.episodes})
        elif action in ("same", "same_off"):
            # The owner declares some variants of the series equal, for every episode or for one, or takes the groups back.
            variants = sorted({each.row.variant for _, each in rows if not each.row.compilation})
            if action == "same_off" or len(variants) < 2:
                world.choose(same={}, markup_same={})
            else:
                members = frozenset(rng.sample(variants, min(len(variants), rng.choice((2, 3)))))
                group = Same((members,))
                if rng.choice(("series", "episode")) == "series":
                    world.choose(same={(each, number): group for each in self.seasons for number in self.episodes})
                else:
                    world.choose(same={**world.choices.same, (season, episode): group})
        elif action == "put_high":
            # A single video higher than the references: a picture to cut onto the timeline of the markup reference.
            world.put(rng.choice((1, 3)), self._key("h"), episode, season=season, group=rng.choice((self.reference, "A")),
                      player="vk", height=2160)
        elif action == "levers_reset":
            world.choose(reference={}, reference_deny={}, voice_order={}, voice_deny={}, voice_off={}, member={}, markup={},
                         markup_deny={}, same={}, markup_same={})
        elif action == "shuffle_voices":
            voices = [self.reference, *self.groups]
            rng.shuffle(voices)
            world.choose(groups={**world.choices.groups, season: tuple(voices)})
        elif action == "title":
            group = rng.choice([self.reference, *self.groups])
            titles = dict(world.choices.titles)
            if titles.pop(group, None) is None:
                titles[group] = f"ПМ_{group}"
            world.choose(titles=titles)
        elif action == "language":
            group = rng.choice([self.reference, *self.groups])
            languages = dict(world.choices.languages)
            languages[group] = rng.choice(("rus", "chi", "jpn"))
            world.choose(languages=languages)
        elif action == "read_fail":
            world.read_fails = {rng.choice((1, 2, 3, 4))}
        elif action == "download_fail" and rows:
            world.download_fails.add(rng.choice(rows)[1].row.id)
        elif action == "transient" and rows:
            world.transient = {rng.choice(rows)[1].row.id}
        elif action == "answer":
            world.answers[((season, episode), rng.choice(self.groups))] = rng.choice(("mismatch", "critical", "suspect", "crash", "lost"))
        elif action == "cut_answer":
            world.cut_answers[(season, episode)] = rng.choice(("critical", "crash", "lost"))
        elif action == "lose_file" and world.containers:
            world.lose_file(rng.choice(sorted(world.containers)))
        elif action == "reset":
            world.calm()
        return action

    def history(self, steps: int) -> None:
        """`steps` changes and faults, a sync and the checks after each; then the faults stop and the series must rest."""
        for number in range(steps):
            action = self.step()
            # A sync of the series, of a season, or of an episode: what the others hold is as it was, whichever it is.
            scope = self.scoper.choice((None, None, (self.scoper.choice(self.seasons), None),
                                        (self.scoper.choice(self.seasons), self.scoper.choice(self.episodes))))
            self.world.check(self.world.sync(f"{number}: {action}", scope))
            self.world.read_fails.clear()
            self.world.transient.clear()
        self.world.calm()
        self.world.settle()
