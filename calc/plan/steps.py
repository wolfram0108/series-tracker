"""The steps of the graph of a plan and the counting over them (stage 12, the table of an episode): what a step made, how a step
that did not come is told, the verdict on a result of the alignment service, the asset and the commands once a group is applied,
the problems a sync leaves. Pure: the executor (modules/planner/executor.py) runs the steps and asks these functions what they mean."""
from __future__ import annotations

from dataclasses import dataclass, replace

from core.contracts import DubResult

from .planner import Goal, Group, Op, Plan, video_op
from .tables import Container, Track, Video

# The steps that obtain a row; the take of a row of subtitles from its file is one of them (DESIGN.md 7.1.1).
DOWNLOADS = ("download_video", "download_dub", "download_picture", "take_subtitles")


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
    # Where the sound of the step's row is, when it is not the first sound of `file`: another file beside the video (a
    # track of a torrent release lying in its own file) and the index of its stream there (stage 15, И-3, И-5).
    sound: str | None = None
    stream: int = 0
    # The font files the subtitles of the step are drawn with, beside them in the release (DESIGN.md 7.1).
    fonts: tuple[str, ...] = ()

    @property
    def sound_file(self) -> object:
        return self.sound or self.file


def judged(result: DubResult | None, *, cut: bool) -> str | None:
    """The file a job of the alignment service made, by the service's own verdict: an aligned track is taken by `ok` (what
    it found critical is a mark, the track is in step); a cut needs `ok` and nothing critical. A pair the service looked at
    and turned down (`refused`) is a failure (None, recorded). A pair the service fell on (its result is there, with no
    file and no decision of its gates) said nothing of the voice-over: it is `Crashed`, passed over for this sync with the
    next source taken. No result at all (the job did not come to the pair, the answer never came) says nothing of the pair
    either, and the service may only be late: `Passing`, the episode waits for it unchanged."""
    if result is None:
        raise Passing
    if result.ok and result.out_path:
        return None if cut and result.critical else result.out_path
    if result.refused:
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
        built[goal.name] = Container(goal.name, video, tuple(tracks), goal.off, goal.own_at, goal.titles, goal.languages,
                                     tuple(way.subtitle for way in goal.subtitles))
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


def fate_of(passing: "Passing") -> str:
    """What the sync does with a step the service did not bring to an end, for the log."""
    if isinstance(passing, Crashed):
        return "источник пропущен в этой синхронизации (берётся следующий), неудача не записана"
    return "повторится при следующей синхронизации"
