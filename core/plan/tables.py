"""The tables the planner of a series reads (stage 12, the table of an episode): what the sources give now, what the
library holds and how it was obtained, and what the user chose. Nothing else is an input of the planner.

An online row is a row of a source that the rules of the constructor gave to an episode at the last complete and
successful read. The asset is what is on disk: containers, each with one video and the tracks aligned to it. A row of an
episode is online, offline (the asset holds a file of it, the read did not give it) or forgotten (neither)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from core import native

# (season, episode), or (season, episode, part) for a part of an episode: the place of one video of the library.
Slot = tuple[int, ...]


def episode_of(slot: Slot) -> tuple[int, int]:
    """The episode of a place: the levers and the choices of the user are the episode's, for all its parts."""
    return slot[0], slot[1]


@dataclass(frozen=True, order=True)
class RowId:
    """A row of a source table: the source, the key of the row in its table and the video behind it as its host numbers
    it. Another video behind the same row is another row (the host uploaded something else)."""

    source_id: int
    row_key: str
    video: str = ""

    def fields(self) -> dict:
        """Its fields as a dict, in their order (what `dataclasses.asdict` gives, with no deep copy of a few texts)."""
        return {"source_id": self.source_id, "row_key": self.row_key, "video": self.video}


@dataclass(frozen=True)
class Snapshot:
    """Where a file came from, written once when it was obtained: it stays when the source forgets the row."""

    source: str
    player: str
    group: str | None
    height: int
    link: str = ""
    # The name of the voice-over as the layout calls it (an unknown one too: «Неизвестная озвучка N»): a variant's name.
    label: str = ""

    @property
    def variant_label(self) -> str:
        return self.label or self.group or ""

    def fields(self) -> dict:
        """Its fields as a dict, in their order (what `dataclasses.asdict` gives, with no deep copy of a few texts)."""
        return {"source": self.source, "player": self.player, "group": self.group, "height": self.height, "link": self.link,
                "label": self.label}


# A row of the output (`core/plan/inputs.online_of`): kept by the core, read here as a row of the tables (its fields and
# properties: id, season, start, end, player, group, height, source, link, label, position, part, playable, current,
# player_label, timeline_key, track, language, subtitle_type; timeline, source_id, variant, compilation, slot, snapshot()).
OnlineRow = native.OnlineRow
# The rows of the output as the core keeps them: a sequence of OnlineRow, made into objects only when read here.
Sequence.register(native.OnlineRows)


@dataclass(frozen=True)
class Video:
    """The video of a container. `timeline` is the row whose timeline it is on: the row itself for a download, the single
    episode it was cut by (REF 2) for a video cut out of a compilation (`cut_of`); a track aligned to that timeline is in
    sync with it. `episode` is the last number its row had."""

    id: RowId
    snapshot: Snapshot
    episode: Slot
    timeline: RowId
    cut_of: RowId | None = None
    # What the timeline of a cut was when the file was made: its voice-over group, player and height. The file may hold no
    # sound of it (the picture's own voice-over is the same group), and a timeline whose row the source no longer lists
    # or has given another video is known by this alone. Not a part of what the video is; none for a file made before it
    # was kept.
    timeline_snapshot: Snapshot | None = field(default=None, compare=False)

    @property
    def key(self) -> tuple:
        # A video is known by its timeline: another row of the same video makes no other video (stage 15, И-2); a video
        # downloaded from a row is on the row's own timeline, so for a row of a site it is the row.
        return ("cut", self.cut_of, self.timeline) if self.cut_of is not None else ("row", self.timeline)


@dataclass(frozen=True)
class Track:
    """A voice-over in a container: the row it came from, its snapshot, the last number of that row and the timeline it
    is aligned to."""

    dub: RowId
    snapshot: Snapshot
    episode: Slot
    aligned_to: RowId
    # The key of the track in its file's tags when it is not the key of its row (a file made before the planner keys its
    # tracks by the voice-over); not a part of what the track is.
    tag: str = field(default="", compare=False)
    # A file of its own beside the container (relative to the library): a track the service placed before the planner and
    # never packed. The container is not built yet: the next build takes the sound from that file and removes it.
    file: str = field(default="", compare=False)

    @property
    def group(self) -> str | None:
        return self.snapshot.group


@dataclass(frozen=True)
class Subtitle:
    """A subtitles track in a container (DESIGN.md 7.1.1): the row it came from, its name, the player of that row, the
    timeline it is in step with, its language and its type (full, signs) as the row gives them; and how it lies in the
    container (core.subtitle_queue.flags: the first full track default, the first of signs forced)."""

    row: RowId
    name: str
    player: str
    timeline: RowId
    language: str = ""
    subtitle_type: str = ""
    default: bool = False
    forced: bool = False

    @property
    def variant(self) -> tuple[str, int, str]:
        """(name, source, player): what the levers of the subtitles name."""
        return self.name, self.row.source_id, self.player


@dataclass(frozen=True)
class Container:
    """A file of the library: one video and its tracks, under a name; `off` hides it from the media server. `own_at` is the
    place of the video's own sound among the sounds of the file (0: first, the default one). `titles` are the user's
    names its sounds were written under (voice-over group, title); a sound not named there bears the name of its group.
    `languages` are the languages its sounds were written with (voice-over group, ISO 639-2): the language the user gave
    each group; a sound not named there was written with none."""

    name: str
    video: Video
    tracks: tuple[Track, ...] = ()
    off: bool = False
    own_at: int = 0
    titles: tuple[tuple[str, str], ...] = ()
    languages: tuple[tuple[str, str], ...] = ()
    # Its subtitles tracks, in the order of the file (DESIGN.md 7.1.1).
    subtitles: tuple[Subtitle, ...] = ()


# What the user chose, kept by the core (native/src/plan_inputs.rs): made of the levers of the layout
# (`core/plan/inputs.choices_of`) or of its fields given by name; `replaced(**changes)` is it with some of them changed.
# `players` and `sources` are the orders of priority, best first, `player_groups` the players the user called the same;
# `groups` the order of the tracks of a container, season by season. The levers of every episode as they come out of its
# levels (episode, compilation, season, series): `reference` puts variants (voice-over, source id, player) first in that
# order, `reference_deny` leaves variants out of the queue of the reference; `voice_order` puts variants (source id,
# player) of a voice-over first, `voice_deny` leaves variants (voice-over, source id, player) out, `voice_off` takes
# voice-overs off. `member` is the user's word on the compilation of an episode: a compilation row, or None for none (no
# entry: the rule). `delete` holds the commands «Удалить»: ("video", container name) or ("track", container name, dub row).
# `titles` the user's name of the track of a voice-over group in every container (its title there), `languages` the
# language the user gave each group (ISO 639-2). `own_reference` the episodes whose levers of the reference are their own.
# `markup`/`markup_deny` the queue of the markup reference of every episode that has one of its own on a level (stage 14):
# with none the episode follows its reference; with one, the first variant of the queue the episode has is the timeline.
# `same`/`markup_same` the variants the user declared equal (Р-99): a group is one place in its queue. `subtitle_order`/
# `subtitle_deny` the subtitles of every episode (DESIGN.md 7.1.1): the variants (name, source id, player) put first, and
# those left out.
Choices = native.Choices


@dataclass(frozen=True)
class Inputs:
    """Everything the planner reads. `failed` are the attempts that failed: ("download", row), ("align", voice-over row,
    timeline of the video) and ("cut", compilation row, timeline of the single video). The planner passes over them and
    takes the next way; an alignment that failed holds only for that video — another video is tried afresh. They are
    tried again when the user resets them."""

    online: native.OnlineRows
    containers: tuple[Container, ...]
    choices: Choices
    failed: frozenset[tuple] = frozenset()
    # The rows of subtitles (DESIGN.md 7.1.1). Rows of subtitles given among `online` are moved here: whatever reads
    # `online` reads rows of sound only, and no row of subtitles is ever taken for a voice-over or a video.
    subtitles: native.OnlineRows = ()

    def __post_init__(self) -> None:
        # The rows are kept by the core (native.OnlineRows), whatever sequence of rows they are given as.
        online, subtitles = native.OnlineRows(self.online).split(self.subtitles)
        object.__setattr__(self, "online", online)
        object.__setattr__(self, "subtitles", subtitles)
