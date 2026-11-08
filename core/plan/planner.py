"""The planner of a series (stage 12, the table of an episode): a pure function of the tables and the priorities.

From the inputs (`tables.Inputs`) it derives the goal — what the container of every episode must be — and the graph of
operations that leads from the asset to it. There is no rule for a situation: renumbering, a better reference, a source
that forgot a row, a command «Удалить» are only other inputs.

Every part of the goal is reached by the first way that is open, the same list for every case:
  the video of an episode: 1) the asset holds it; 2) its row is online — download; 3) cut out of a compilation;
  the track of a voice-over to the video: 1) the asset holds it aligned to that video's timeline; 2) its row is online —
  download and align; 3) the asset holds it at another video — align it from that container (the container is the dub:
  its picture and the track are in sync).

The work comes in two phases. The operations run first, beside the old files. Only when every operation of a group of
episodes that share files has succeeded is the group applied: new containers take their names, what the goal holds anew
goes, what nothing carries stays hidden (`.off`). A file goes only when everything in it lives on in the goal or the user
said «Удалить»."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .tables import Container, Inputs, OnlineRow, RowId, Slot, Snapshot, Track, Video

OFF = ".off"
HIDDEN = ".hidden-"  # a file nothing carries any more: it stays, out of the media server's sight


@dataclass(frozen=True)
class VideoWay:
    """How the video of a goal is obtained: `asset` (held by `container`), `download` (`row`), `cut` (`row` is the
    compilation, `ref2` the way to the single video it is cut by)."""

    kind: str
    key: tuple
    timeline: RowId
    snapshot: Snapshot
    row: OnlineRow | None = None
    container: Container | None = None
    ref2: "VideoWay | None" = None


@dataclass(frozen=True)
class TrackWay:
    """How the track of a voice-over is obtained: `reuse` (the track of `container` is aligned to the goal's timeline),
    `download` (`row`, then align), `realign` (the track of `container` aligned anew to the goal's video). `own`: the track
    is the own sound of the video of `container` — the voice-over of a video it no longer is."""

    voice: str
    dub: RowId
    snapshot: Snapshot
    kind: str
    row: OnlineRow | None = None
    container: Container | None = None
    own: bool = False


@dataclass(frozen=True)
class Goal:
    slot: Slot
    video: VideoWay
    tracks: tuple[TrackWay, ...]
    name: str
    off: bool


@dataclass(frozen=True)
class Op:
    """A step of the graph. `subject` is what the step works on (a row, a way, a goal), `needs` the steps before it."""

    id: str
    kind: str
    slot: Slot | None
    needs: tuple[str, ...]
    subject: object


@dataclass(frozen=True)
class Group:
    """Episodes and files that share work: applied together once every operation of theirs has succeeded. `kept` are
    goals an existing file already is (renamed if its name differs), `built` the goals built anew, `remove` the files
    that go, `hide` the files nothing carries (they stay, hidden: old name, new name), `lost` the tracks that go with a video the user
    deleted, having nowhere else to live."""

    slots: tuple[Slot, ...]
    ops: tuple[str, ...]
    kept: tuple[tuple[Slot, str], ...]
    built: tuple[Slot, ...]
    remove: tuple[str, ...]
    hide: tuple[tuple[str, str], ...]
    lost: tuple[tuple[str, RowId], ...]


@dataclass(frozen=True)
class Plan:
    goals: tuple[Goal, ...]
    ops: tuple[Op, ...]
    groups: tuple[Group, ...]

    def goal(self, slot: Slot) -> Goal | None:
        return next((each for each in self.goals if each.slot == slot), None)


def voice_of(group: str | None, dub: RowId) -> str:
    """The key of a voice-over in a container: its group; an unknown voice-over is a voice of its own row."""
    return group if group is not None else f"?{dub.source_id}:{dub.row_key}:{dub.video}"


def name_of(slot: Slot, snapshot: Snapshot, off: bool) -> str:
    return f"s{slot[0]:02d}e{slot[1]:02d} [{snapshot.group or '—'}] {snapshot.height}p.mkv" + (OFF if off else "")


def own_track(container: Container) -> Track:
    """The own sound of the video of a container as a track: the voice-over of the row the video came from (for a cut,
    the compilation's), on the video's timeline. When the video gives way to another one, this voice-over lives on."""
    video = container.video
    return Track(video.cut_of or video.id, video.snapshot, video.episode, video.timeline)


def plan(inputs: Inputs, namer: Callable[[Slot, VideoWay, bool], str] | None = None) -> Plan:
    """The goal, the graph and the groups of a series. `namer` is the rule of the name of a file (the series' own rule of
    names in the service); without it a plain name of the episode, the group and the height."""
    choices = inputs.choices
    online = {row.id: row for row in inputs.online}
    singles = [row for row in inputs.online if not row.compilation]
    deleted_videos = {each[1] for each in choices.delete if each[0] == "video"}
    deleted_tracks = {(each[1], each[2]) for each in choices.delete if each[0] == "track"}
    containers = sorted(inputs.containers, key=lambda each: each.name)

    def video_slot(video: Video) -> Slot:
        row = online.get(video.timeline if video.cut_of is not None else video.id)
        return row.slot if row is not None and not row.compilation else video.episode

    def track_slot(track: Track) -> Slot:
        row = online.get(track.dub)
        return row.slot if row is not None and not row.compilation else track.episode

    players = {player: place for place, player in enumerate(choices.players)}
    sources = {source_id: place for place, source_id in enumerate(choices.sources)}
    voices_order = {voice: place for place, voice in enumerate(choices.groups)}

    def player_rank(player: str) -> int:
        return players.get(player, len(players))

    def source_rank(source_id: int) -> int:
        return sources.get(source_id, len(sources))

    def group_rank(voice: str) -> tuple:
        return (voices_order.get(voice, len(voices_order)), voice)

    # Everything laid out by episode once: a goal reads its own episode's rows, files and tracks.
    by_slot = Index()
    for row in singles:
        by_slot.rows.setdefault(row.slot, []).append(row)
    by_slot.compilations = [row for row in inputs.online if row.compilation]
    # Of several files of one video the best one stands for it: the highest, then a file of an episode before one nothing
    # carries any more, then one not switched off.
    for container in sorted(containers, key=lambda each: (-each.video.snapshot.height, HIDDEN in each.name, each.off,
                                                          each.name)):
        if container.name in deleted_videos:
            continue
        here = video_slot(container.video)
        by_slot.files.setdefault(here, []).append(container)
        by_slot.tracks.setdefault(here, [])
    for container in containers:
        if container.name not in deleted_videos:
            by_slot.tracks.setdefault(video_slot(container.video), []).append((container, own_track(container), True))
        for track in container.tracks:
            if (container.name, track.dub) not in deleted_tracks:
                by_slot.tracks.setdefault(track_slot(track), []).append((container, track, False))

    slots = sorted(set(by_slot.rows) | set(by_slot.files))
    namer = namer or (lambda slot, video, off: name_of(slot, video.snapshot, off))
    goals = [goal for slot in slots if (goal := _goal(slot, inputs, namer, by_slot, player_rank, source_rank,
                                                      group_rank)) is not None]
    ops = _ops(goals, containers)
    groups = _groups(goals, ops, containers, deleted_videos, deleted_tracks, video_slot, track_slot)
    return Plan(tuple(goals), tuple(ops), tuple(groups))


class Index:
    """The rows, files and tracks of a series by episode."""

    def __init__(self) -> None:
        self.rows: dict[Slot, list[OnlineRow]] = {}
        self.files: dict[Slot, list[Container]] = {}
        self.tracks: dict[Slot, list[tuple[Container, Track, bool]]] = {}
        self.compilations: list[OnlineRow] = []


def _goal(slot, inputs, namer, by_slot: Index, player_rank, source_rank, group_rank) -> Goal | None:
    choices = inputs.choices
    off = slot in choices.off_episodes
    order = choices.reference.get(slot, ())

    def user_rank(source_id: int, player: str) -> int:
        return order.index((source_id, player)) if (source_id, player) in order else len(order)

    candidates: list[tuple[tuple, VideoWay]] = []
    seen: set[tuple] = set()
    singles = by_slot.rows.get(slot, [])
    for container in by_slot.files.get(slot, []):
        video = container.video
        if video.key in seen:
            continue
        seen.add(video.key)
        way = VideoWay("asset", video.key, video.timeline, video.snapshot, container=container)
        source_id = (video.cut_of or video.id).source_id
        candidates.append(((user_rank(source_id, video.snapshot.player), -video.snapshot.height,
                            player_rank(video.snapshot.player), source_rank(source_id), 0, repr(video.key)), way))
    if not off:
        for row in singles:
            if ("download", row.id) in inputs.failed:
                continue
            held = next((way for _, way in candidates if way.key == ("row", row.id)), None)
            if held is not None and held.snapshot.height >= row.height:
                continue  # the asset holds this video at least as high: no download of it
            way = VideoWay("download", ("row", row.id), row.id, row.snapshot(), row=row)
            candidates.append(((user_rank(row.id.source_id, row.player), -row.height, player_rank(row.player),
                                source_rank(row.id.source_id), 1, repr(way.key)), way))
        cut = _cut(slot, inputs, by_slot.compilations, candidates, player_rank, source_rank, user_rank)
        if cut is not None:
            candidates.append(cut)
    if not candidates:
        return None
    video = min(candidates, key=lambda each: each[0])[1]
    own = voice_of(video.snapshot.group, video.timeline)

    voices: dict[str, list] = {}
    if not off:
        for row in singles:
            if row.group is not None and row.group not in choices.off_groups and row.group != own:
                voices.setdefault(row.group, [])
    held_tracks = [each for each in by_slot.tracks.get(slot, []) if not (each[2] and each[0] is video.container)]
    for container, track, _ in held_tracks:
        voice = voice_of(track.group, track.dub)
        if voice != own:
            voices.setdefault(voice, [])

    tracks: list[TrackWay] = []
    for voice in sorted(voices, key=group_rank):
        mine = [each for each in held_tracks if voice_of(each[1].group, each[1].dub) == voice]
        reuse = sorted((each for each in mine if each[1].aligned_to == video.timeline),
                       key=lambda each: (each[0] is not video.container, each[2], each[0].name))
        if reuse:
            container, track, is_own = reuse[0]
            tracks.append(TrackWay(voice, track.dub, track.snapshot, "reuse", container=container, own=is_own))
            continue
        rows = sorted((row for row in singles if row.group == voice and not off
                       and ("download", row.id) not in inputs.failed and ("align", row.id, video.timeline) not in inputs.failed
                       and voice not in choices.off_groups),
                      key=lambda row: (player_rank(row.player), source_rank(row.id.source_id), -row.height, row.id))
        if rows:
            tracks.append(TrackWay(voice, rows[0].id, rows[0].snapshot(), "download", row=rows[0]))
            continue
        mine = [each for each in mine if ("align", each[1].dub, video.timeline) not in inputs.failed]
        if mine and not off:
            container, track, is_own = sorted(mine, key=lambda each: (each[2], each[0].name, each[1].dub))[0]
            tracks.append(TrackWay(voice, track.dub, track.snapshot, "realign", container=container, own=is_own))
    return Goal(slot, video, tuple(tracks), namer(slot, video, off), off)


def _cut(slot, inputs, compilations, candidates, player_rank, source_rank, user_rank):
    """The cut out of the shortest switched-on compilation that covers the episode and is higher than its best single
    video, by that single video (REF 2); none when the episode has no single video."""
    singles = [(rank, way) for rank, way in candidates if way.key[0] == "row"]
    if not singles:
        return None
    rank2, ref2 = min(singles, key=lambda each: each[0])
    best = max(way.snapshot.height for _, way in singles)
    covering = [row for row in compilations if ("download", row.id) not in inputs.failed
                and ("cut", row.id, ref2.timeline) not in inputs.failed
                and row.season == slot[0] and row.start <= slot[1] <= row.end
                and row.group is not None and row.id not in inputs.choices.off_compilations and row.height > best]
    if not covering:
        return None
    compilation = min(covering, key=lambda row: (row.end - row.start, -row.height, source_rank(row.id.source_id), row.id))
    key = ("cut", compilation.id, ref2.timeline)
    if any(way.key == key and way.snapshot.height >= compilation.height for _, way in candidates):
        return None  # the asset holds this cut already, as high as the compilation is now
    snapshot = compilation.snapshot()
    way = VideoWay("cut", key, ref2.timeline, snapshot, row=compilation, ref2=ref2)
    return ((user_rank(compilation.id.source_id, compilation.player), -compilation.height, player_rank(compilation.player),
             source_rank(compilation.id.source_id), 1, repr(key)), way)


def _satisfied_by(goal: Goal) -> Container | None:
    """The existing container that already is the goal (perhaps under another name): its video and tracks, in order."""
    if goal.video.kind != "asset":
        return None
    container = goal.video.container
    if any(way.kind != "reuse" or way.container is not container or way.own for way in goal.tracks):
        return None
    if tuple(track.dub for track in container.tracks) != tuple(way.dub for way in goal.tracks):
        return None
    return container


def _ops(goals: Iterable[Goal], containers) -> list[Op]:
    ops: dict[str, Op] = {}
    for goal in goals:
        if _satisfied_by(goal) is not None:
            continue
        slot, mine = goal.slot, []
        video = goal.video
        if video.kind == "download":
            ops[f"video:{slot}"] = Op(f"video:{slot}", "download_video", slot, (), video.row)
            mine.append(f"video:{slot}")
        elif video.kind == "cut":
            compilation = f"compilation:{video.row.id}"
            ops.setdefault(compilation, Op(compilation, "download_compilation", None, (), video.row))
            needs = [compilation]
            if video.ref2.kind == "download":
                ref2 = f"ref2:{slot}"
                ops[ref2] = Op(ref2, "download_video", slot, (), video.ref2.row)
                needs.append(ref2)
                mine.append(ref2)
            ops[f"cut:{slot}"] = Op(f"cut:{slot}", "cut", slot, tuple(needs), video)
            mine += [compilation, f"cut:{slot}"]
        before = [each for each in mine if each.startswith(("video:", "cut:"))]
        for way in goal.tracks:
            if way.kind == "reuse":
                continue
            needs = list(before)
            if way.kind == "download":
                fetch = f"dub:{slot}:{way.dub}"
                ops[fetch] = Op(fetch, "download_dub", slot, (), way.row)
                needs.append(fetch)
                mine.append(fetch)
            align = f"align:{slot}:{way.voice}"
            ops[align] = Op(align, "align", slot, tuple(needs), (goal, way))
            mine.append(align)
        ops[f"build:{slot}"] = Op(f"build:{slot}", "build", slot, tuple(dict.fromkeys(mine)), goal)
    return list(ops.values())


def _groups(goals, ops, containers, deleted_videos, deleted_tracks, video_slot, track_slot) -> list[Group]:
    parent: dict[str, str] = {}
    slot_of: dict[str, Slot] = {}

    def at(slot: Slot) -> str:
        node = f"slot:{slot[0]}:{slot[1]}"
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
        for way in goal.tracks:
            if way.container is not None:
                join(here, f"file:{way.container.name}")
    for container in containers:
        here = f"file:{container.name}"
        join(here, at(video_slot(container.video)))
        for track in container.tracks:
            join(here, at(track_slot(track)))

    kept = {goal.slot: container for goal in goals if (container := _satisfied_by(goal)) is not None}
    kept_names = {container.name for container in kept.values()}
    used_tracks = {(way.container.name, way.dub, way.own) for goal in goals for way in goal.tracks if way.container is not None}
    used_videos = {goal.video.container.name for goal in goals if goal.video.container is not None}
    remove, hide, lost = set(), set(), []
    for container in containers:
        if container.name in kept_names:
            continue
        video_deleted = container.name in deleted_videos
        video_ok = video_deleted or video_slot(container.video) in by_slot
        video_used = container.name in used_videos
        tracks_ok, gone_with_video = True, []
        sounds = [(track, False) for track in container.tracks]
        if not video_deleted and not video_used:
            sounds.append((own_track(container), True))
        for track, is_own in sounds:
            voice = voice_of(track.group, track.dub)
            goal = by_slot.get(video_slot(container.video) if is_own else track_slot(track))
            used = (container.name, track.dub, is_own) in used_tracks
            # Replaced: the goal of its episode has the voice-over otherwise — as another track or as the own sound.
            replaced = goal is not None and (voice_of(goal.video.snapshot.group, goal.video.timeline) == voice or any(
                way.voice == voice and not (way.container is container and way.dub == track.dub and way.own == is_own)
                for way in goal.tracks))
            deleted = (container.name, track.dub) in deleted_tracks
            if not (used or replaced or deleted):
                if video_deleted:
                    gone_with_video.append((container.name, track.dub))
                else:
                    tracks_ok = False
        if video_ok and tracks_ok:
            remove.add(container.name)
            lost += gone_with_video
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
    groups = []
    for nodes in members.values():
        slots = sorted(slot_of[node] for node in nodes if node in slot_of)
        files = {node[5:] for node in nodes if node.startswith("file:")}
        mine = tuple(op.id for op in ops if op.slot in slots or (op.slot is None and any(
            each.needs and op.id in each.needs for each in ops if each.slot in slots)))
        groups.append(Group(
            slots=tuple(slots), ops=mine,
            kept=tuple((slot, kept[slot].name) for slot in slots if slot in kept),
            built=tuple(slot for slot in slots if slot in by_slot and slot not in kept),
            remove=tuple(sorted(files & remove)), hide=tuple((name, hidden[name]) for name in sorted(files & hide)),
            lost=tuple(sorted((name, dub) for name, dub in lost if name in files))))
    return sorted(groups, key=lambda each: each.slots)
