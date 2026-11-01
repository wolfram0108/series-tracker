"""The layout of a series: the rows of all its sources, each with the numbers the constructor gave it."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from .output_settings import EpisodeSettings
from .release_table import RowState


class CompositionRow(BaseModel):
    """A table row of a source of the series with the answer of its profile and its voice-over group.

    `episode_end` greater than `episode_start` is a compilation. A source without a profile has no rows."""

    series_id: int
    source_id: int
    row_key: str
    excluded: bool = False
    # Why the row is excluded: `rule` (the constructor excluded it) or `no_number` (it gave no episode number).
    exclusion: str | None = None
    season: int | None = None
    episode_start: int | None = None
    episode_end: int | None = None
    part: int | None = None
    voice_group: str | None = None
    voice_label: str = ""
    # The number of an unknown voice-over, for the interface to word it by its own key.
    voice_unknown: int | None = None
    player: str
    player_label: str = ""
    group: str = ""
    text: str = ""
    heights: list[int] = Field(default_factory=list)
    state: RowState
    published: str | None = None
    link: str = ""

    @property
    def is_compilation(self) -> bool:
        return (self.episode_start is not None and self.episode_end is not None
                and self.episode_end > self.episode_start)

    @property
    def height(self) -> int | None:
        return max(self.heights) if self.heights else None


class SourceStatus(StrEnum):
    WAITING = "waiting"
    NO_PROFILE = "no_profile"
    READY = "ready"
    FAILED = "failed"


class PlayerSummary(BaseModel):
    """What a window may show before a profile is assigned: counts and resolutions, never numbers."""

    player: str
    player_label: str = ""
    rows: int = 0
    groups: int = 0
    heights: list[int] = Field(default_factory=list)


class SourceSummary(BaseModel):
    source_id: int
    url: str
    status: SourceStatus
    reason: str | None = None
    read_at: str | None = None
    players: list[PlayerSummary] = Field(default_factory=list)


class CompositionGetRequest(BaseModel):
    """Excluded rows are for the window that counts what a source gave; no consumer of the layout asks for them."""

    series_id: int
    include_excluded: bool = False


class CompositionReply(BaseModel):
    series_id: int
    rev: int
    rows: list[CompositionRow] = Field(default_factory=list)
    sources: list[SourceSummary] = Field(default_factory=list)
    settings: list[EpisodeSettings] = Field(default_factory=list)


class EpisodeRef(BaseModel):
    season: int
    episode: int


class CompositionChanged(BaseModel):
    """`episodes` are the episodes the change touched; empty means the whole series."""

    series_id: int
    rev: int
    episodes: list[EpisodeRef] = Field(default_factory=list)


class CompositionFailed(BaseModel):
    series_id: int
    reason: str
