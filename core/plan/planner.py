"""The planner of a series (stage 12, the table of an episode): a pure function of the tables and the priorities.

From the inputs (`tables.Inputs`) it derives the goal — what the container of every episode must be — and the graph of
operations that leads from the asset to it. There is no rule for a situation: renumbering, a better reference, a source
that forgot a row, a command «Удалить» are only other inputs.

Every part of the goal is reached by the first way that is open, the same list for every case:
  the video of an episode: 1) the asset holds it; 2) its row is online — download; 3) cut: the picture of a higher row
  (a compilation, or with a markup reference any single row) laid by the service onto the timeline of a single one;
  with a markup reference (stage 14) every way onto the timeline of its row comes before every other way;
  the track of a voice-over to the video: 1) the asset holds it aligned to that video's timeline; 2) its row is online —
  download and align; 3) the asset holds it at another video — align it from that container (the container is the dub:
  its picture and the track are in sync).

The work comes in two phases. The operations run first, beside the old files. Only when every operation of a group of
episodes that share files has succeeded is the group applied: new containers take their names, what the goal holds anew
goes, what nothing carries stays hidden (`.off`). A file goes only when everything in it lives on in the goal or the user
said so: «Удалить», or a voice-over taken off the episode (a track cannot be hidden: switched off, it is removed). A video
the user deleted goes only for another video of its episode: with none, nothing changes.

«Удалить» names what goes and nothing more. A deleted track leaves its container at the next sync: its online row, still
switched on, is downloaded again (Н-3); with no online row it is forgotten (Д-1) and is never aligned out of its own file.
A deleted video is no longer the picture of its episode; its own sound and the other tracks of its file stay sources to
align from onto the new video, and the file goes only when nothing in it is needed. A file that takes no new work (a
switched-off episode, a deleted video with no other one) stays as it is, and the command waits."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Iterable

from core import native

from .tables import Choices, Container, Inputs, OnlineRow, RowId, Slot, Snapshot, Subtitle, Track, Video, episode_of



OFF = ".off"
HIDDEN = ".hidden-"  # a file nothing carries any more: it stays, out of the media server's sight


@dataclass(frozen=True)
class VideoWay:
    """How the video of a goal is obtained: `asset` (held by `container`), `download` (`row`), `cut` (`row` gives the
    picture — a compilation, or a single row higher than the markup reference — and `ref2` is the way to the single video
    whose timeline it is laid on; `picture` is the file of the library that holds the picture's row, taken instead of a
    download of it)."""

    kind: str
    key: tuple
    timeline: RowId
    snapshot: Snapshot
    row: OnlineRow | None = None
    container: Container | None = None
    ref2: "VideoWay | None" = None
    picture: Container | None = None


def cut_video(way: VideoWay, slot: Slot) -> Video:
    """The video a cut makes: the picture of `way.row` laid on the timeline of `way.ref2`. The file keeps what that timeline
    was (`Video.timeline_snapshot`): the one place where a made cut is described, whoever makes it."""
    return Video(way.row.id, way.snapshot, slot, way.timeline, cut_of=way.row.id,
                 timeline_snapshot=way.ref2.snapshot if way.ref2 is not None else None)


@dataclass(frozen=True)
class TrackWay:
    """How the track of a voice-over is obtained: `reuse` (the track of `container` is aligned to the goal's timeline),
    `download` (`row`, then align), `realign` (the track of `container` aligned anew to the goal's video), `timeline` (the
    sound of the row that is the timeline of a cut video: in step with it by construction, taken from the download of that
    row with no alignment). `own`: the track is the own sound of the video of `container` — the voice-over of a video it
    no longer is."""

    voice: str
    dub: RowId
    snapshot: Snapshot
    kind: str
    row: OnlineRow | None = None
    container: Container | None = None
    own: bool = False


@dataclass(frozen=True)
class SubtitleWay:
    """How a subtitles track of the goal is obtained (DESIGN.md 7.1.1): `take` — from the file of its row, which is on the
    video's timeline; `reuse` — from the file `container`, which holds it on that timeline."""

    subtitle: Subtitle
    kind: str
    row: OnlineRow | None = None
    container: Container | None = None


@dataclass(frozen=True)
class Markup:
    """The markup reference of an episode (stage 14): the single video whose timeline the episode's video lies on. `row`
    is its row as the layout has it now; none for a timeline only a file of the library is on (the source forgot it)."""

    id: RowId
    group: str | None
    height: int
    row: OnlineRow | None = None
    # (voice-over, source id, player): the variant of the queue the timeline is of, as the window lists it.
    variant: tuple[str, int, str] | None = None


@dataclass(frozen=True)
class Goal:
    slot: Slot
    video: VideoWay
    tracks: tuple[TrackWay, ...]
    name: str
    off: bool
    # The voice-over groups the user switched off for the episode: no track of theirs is taken, by the user's word.
    voices_off: frozenset[str] = frozenset()
    # The place of the video's own sound among the tracks, by the order of the season the user set: the voice-over of the
    # reference is first only when it stands first there (ЭТАП_11). A voice-over the order does not name keeps it first.
    own_at: int = 0
    # The user's names of the sounds of the file (voice-over group, title), as the container is to bear them.
    titles: tuple[tuple[str, str], ...] = ()
    # The markup reference that gives the episode its timeline (stage 14); its sound is the last of the file.
    markup: Markup | None = None
    # The languages of the sounds of the file (voice-over group, ISO 639-2), as the container is to bear them.
    languages: tuple[tuple[str, str], ...] = ()
    # The subtitles of the file in its order (DESIGN.md 7.1.1).
    subtitles: tuple[SubtitleWay, ...] = ()
    # The variants of subtitles the episode takes that are on no timeline of its video: they wait for the alignment of
    # subtitles to another reference (stage 15, step 10), the window says so.
    unaligned: tuple[tuple[str, int, str], ...] = ()


@dataclass(frozen=True)
class Op:
    """A step of the graph. `subject` is what the step works on (a row, a way, a goal), `needs` the steps before it.
    Steps of one `batch` are done as one piece of work once each of them is ready or left behind (the alignment of an
    episode: one job of the service for its reference and every voice-over of it that came)."""

    id: str
    kind: str
    slot: Slot | None
    needs: tuple[str, ...]
    subject: object
    batch: str = ""


@dataclass(frozen=True)
class Group:
    """Episodes and files that share work: applied together once every operation of theirs has succeeded. `kept` are
    goals an existing file already is (renamed if its name differs), `built` the goals built anew, `remove` the files
    that go, `hide` the files nothing carries (they stay, hidden: old name, new name)."""

    slots: tuple[Slot, ...]
    ops: tuple[str, ...]
    kept: tuple[tuple[Slot, str], ...]
    built: tuple[Slot, ...]
    remove: tuple[str, ...]
    hide: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Plan:
    goals: tuple[Goal, ...]
    ops: tuple[Op, ...]
    groups: tuple[Group, ...]
    # The goals as the core counted them (core.native.Planned): what the view of the window is counted from.
    counted: object = field(default=None, compare=False, repr=False)

    def goal(self, slot: Slot) -> Goal | None:
        return next((each for each in self.goals if each.slot == slot), None)


def restricted(made: Plan, holds: Callable[[Slot], bool]) -> Plan:
    """The plan of a sync that covers a part of the series: the goals stay all (the window shows the series as it is, the
    plan of an episode is read from them), the steps and the groups are those of the groups that hold a place `holds` says
    is of the scope. A group is the episodes and files that share work, so a step of one group never needs a step of another:
    nothing of the others is done, applied, removed or hidden."""
    groups = tuple(group for group in made.groups if any(holds(slot) for slot in group.slots))
    wanted = {op for group in groups for op in group.ops}
    return Plan(made.goals, tuple(op for op in made.ops if op.id in wanted), groups, made.counted)


def voice_of(group: str | None, dub: RowId) -> str:
    """The key of a voice-over in a container: its group; an unknown voice-over is a voice of its own row."""
    return group if group is not None else f"?{dub.source_id}:{dub.row_key}:{dub.video}"


def name_of(item: tuple) -> str:
    """The plain name of a goal by what the rule of names is asked of it (`count(...).items()`): the episode, the group and
    the height."""
    slot, height, group, _link, _cut, off = item
    part = f" pt{slot[2]}" if len(slot) > 2 else ""
    return f"s{slot[0]:02d}e{slot[1]:02d}{part} [{group or '—'}] {height}p.mkv" + (OFF if off else "")


def own_track(container: Container) -> Track:
    """The own sound of the video of a container as a track: the voice-over of the row the video came from (for a cut,
    the compilation's), on the video's timeline. When the video gives way to another one, this voice-over lives on."""
    video = container.video
    return Track(video.cut_of or video.id, video.snapshot, video.episode, video.timeline)


def variant_of(track: Track) -> tuple[str, int, str]:
    """(voice-over, source, player) of a track: what the user's levers name, as `OnlineRow.variant`."""
    return track.snapshot.variant_label, track.dub.source_id, track.snapshot.player


def switched_off(choices: Choices, track: Track, slot: Slot) -> bool:
    """The user took the voice-over of a track off its episode: the group (series, season, episode) or its variant. A track
    cannot be hidden in its container, so the next sync removes it."""
    episode = episode_of(slot)
    return (voice_of(track.group, track.dub) in choices.off_groups | choices.voice_off.get(episode, frozenset())
            or variant_of(track) in choices.voice_deny.get(episode, frozenset()))


def count(inputs: Inputs):
    """The goals of the inputs, counted by the core (core.native.Planned): what a plan is made of, what the rule of names is
    asked (`items()`: slot, height, group, link, whether the video is cut, whether the episode is off) and what the view of
    the window is counted from."""
    return native.plan_goals(inputs)


def plan(inputs: Inputs, names: Iterable[str] | None = None, counted=None) -> Plan:
    """The goal, the graph and the groups of a series. `names` is the name of the file of every goal of `counted` (the
    series' own rule of names in the service); without them a plain name of the episode, the group and the height. Its rows
    are rows of the output only (`online_of`): a file made before of a row no group took stays as it is and takes no new
    work."""
    counted = count(inputs) if counted is None else counted
    names = [name_of(item) for item in counted.items()] if names is None else list(names)
    return _planned(inputs, _Frame(inputs), _goals(inputs, counted, names), counted)


class _Frame:
    """What every part of a plan reads of its inputs: the rows and the files by what they are, and where each file and
    track stands."""

    def __init__(self, inputs: Inputs) -> None:
        choices = inputs.choices
        self.online = {row.id: row for row in inputs.online}
        # A timeline is found as its row is: a row's own timeline is the row, a timeline several rows share is each of them.
        self.on_timeline = {**{row.timeline: row for row in inputs.online}, **self.online}
        self.deleted_videos = {each[1] for each in choices.delete if each[0] == "video"}
        self.deleted_tracks = {(each[1], each[2]) for each in choices.delete if each[0] == "track"}
        self.containers = sorted(inputs.containers, key=lambda each: each.name)

    def video_slot(self, video: Video) -> Slot:
        row = self.on_timeline.get(video.timeline) if video.cut_of is not None else self.online.get(video.id)
        return row.slot if row is not None and not row.compilation else video.episode

    def track_slot(self, track: Track) -> Slot:
        row = self.online.get(track.dub)
        return row.slot if row is not None and not row.compilation else track.episode


def _planned(inputs: Inputs, frame: _Frame, goals, counted) -> Plan:
    """The steps and the groups of the goals."""
    choices = inputs.choices
    ops = _ops(goals, frame.containers)
    groups = _groups(goals, ops, frame.containers, frame.deleted_videos, frame.video_slot, frame.track_slot,
                     lambda track, slot: switched_off(choices, track, slot), frame.deleted_tracks,
                     lambda variant, slot: variant in choices.subtitle_deny.get(episode_of(slot), frozenset()))
    return Plan(tuple(goals), tuple(ops), tuple(groups), counted)


def _goals(inputs: Inputs, counted, names: list[str]) -> list[Goal]:
    """The goal of every episode the rows or the files have, by the rule of the module docstring: counted by the core
    (core.native, the same rule step for step), made here of the objects of the inputs it names by their places."""
    online, files, subtitles = inputs.online, inputs.containers, inputs.subtitles

    def snapshot(spec) -> Snapshot:
        if spec[0] == "row":
            return online[spec[1]].snapshot()
        if spec[0] == "video":
            return files[spec[1]].video.snapshot
        return files[spec[1]].tracks[spec[2]].snapshot

    def key(spec) -> tuple:
        return ("row", RowId(*spec[1])) if spec[0] == "row" else ("cut", RowId(*spec[1]), RowId(*spec[2]))

    def way(spec) -> VideoWay:
        kind, made, timeline, snap, row, container, ref2, picture = spec
        return VideoWay(kind, key(made), RowId(*timeline), snapshot(snap), row=None if row is None else online[row],
                        container=None if container is None else files[container], ref2=None if ref2 is None else way(ref2),
                        picture=None if picture is None else files[picture])

    def subtitle(spec) -> SubtitleWay:
        if spec[0] == "reuse":
            _, container, at, default, forced = spec
            return SubtitleWay(replace(files[container].subtitles[at], default=default, forced=forced), "reuse",
                               container=files[container])
        _, at, default, forced, _ = spec
        row = subtitles[at]
        return SubtitleWay(Subtitle(row.id, row.label, row.player, row.timeline, row.language, row.subtitle_type, default,
                                    forced), "take", row=row)

    goals = []
    for ((slot, video, tracks, off, voices_off, own_at), (titles, markup, languages, held, unaligned)), name in zip(
            counted.specs(), names, strict=True):
        slot = tuple(slot)
        made = way(video)
        ways = tuple(TrackWay(voice, RowId(*dub), snapshot(snap), kind, row=None if row is None else online[row],
                              container=None if container is None else files[container], own=own)
                     for voice, dub, snap, kind, row, container, own in tracks)
        mark = None if markup is None else Markup(RowId(*markup[0]), markup[1], markup[2],
                                                  None if markup[3] is None else online[markup[3]], markup[4])
        goals.append(Goal(slot, made, ways, name, off, frozenset(voices_off), own_at, tuple(titles), mark,
                          tuple(languages), tuple(subtitle(each) for each in held), tuple(unaligned)))
    return goals


def timeline_shot(container: Container) -> Snapshot | None:
    """What the timeline a file lies on is: the video itself, or for a cut what the file kept of its timeline, else the sound
    of its timeline the file holds (a file made before the timeline was kept)."""
    video = container.video
    if video.cut_of is None:
        return video.snapshot
    return video.timeline_snapshot or next((track.snapshot for track in container.tracks if track.dub == video.timeline), None)


def timeline_variant(container: Container) -> tuple[str, int, str] | None:
    """The variant of the queue of the markup reference whose timeline a file lies on; none for a file whose timeline nothing tells."""
    shot = timeline_shot(container)
    if shot is None or shot.group is None:
        return None
    return (shot.variant_label, container.video.timeline.source_id, shot.player)


def _satisfied_by(goal: Goal) -> Container | None:
    """The existing container that already is the goal (perhaps under another name): its video and tracks, in order."""
    if goal.video.kind != "asset":
        return None
    container = goal.video.container
    if any(track.file for track in container.tracks):
        return None  # a track still lies beside it: the container is to be built with it inside
    if any(way.kind != "reuse" or way.container is not container or way.own for way in goal.tracks):
        return None
    if tuple(track.dub for track in container.tracks) != tuple(way.dub for way in goal.tracks):
        return None
    if any(way.kind != "reuse" or way.container is not container for way in goal.subtitles):
        return None
    if container.subtitles != tuple(way.subtitle for way in goal.subtitles):
        return None
    held = (container.own_at, container.titles, container.languages)
    return container if held == (goal.own_at, goal.titles, goal.languages) else None


def _ops(goals: Iterable[Goal], containers) -> list[Op]:
    ops: dict[str, Op] = {}
    for goal in goals:
        if _satisfied_by(goal) is not None:
            continue
        slot, mine = goal.slot, []
        video = goal.video
        before = []
        if video.kind == "download":
            before.append(_fetch(ops, video.row, slot))
        elif video.kind == "cut":
            needs = []
            if video.picture is None:
                picture = f"picture:{video.row.id}"
                ops.setdefault(picture, Op(picture, "download_picture", None, (), video.row))
                needs.append(picture)
            if video.ref2.kind == "download":
                needs.append(_fetch(ops, video.ref2.row, slot))
            ops[f"cut:{slot}"] = Op(f"cut:{slot}", "cut", slot, tuple(needs), video)
            mine += needs
            before.append(f"cut:{slot}")
        mine += before
        for way in goal.tracks:
            if way.kind == "reuse":
                continue
            if way.kind == "timeline":
                mine.append(_fetch(ops, way.row, slot))
                continue
            needs = list(before)
            if way.kind == "download":
                # Named by the row: a compilation that is a voice-over of several episodes is downloaded once.
                fetch = f"dub:{way.dub}"
                ops.setdefault(fetch, Op(fetch, "download_dub", None if way.row.compilation else slot, (), way.row))
                needs.append(fetch)
                mine.append(fetch)
            align = f"align:{slot}:{way.voice}"
            ops[align] = Op(align, "align", slot, tuple(needs), (goal, way), batch=f"align:{slot}")
            mine.append(align)
        for way in goal.subtitles:
            if way.kind == "take":
                key = subtitles_op(way.row)
                ops.setdefault(key, Op(key, "take_subtitles", slot, (), way.row))
                mine.append(key)
        ops[f"build:{slot}"] = Op(f"build:{slot}", "build", slot, tuple(dict.fromkeys(mine)), goal)
    return list(ops.values())


def subtitles_op(row: OnlineRow) -> str:
    """The step that takes a row of subtitles from its file: named by the row, once a sync."""
    return f"subs:{row.id}"


def video_op(goal: Goal) -> str:
    """The step that makes the video of a goal that is not held: its download or its cut."""
    return f"row:{goal.video.row.id}" if goal.video.kind == "download" else f"cut:{goal.slot}"


def _fetch(ops: dict[str, Op], row: OnlineRow, slot: Slot) -> str:
    """The download of a single row, one step whatever it is needed for (the video, the timeline of a cut, its sound):
    named by the row, so that a row is downloaded once in a sync even when the plan is made again."""
    key = f"row:{row.id}"
    ops.setdefault(key, Op(key, "download_video", slot, (), row))
    return key


def _groups(goals, ops, containers, deleted_videos, video_slot, track_slot, taken_off, deleted_tracks,
            subtitles_off=lambda variant, slot: False) -> list[Group]:
    parent: dict[str, str] = {}
    slot_of: dict[str, Slot] = {}

    def at(slot: Slot) -> str:
        node = "slot:" + ":".join(map(str, slot))  # the whole place: a part of an episode is a place of its own
        slot_of[node] = slot
        return node

    def find(node: str) -> str:
        while parent.setdefault(node, node) != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def join(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    by_slot = {goal.slot: goal for goal in goals}
    names = {container.name for container in containers}
    for goal in goals:
        here = at(goal.slot)
        find(here)
        if goal.name in names:
            join(here, f"file:{goal.name}")  # the goal is written where a file lies now: one group with that file
        if goal.video.container is not None:
            join(here, f"file:{goal.video.container.name}")
        if goal.video.ref2 is not None and goal.video.ref2.container is not None:
            join(here, f"file:{goal.video.ref2.container.name}")
        if goal.video.picture is not None:
            join(here, f"file:{goal.video.picture.name}")
        for way in goal.tracks:
            if way.container is not None:
                join(here, f"file:{way.container.name}")
        for way in goal.subtitles:
            if way.container is not None:
                join(here, f"file:{way.container.name}")
    for container in containers:
        here = f"file:{container.name}"
        join(here, at(video_slot(container.video)))
        # The number it stands under is the number the media server sees it by: another file may take that number in this
        # very sync (its row moved to another number), so the two are one group, applied together or not at all.
        join(here, at(container.video.episode))
        for track in container.tracks:
            join(here, at(track_slot(track)))

    kept = {goal.slot: container for goal in goals if (container := _satisfied_by(goal)) is not None}
    kept_names = {container.name for container in kept.values()}
    used_tracks = {(way.container.name, way.dub, way.own) for goal in goals for way in goal.tracks if way.container is not None}
    used_videos = {goal.video.container.name for goal in goals if goal.video.container is not None}
    used_subtitles = {(way.container.name, way.subtitle.row) for goal in goals for way in goal.subtitles
                      if way.container is not None}
    remove, hide = set(), set()
    for container in containers:
        if container.name in kept_names:
            continue
        video_ok = container.name in deleted_videos or video_slot(container.video) in by_slot
        tracks_ok = True
        sounds = [(track, False) for track in container.tracks]
        if container.name not in used_videos:
            sounds.append((own_track(container), True))
        for track, is_own in sounds:
            voice = voice_of(track.group, track.dub)
            here = video_slot(container.video) if is_own else track_slot(track)
            goal = by_slot.get(here)
            used = (container.name, track.dub, is_own) in used_tracks
            # Replaced: the goal of its episode has the voice-over otherwise — as another track or as the own sound.
            replaced = goal is not None and (voice_of(goal.video.snapshot.group, goal.video.timeline) == voice or any(
                way.voice == voice and not (way.container is container and way.dub == track.dub and way.own == is_own)
                for way in goal.tracks))
            # A sound goes with nothing in its place only by the user: a voice-over taken off, or «Удалить» at that track.
            deleted = not is_own and (container.name, track.dub) in deleted_tracks
            if not (used or replaced or deleted or taken_off(track, here)):
                tracks_ok = False
        # Subtitles go with nothing in their place only by the user (left out); else the goal of the episode has the variant.
        goal = by_slot.get(video_slot(container.video))
        subtitles_ok = all((container.name, each.row) in used_subtitles or subtitles_off(each.variant, video_slot(container.video))
                           or (goal is not None and each.variant in {way.subtitle.variant for way in goal.subtitles})
                           for each in container.subtitles)
        if video_ok and tracks_ok and subtitles_ok:
            remove.add(container.name)
        elif HIDDEN not in container.name:
            hide.add(container.name)

    # A hidden file takes a name of its own: the name of an episode switched off (`… .mkv.off`) is the goal's.
    taken = {container.name for container in containers} | {goal.name for goal in goals}
    hidden: dict[str, str] = {}
    for name in sorted(hide):
        stem = name.removesuffix(OFF).removesuffix(".mkv")
        number = 1
        while f"{stem}{HIDDEN}{number}.mkv{OFF}" in taken:
            number += 1
        hidden[name] = f"{stem}{HIDDEN}{number}.mkv{OFF}"
        taken.add(hidden[name])

    members: dict[str, list[str]] = {}
    for node in list(parent):
        members.setdefault(find(node), []).append(node)
    parts = [(sorted(slot_of[node] for node in nodes if node in slot_of), nodes) for nodes in members.values()]
    # The ops of a group: those of its episodes, and those of no episode that an op of its episodes needs — each op is
    # placed once by an index of the plan, not looked for again in every group.
    part_of = {slot: place for place, (slots, _nodes) in enumerate(parts) for slot in slots}
    needers: dict[str, set] = {}
    for each in ops:
        for need in each.needs or ():
            needers.setdefault(need, set()).add(each.slot)
    mine_of: list[list[str]] = [[] for _ in parts]
    for op in ops:
        if op.slot is not None:
            if op.slot in part_of:
                mine_of[part_of[op.slot]].append(op.id)
        else:
            for place in sorted({part_of[slot] for slot in needers.get(op.id, ()) if slot in part_of}):
                mine_of[place].append(op.id)
    groups = []
    for (slots, nodes), mine in zip(parts, mine_of):
        files = {node[5:] for node in nodes if node.startswith("file:")}
        groups.append(Group(
            slots=tuple(slots), ops=tuple(mine),
            kept=tuple((slot, kept[slot].name) for slot in slots if slot in kept),
            built=tuple(slot for slot in slots if slot in by_slot and slot not in kept),
            remove=tuple(sorted(files & remove)), hide=tuple((name, hidden[name]) for name in sorted(files & hide))))
    return sorted(groups, key=lambda each: each.slots)
