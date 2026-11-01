"""The layout of a series: the rows of all its sources, each with the numbers the constructor gave it."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from .output_settings import EpisodeSettings, LevelSettings
from .release_table import RowState, TableState
from .rules import RuleResult


class Exclusion(StrEnum):
    """Why a row is out of the layout: the constructor excluded it, or it gave the row no episode number."""

    RULE = "rule"
    NO_NUMBER = "no_number"


class CompositionRow(BaseModel):
    """A table row of a source of the series with the answer of its profile and its voice-over group.

    A single episode has `episode_end` equal to `episode_start`; `episode_end` greater than `episode_start` is a
    compilation. A source without a profile has no rows."""

    series_id: int
    source_id: int
    row_key: str
    excluded: bool = False
    exclusion: Exclusion | None = None
    season: int | None = None
    episode_start: int | None = None
    episode_end: int | None = None
    part: int | None = None
    voice_group: str | None = None
    voice_label: str = ""
    # The name of the voice-over as it came (the constructor's name wins over the player's), before any grouping.
    voice_name: str = ""
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


class VoiceSummary(BaseModel):
    """A voice-over of a player as the table names it and the user group it belongs to (None: no group)."""

    name: str = ""
    group: str | None = None
    rows: int = 0


class PlayerSummary(BaseModel):
    """What a window may show before a profile is assigned: counts, resolutions and voices, never numbers."""

    player: str
    player_label: str = ""
    rows: int = 0
    groups: int = 0
    # The groups of the player as it names them, in the order of the table (a season of a catalog is one).
    group_names: list[str] = Field(default_factory=list)
    heights: list[int] = Field(default_factory=list)
    voices: list[VoiceSummary] = Field(default_factory=list)


class SourceSummary(BaseModel):
    """`source_id` is 0 for an address that is not yet a source of a series (the add window, the test of rules)."""

    source_id: int = 0
    url: str
    # The address the events about the table carry: the one a window waits for.
    canonical_url: str | None = None
    status: SourceStatus
    reason: str | None = None
    read_at: str | None = None
    players: list[PlayerSummary] = Field(default_factory=list)


class CompositionGetRequest(BaseModel):
    """Excluded rows are for the window that counts what a source gave; no consumer of the layout asks for them."""

    series_id: int
    include_excluded: bool = False
    # Only the summaries of the sources: no rows, levels or settings (a window that lists the sources needs no more).
    sources_only: bool = False


class CompositionReply(BaseModel):
    series_id: int
    rev: int
    rows: list[CompositionRow] = Field(default_factory=list)
    sources: list[SourceSummary] = Field(default_factory=list)
    levels: list[LevelSettings] = Field(default_factory=list)
    settings: list[EpisodeSettings] = Field(default_factory=list)


class EpisodeRef(BaseModel):
    season: int
    episode: int


class CompositionChanged(BaseModel):
    """`episodes` are the episodes the change touched; empty means the whole series.

    `summary_only` says that no row and no lever changed, only the summaries of the sources (state, reason, time
    of reading): a consumer that works from rows and levers has nothing to do, a window refreshes."""

    series_id: int
    rev: int
    episodes: list[EpisodeRef] = Field(default_factory=list)
    summary_only: bool = False


class CompositionFailed(BaseModel):
    series_id: int
    reason: str


class PreviewLine(BaseModel):
    """A row of a page as the constructor gets it (`line` is `line_of`), with its answer when a profile is given."""

    key: str
    line: str
    result: RuleResult | None = None


class CompositionPreviewRequest(BaseModel):
    """What a window shows of an address before it belongs to a series. `refresh` False reads nothing."""

    url: str
    options: dict[str, str] = Field(default_factory=dict)
    profile_id: int | None = None
    series_season: str | int | None = None
    refresh: bool = True


class CompositionPreviewReply(BaseModel):
    """`state` is the state of the table of the page; `summary` and `lines` come from the last complete table."""

    url: str
    state: TableState
    reason: str | None = None
    # The names of the release as the site writes them (the series gets its own from them).
    names: list[str] = Field(default_factory=list)
    summary: SourceSummary | None = None
    lines: list[PreviewLine] = Field(default_factory=list)
