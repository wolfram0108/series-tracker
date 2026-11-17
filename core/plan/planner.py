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

from dataclasses import dataclass
from typing import Callable, Iterable

from core.subtitle_queue import ordered as subtitles_ordered
from core.variant_queue import NO_SAME, VARIANT_RANKING, VOICE_RANKING, Line, standing
from core.variant_queue import Candidate as Ranked
from core.variant_queue import Order, by_resolution

from .tables import Choices, Container, Inputs, OnlineRow, RowId, Slot, Snapshot, Subtitle, Track, Video, episode_of

RULE = object()  # no word of the user on the compilation of an episode: the rule decides


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

    def goal(self, slot: Slot) -> Goal | None:
        return next((each for each in self.goals if each.slot == slot), None)


def restricted(made: Plan, holds: Callable[[Slot], bool]) -> Plan:
    """The plan of a sync that covers a part of the series: the goals stay all (the window shows the series as it is, the
    plan of an episode is read from them), the steps and the groups are those of the groups that hold a place `holds` says
    is of the scope. A group is the episodes and files that share work, so a step of one group never needs a step of another:
    nothing of the others is done, applied, removed or hidden."""
    groups = tuple(group for group in made.groups if any(holds(slot) for slot in group.slots))
    wanted = {op for group in groups for op in group.ops}
    return Plan(made.goals, tuple(op for op in made.ops if op.id in wanted), groups)


def voice_of(group: str | None, dub: RowId) -> str:
    """The key of a voice-over in a container: its group; an unknown voice-over is a voice of its own row."""
    return group if group is not None else f"?{dub.source_id}:{dub.row_key}:{dub.video}"


def name_of(slot: Slot, snapshot: Snapshot, off: bool) -> str:
    part = f" pt{slot[2]}" if len(slot) > 2 else ""
    return f"s{slot[0]:02d}e{slot[1]:02d}{part} [{snapshot.group or '—'}] {snapshot.height}p.mkv" + (OFF if off else "")


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


def plan(inputs: Inputs, namer: Callable[[Slot, VideoWay, bool], str] | None = None) -> Plan:
    """The goal, the graph and the groups of a series. `namer` is the rule of the name of a file (the series' own rule of
    names in the service); without it a plain name of the episode, the group and the height. Its rows are rows of the
    output only (`online_of`): a file made before of a row no group took stays as it is and takes no new work."""
    choices = inputs.choices
    online = {row.id: row for row in inputs.online}
    # A timeline is found as its row is: a row's own timeline is the row, a timeline several rows share is each of them.
    on_timeline = {**{row.timeline: row for row in inputs.online}, **online}
    singles = [row for row in inputs.online if not row.compilation]
    deleted_videos = {each[1] for each in choices.delete if each[0] == "video"}
    deleted_tracks = {(each[1], each[2]) for each in choices.delete if each[0] == "track"}
    containers = sorted(inputs.containers, key=lambda each: each.name)

    def video_slot(video: Video) -> Slot:
        row = on_timeline.get(video.timeline) if video.cut_of is not None else online.get(video.id)
        return row.slot if row is not None and not row.compilation else video.episode

    def track_slot(track: Track) -> Slot:
        row = online.get(track.dub)
        return row.slot if row is not None and not row.compilation else track.episode

    order = Order(tuple(choices.players), {source_id: place for place, source_id in enumerate(choices.sources)},
                  choices.player_groups)
    voices_order = {season: {voice: place for place, voice in enumerate(order)} for season, order in choices.groups.items()}
    last = len(inputs.online)

    def criteria(ranking, height: int, player: str, source_id: int) -> tuple:
        """The service's own criteria (core/variant_queue), the place of the row aside: it is the last tie-break."""
        item = Ranked(Line(height, player, source_id), 0)
        return tuple(each(item, order) for each in ranking)

    def position(row_id: RowId) -> int:
        row = online.get(row_id)
        return row.position if row is not None else last

    def group_rank(season: int, voice: str) -> tuple:
        places = voices_order.get(season, {})
        return (places.get(voice, len(places)), voice)

    def own_place(season: int, own: str, voices: Iterable[str]) -> int:
        if own not in voices_order.get(season, {}):
            return 0
        return sum(1 for voice in voices if group_rank(season, voice) < group_rank(season, own))

    # Everything laid out by episode once: a goal reads its own episode's rows, files and tracks.
    by_slot = Index()
    for row in singles:
        by_slot.rows.setdefault(row.slot, []).append(row)
    by_slot.compilations = [row for row in inputs.online if row.compilation]
    for row in inputs.subtitles:
        if not row.compilation:
            by_slot.subtitles.setdefault(row.slot, []).append(row)
    # Of several files of one video the best one stands for it: one not deleted, the highest, then a file of an episode
    # before one nothing carries any more, then one not switched off.
    by_slot.deleted = deleted_videos
    for container in sorted(containers, key=lambda each: (each.name in deleted_videos, -each.video.snapshot.height,
                                                          HIDDEN in each.name, each.off, each.name)):
        here = video_slot(container.video)
        by_slot.files.setdefault(here, []).append(container)
        by_slot.tracks.setdefault(here, [])
    # Every sound of every file, with whether it may be taken as it is: not the own sound of a deleted video, not a deleted
    # track. Those stay sources to align from (the module docstring: «Удалить»).
    for container in containers:
        by_slot.tracks.setdefault(video_slot(container.video), []).append(
            (container, own_track(container), True, container.name in deleted_videos))
        for track in container.tracks:
            by_slot.tracks.setdefault(track_slot(track), []).append(
                (container, track, False, (container.name, track.dub) in deleted_tracks))

    slots = sorted(set(by_slot.rows) | set(by_slot.files))
    namer = namer or (lambda slot, video, off: name_of(slot, video.snapshot, off))
    goals = [goal for slot in slots if (goal := _goal(slot, inputs, namer, by_slot, criteria, position,
                                                      group_rank, own_place)) is not None]
    ops = _ops(goals, containers)
    groups = _groups(goals, ops, containers, deleted_videos, video_slot, track_slot,
                     lambda track, slot: switched_off(choices, track, slot), deleted_tracks,
                     lambda variant, slot: variant in choices.subtitle_deny.get(episode_of(slot), frozenset()))
    return Plan(tuple(goals), tuple(ops), tuple(groups))


class Index:
    """The rows, files and tracks of a series by episode."""

    def __init__(self) -> None:
        self.rows: dict[Slot, list[OnlineRow]] = {}
        self.files: dict[Slot, list[Container]] = {}
        # (file, track, the own sound of its video, deleted: not to be taken as it is)
        self.tracks: dict[Slot, list[tuple[Container, Track, bool, bool]]] = {}
        self.compilations: list[OnlineRow] = []
        self.deleted: set[str] = set()  # the files of the videos the user deleted
        self.subtitles: dict[Slot, list[OnlineRow]] = {}  # the rows of subtitles of every episode (DESIGN.md 7.1.1)


def _goal(slot, inputs, namer, by_slot: Index, criteria, position, group_rank, own_place) -> Goal | None:
    choices = inputs.choices
    episode = episode_of(slot)  # the levers are the episode's, for all its parts
    off = episode in choices.off_episodes
    wanted = choices.reference.get(episode, ())
    denied = choices.reference_deny.get(episode, frozenset())
    same = choices.same.get(episode, NO_SAME)

    def video_rank(variant: tuple[str, int, str], height: int, held: bool, row_id: RowId, deleted: bool = False) -> tuple:
        """The service's rule for the reference: a video the user deleted comes after every other one (it goes only for
        another video, В-9); the user's order, the resolution, then the criteria of the variant — the variants the user
        declared equal take one place in both —; of equals what the asset holds stays (a video goes only for a better
        one); then the place of the row in the layout."""
        named, *by_variant = standing(variant, same, lambda each: wanted.index(each) if each in wanted else len(wanted),
                                      lambda each: criteria(VARIANT_RANKING, 0, each[2], each[1]))
        return (deleted, named, *criteria((by_resolution,), height, variant[2], variant[1]), *by_variant, 0 if held else 1,
                position(row_id), repr(row_id))

    def gone(way: VideoWay) -> bool:
        return way.container is not None and way.container.name in by_slot.deleted

    candidates: list[tuple[tuple, VideoWay]] = []
    seen: set[tuple] = set()
    singles = by_slot.rows.get(slot, [])
    markup = _markup_of(inputs, episode, singles, [each for each in by_slot.files.get(slot, [])
                                                   if each.name not in by_slot.deleted], criteria, position)
    # The queue of the reference leaves a variant out as a picture; the markup reference is a queue of its own, so the
    # video of its row stays a way to the timeline whatever the reference says of it.
    timeline = ("row", markup.id) if markup is not None else None
    for container in by_slot.files.get(slot, []):
        video = container.video
        source = video.cut_of or video.id
        variant = (video.snapshot.variant_label, source.source_id, video.snapshot.player)
        if video.key in seen or (variant in denied and video.key != timeline):
            continue
        seen.add(video.key)
        way = VideoWay("asset", video.key, video.timeline, video.snapshot, container=container)
        candidates.append((video_rank(variant, video.snapshot.height, True, source, gone(way)), way))
    if not off:
        for row in singles:
            if not row.playable or ("download", row.id) in inputs.failed or (row.variant in denied and
                                                                              ("row", row.timeline) != timeline):
                continue
            held = next((way for _, way in candidates if way.key == ("row", row.timeline) and not gone(way)), None)
            if held is not None and held.snapshot.height >= row.height:
                continue  # the asset holds this video at least as high: no download of it
            way = VideoWay("download", ("row", row.timeline), row.timeline, row.snapshot(), row=row)
            candidates.append((video_rank(row.variant, row.height, False, row.id), way))
        candidates += _cuts(slot, inputs, by_slot.compilations, singles,
                            [each for each in candidates if not gone(each[1])], video_rank, denied, markup)
    # The user's word on the compilation is stronger than what the asset holds: «none» leaves out every video cut out of a
    # compilation; a compilation that fits leaves out the videos cut out of the others. A cut whose picture is no single
    # row of the episode the layout gives is taken for a compilation's.
    word = choices.member.get(episode, RULE)
    pictures = {row.id for row in singles}

    def compiled(way: VideoWay) -> bool:
        return way.key[0] == "cut" and way.key[1] not in pictures

    if word is None:
        candidates = [each for each in candidates if not compiled(each[1])]
    elif word is not RULE and any(compiled(each[1]) and each[1].key[1] == word for each in candidates):
        candidates = [each for each in candidates if not compiled(each[1]) or each[1].key[1] == word]
    if not candidates:
        return None
    # With a markup reference every video on its timeline comes before every other one; a video the user deleted still
    # comes after them all (it goes only for another video).
    video = min(candidates, key=lambda each: (each[0][0], markup is not None and each[1].timeline != markup.id,
                                              *each[0][1:]))[1]
    own = voice_of(video.snapshot.group, video.timeline)
    # A switched-off episode, and a deleted video that stays for want of another one, take no new work: what the file
    # holds stays as it is.
    idle = off or gone(video)

    taken_off = choices.off_groups | choices.voice_off.get(episode, frozenset())
    left_out = choices.voice_deny.get(episode, frozenset())
    # The voice-overs of an episode are its single rows and the compilations that cover it (Д-19), but the compilation
    # its own video is cut out of: that one is its own sound. A part of an episode takes the rows of that part only.
    dubbing = [row for row in singles if row.playable] + [
        row for row in by_slot.compilations if len(slot) == 2 and row.season == slot[0] and row.playable
        and row.start <= slot[1] <= row.end and row.id != (video.row.id if video.kind == "cut" else None)]
    voices: dict[str, list] = {}
    if not idle:
        for row in dubbing:
            if row.group not in taken_off and row.group != own:
                voices.setdefault(row.group, [])
    held_tracks = [each for each in by_slot.tracks.get(slot, []) if not (each[2] and each[0] is video.container)
                   and not switched_off(choices, each[1], slot)]
    for container, track, _, _ in held_tracks:
        voice = voice_of(track.group, track.dub)
        if voice != own:
            voices.setdefault(voice, [])

    # The sound of the markup reference is the last of the file, out of the order of the season (stage 14).
    last = voice_of(markup.group, markup.id) if markup is not None else None
    # A video laid onto the timeline of another row: the sound of that row is in step with it as it is.
    cut = video.kind == "cut" or (video.container is not None and video.container.video.cut_of is not None)
    tracks: list[TrackWay] = []
    for voice in sorted(voices, key=lambda voice: (voice == last, group_rank(slot[0], voice))):
        mine = [each for each in held_tracks if voice_of(each[1].group, each[1].dub) == voice]
        reuse = sorted((each for each in mine if each[1].aligned_to == video.timeline and not each[3]),
                       key=lambda each: (each[0] is not video.container, each[2], each[0].name))
        if reuse:
            container, track, is_own, _ = reuse[0]
            tracks.append(TrackWay(voice, track.dub, track.snapshot, "reuse", container=container, own=is_own))
            continue
        preferred = choices.voice_order.get((episode, voice), ())
        rows = sorted((row for row in dubbing if row.group == voice and not idle and voice not in taken_off
                       and row.variant not in left_out
                       and ("download", row.id) not in inputs.failed and ("align", row.id, video.timeline) not in inputs.failed),
                      key=lambda row: ((preferred.index((row.id.source_id, row.player)) if (row.id.source_id, row.player)
                                        in preferred else len(preferred)),
                                       *criteria(VOICE_RANKING, row.height, row.player, row.id.source_id),
                                       row.position, repr(row.id)))
        # The sound of a row on the video's own timeline is in step with it as it is: the timeline of a cut, or another
        # track of the same video (stage 15, И-3).
        if rows and rows[0].timeline == video.timeline:
            tracks.append(TrackWay(voice, rows[0].id, rows[0].snapshot(), "timeline", row=rows[0]))
            continue
        if rows:
            tracks.append(TrackWay(voice, rows[0].id, rows[0].snapshot(), "download", row=rows[0]))
            continue
        # A track the user deleted is no source of itself: «Удалить» takes it out of the file, it is not aligned anew (Д-1).
        usable = [each for each in mine if not (each[3] and not each[2])]
        sources = [each for each in usable if ("align", each[1].dub, video.timeline) not in inputs.failed]
        if sources and not idle:
            container, track, is_own, _ = sorted(sources, key=lambda each: (each[2], each[0].name, each[1].dub))[0]
            tracks.append(TrackWay(voice, track.dub, track.snapshot, "realign", container=container, own=is_own))
            continue
        # A file that takes no new work stays as it is, a deleted track in it too; elsewhere a deleted track goes.
        waiting = sorted((each for each in (mine if idle else usable) if each[1].aligned_to == video.timeline),
                         key=lambda each: (each[0] is not video.container, each[2], each[0].name))
        if waiting:
            container, track, is_own, _ = waiting[0]
            tracks.append(TrackWay(voice, track.dub, track.snapshot, "reuse", container=container, own=is_own))
    # The names of the sounds the user gave; an idle file keeps the names it was written under (no new work).
    sounds = {video.snapshot.group or "—"} | {way.snapshot.group or way.voice for way in tracks}
    titles = tuple(sorted((label, title) for label, title in choices.titles.items() if label in sounds))
    languages = tuple(sorted((label, code) for label, code in choices.languages.items() if label in sounds))
    if idle and video.container is not None:
        titles, languages = video.container.titles, video.container.languages
    own_at = len(tracks) if own == last else own_place(slot[0], own, (way.voice for way in tracks if way.voice != last))
    subtitles, unaligned = _subtitles(slot, inputs, by_slot, video, idle)
    return Goal(slot, video, tuple(tracks), namer(slot, video, off), off, frozenset(taken_off), own_at, titles, markup,
                languages, subtitles, unaligned)


def _subtitles(slot: Slot, inputs: Inputs, by_slot: "Index", video: VideoWay, idle: bool
               ) -> tuple[tuple[SubtitleWay, ...], tuple[tuple[str, int, str], ...]]:
    """The subtitles of a goal in the order of the container (core.subtitle_queue), and the variants it takes that are on
    no timeline of its video. A track is in step only on the video's own timeline: one held there by a file of the episode
    stays (its row may be gone, the release archived), one of a row on it is taken from the row's file; another timeline
    waits for the alignment of subtitles (stage 15, step 10). An idle file keeps what it holds."""
    if idle:
        held = video.container.subtitles if video.container is not None else ()
        return tuple(SubtitleWay(each, "reuse", container=video.container) for each in held), ()
    choices = inputs.choices
    episode = episode_of(slot)
    rows = by_slot.subtitles.get(slot, [])
    places = {source_id: place for place, source_id in enumerate(choices.sources)}
    # What the files of the episode on this timeline hold, the file of the video first.
    files = sorted((each for each in by_slot.files.get(slot, []) if each.video.timeline == video.timeline),
                   key=lambda each: (each is not video.container, each.name))
    held: dict[tuple[str, int, str], tuple[Container, Subtitle]] = {}
    for container in files:
        for each in container.subtitles:
            if each.timeline == video.timeline:
                held.setdefault(each.variant, (container, each))
    last = len(rows) + 1
    found = [(row.variant, places.get(row.source_id, len(places)), row.position) for row in rows]
    found += [(variant, places.get(variant[1], len(places)), last) for variant in held]
    denied = choices.subtitle_deny.get(episode, frozenset())
    ways, unaligned = [], []
    for variant in subtitles_ordered(found, choices.subtitle_order.get(episode, ())):
        if variant in denied:
            continue
        in_step = sorted((row for row in rows if row.variant == variant and row.timeline == video.timeline and row.playable
                          and ("download", row.id) not in inputs.failed), key=lambda row: (row.position, repr(row.id)))
        kept = held.get(variant)
        if kept is not None and (not in_step or kept[1].row == in_step[0].id):
            ways.append(SubtitleWay(kept[1], "reuse", container=kept[0]))
        elif in_step:
            row = in_step[0]
            ways.append(SubtitleWay(Subtitle(row.id, row.label, row.player, row.timeline, row.language, row.subtitle_type),
                                    "take", row=row))
        elif any(row.variant == variant for row in rows):
            unaligned.append(variant)
    return tuple(ways), tuple(unaligned)


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


def _markup_of(inputs: Inputs, episode, singles: list[OnlineRow], files: list[Container], criteria,
               position) -> Markup | None:
    """The markup reference of the episode (stage 14) by its queue: the user's order, then the service's rule, then what
    the library holds (a timeline goes only for a better one, as a video does), then the place of the row. Its candidates
    are the rows of the place of the episode that play, did not fail to download, have a voice-over group (an unknown one
    takes no work, Р-77) and are not left out of the queue, and the timelines the files of the episode lie on (a source
    that forgets a row does not move the timeline). None: the episode follows its reference."""
    order = inputs.choices.markup.get(episode)
    if order is None:
        return None
    denied = inputs.choices.markup_deny.get(episode, frozenset())

    same = inputs.choices.markup_same.get(episode, NO_SAME)

    def rank(variant: tuple[str, int, str], height: int, player: str, row_id: RowId, held: bool) -> tuple:
        named, *by_variant = standing(variant, same, lambda each: order.index(each) if each in order else len(order),
                                      lambda each: criteria(VARIANT_RANKING, 0, each[2], each[1]))
        return (named, *criteria((by_resolution,), height, player, row_id.source_id), *by_variant, 0 if held else 1,
                position(row_id), repr(row_id))

    found: list[tuple[tuple, Markup]] = []
    for row in singles:
        if row.playable and row.variant not in denied and ("download", row.id) not in inputs.failed:
            found.append((rank(row.variant, row.height, row.player, row.id, False),
                          Markup(row.timeline, row.group, row.height, row, row.variant)))
    online = {**{row.timeline: row for row in singles}, **{row.id: row for row in singles}}
    for container in files:
        timeline = container.video.timeline
        shot = timeline_shot(container)
        if shot is None or shot.group is None:
            continue
        variant = (shot.variant_label, timeline.source_id, shot.player)
        if variant not in denied:
            found.append((rank(variant, shot.height, shot.player, timeline, True),
                          Markup(timeline, shot.group, shot.height, online.get(timeline), variant)))
    return min(found, key=lambda each: each[0])[1] if found else None


def _cuts(slot, inputs, compilations, singles, candidates, video_rank, denied, markup) -> list[tuple[tuple, VideoWay]]:
    """The videos a cut can give the episode. The timeline (REF 2) is the markup row, else the best single video. The
    pictures are higher than it: with a markup reference every single row and the compilation the episode stands in,
    without one that compilation only. The compilation is the one the user named while it fits, else the shortest
    switched-on one that covers the episode; none when the user said none. An episode in parts is not cut."""
    if len(slot) > 2:
        return []
    word = inputs.choices.member.get(episode_of(slot), RULE)
    held = [(rank, way) for rank, way in candidates if way.key[0] == "row"]
    if markup is not None:
        ref2 = min((way for _, way in held if way.key == ("row", markup.id)),
                   key=lambda way: way.kind != "asset", default=None)
        if ref2 is None:
            return []
        # A cut is only of a picture higher than the best the timeline itself gives: a file that holds it lower than its row
        # is listed now is as high as that row, which the plan may download instead (unless it cannot be downloaded).
        row = markup.row
        floor = max(markup.height, row.height if row is not None and row.playable and ("download", row.id) not in inputs.failed
                    else 0)
    elif held:
        ref2 = min(held, key=lambda each: each[0])[1]
        floor = max(way.snapshot.height for _, way in held)
    else:
        return []
    timeline = ref2.timeline
    failed = inputs.failed

    def fits(row: OnlineRow) -> bool:
        # A variant left out is no reference in any form: file, download or cut.
        return (row.playable and row.height > floor and row.variant not in denied
                and ("download", row.id) not in failed and ("cut", row.id, timeline) not in failed)

    pictures = [row for row in singles if markup is not None and row.timeline != timeline and fits(row)]
    covering = [row for row in compilations if word is not None and fits(row) and row.season == slot[0]
                and row.start <= slot[1] <= row.end and row.id not in inputs.choices.off_compilations]
    named = [row for row in covering if row.id == word]
    covering = named or covering
    if covering:
        pictures.append(min(covering, key=lambda row: (row.end - row.start, -row.height, row.position, repr(row.id))))
    found = []
    for row in pictures:
        key = ("cut", row.id, timeline)
        if any(way.key == key and way.snapshot.height >= row.height for _, way in candidates):
            continue  # the asset holds this cut already, as high as the picture is now
        # The library holds the download of the picture's row as high: the cut takes that file.
        picture = next((way.container for _, way in held if way.kind == "asset" and way.key == ("row", row.timeline)
                        and way.snapshot.height >= row.height), None)
        way = VideoWay("cut", key, timeline, row.snapshot(), row=row, ref2=ref2, picture=picture)
        found.append((video_rank(row.variant, row.height, False, row.id), way))
    return found


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
            remove=tuple(sorted(files & remove)), hide=tuple((name, hidden[name]) for name in sorted(files & hide))))
    return sorted(groups, key=lambda each: each.slots)
