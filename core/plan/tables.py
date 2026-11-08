"""The tables the planner of a series reads (stage 12, the table of an episode): what the sources give now, what the
library holds and how it was obtained, and what the user chose. Nothing else is an input of the planner.

An online row is a row of a source that the rules of the constructor gave to an episode at the last complete and
successful read. The asset is what is on disk: containers, each with one video and the tracks aligned to it. A row of an
episode is online, offline (the asset holds a file of it, the read did not give it) or forgotten (neither)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from core.voice_names import NO_GROUPS, Groups

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


@dataclass(frozen=True)
class OnlineRow:
    """A row the rules gave to an episode (or to a range of them: a compilation). `group` is the voice-over group; None is
    an unknown voice-over, which no new work takes (Р-77)."""

    id: RowId
    season: int
    start: int
    end: int
    player: str
    group: str | None
    height: int
    source: str = ""
    link: str = ""
    label: str = ""  # the name of the voice-over as the layout calls it
    position: int = 0  # the place of the row in the layout: the last tie-break of the service's rule
    part: int | None = None  # the part of the episode the row is (an episode in parts is several videos)

    @property
    def source_id(self) -> int:
        return self.id.source_id

    @property
    def variant(self) -> tuple[str, int, str]:
        """(voice-over, source, player): what the user's levers name."""
        return self.label or self.group or "", self.id.source_id, self.player

    @property
    def compilation(self) -> bool:
        return self.end > self.start

    @property
    def slot(self) -> Slot:
        return (self.season, self.start) if self.part is None else (self.season, self.start, self.part)

    def snapshot(self) -> Snapshot:
        return Snapshot(self.source, self.player, self.group, self.height, self.link, self.label)


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

    @property
    def key(self) -> tuple:
        return ("cut", self.cut_of, self.timeline) if self.cut_of is not None else ("row", self.id)


@dataclass(frozen=True)
class Track:
    """A voice-over in a container: the row it came from, its snapshot, the last number of that row and the timeline it
    is aligned to."""

    dub: RowId
    snapshot: Snapshot
    episode: Slot
    aligned_to: RowId

    @property
    def group(self) -> str | None:
        return self.snapshot.group


@dataclass(frozen=True)
class Container:
    """A file of the library: one video and its tracks, under a name; `off` hides it from the media server."""

    name: str
    video: Video
    tracks: tuple[Track, ...] = ()
    off: bool = False


@dataclass(frozen=True)
class Choices:
    """What the user chose. `players` and `sources` are the orders of priority, best first, `player_groups` the players
    the user called the same; `groups` the order of the tracks of a container. The levers of every episode as they come
    out of its levels (episode, compilation, season, series): `reference` puts variants (voice-over, source id, player)
    first in that order, `reference_deny` leaves variants out of the queue of the reference; `voice_order` puts variants
    (source id, player) of a voice-over first, `voice_deny` leaves variants (voice-over, source id, player) out,
    `voice_off` takes voice-overs off. `member` is the user's word on the compilation of an episode: a compilation row,
    or None for none (no entry: the rule). `delete` holds the commands «Удалить»: ("video", container name) or
    ("track", container name, dub row)."""

    players: tuple[str, ...] = ()
    sources: tuple[int, ...] = ()
    groups: tuple[str, ...] = ()
    player_groups: Groups = NO_GROUPS
    off_episodes: frozenset[Slot] = frozenset()
    off_groups: frozenset[str] = frozenset()
    off_compilations: frozenset[RowId] = frozenset()
    reference: Mapping[Slot, tuple[tuple[str, int, str], ...]] = field(default_factory=dict)
    reference_deny: Mapping[Slot, frozenset[tuple[str, int, str]]] = field(default_factory=dict)
    voice_order: Mapping[tuple[Slot, str], tuple[tuple[int, str], ...]] = field(default_factory=dict)
    voice_deny: Mapping[Slot, frozenset[tuple[str, int, str]]] = field(default_factory=dict)
    voice_off: Mapping[Slot, frozenset[str]] = field(default_factory=dict)
    member: Mapping[Slot, RowId | None] = field(default_factory=dict)
    delete: frozenset[tuple] = frozenset()


@dataclass(frozen=True)
class Inputs:
    """Everything the planner reads. `failed` are the attempts that failed: ("download", row), ("align", voice-over row,
    timeline of the video) and ("cut", compilation row, timeline of the single video). The planner passes over them and
    takes the next way; an alignment that failed holds only for that video — another video is tried afresh. They are
    tried again when the user resets them."""

    online: tuple[OnlineRow, ...]
    containers: tuple[Container, ...]
    choices: Choices
    failed: frozenset[tuple] = frozenset()
