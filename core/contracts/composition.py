"""The layout of a series: the rows of all its sources, each with the numbers the constructor gave it."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from .output_settings import EpisodeSettings, LeverSpec, LevelSettings
from .release_table import RowState, TableState
from .rules import RuleResult


class Exclusion(StrEnum):
    """Why a row is out of the layout: the constructor excluded it, it gave the row no episode number, or the source's
    own choice left it out (a switched-off player, another season of a catalog)."""

    RULE = "rule"
    NO_NUMBER = "no_number"
    SOURCE = "source"


# The exclusions that are a choice of the user: the video is taken out on purpose. A row with no number is not one: a
# site that changed its wording is no choice, and like a row a read did not bring it proves nothing.
CHOSEN_EXCLUSIONS = frozenset({Exclusion.RULE, Exclusion.SOURCE})


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
    # Which video the row is, as its host numbers it (release players' `video_of` of the link): two rows of one player with
    # one video are the same video whatever page shows them. Empty when the link names no video of its host's own.
    video: str = ""

    @property
    def is_compilation(self) -> bool:
        return (self.episode_start is not None and self.episode_end is not None
                and self.episode_end > self.episode_start)

    @property
    def height(self) -> int | None:
        return max(self.heights) if self.heights else None

    @property
    def left_out(self) -> bool:
        """The configuration took this video out on purpose: what was made from it is withdrawn, not kept."""
        return self.excluded and self.exclusion in CHOSEN_EXCLUSIONS

    @property
    def decided(self) -> bool:
        """The layout has a word on this video: it numbers it, or the configuration took it out. A row with no number
        has none, like a row a read did not bring: what was made from it stands."""
        return not self.excluded or self.left_out


class SourceStatus(StrEnum):
    WAITING = "waiting"
    NO_PROFILE = "no_profile"
    READY = "ready"
    FAILED = "failed"


class PlayerSummary(BaseModel):
    """What a window may show of a player before any rule has read its rows: how many rows it has and how a catalog
    splits them. Episodes, voice-overs and resolutions of a player are the constructor's answer: the rows of the layout."""

    player: str
    player_label: str = ""
    rows: int = 0
    groups: int = 0
    # The groups of the player as it names them, in the order of the table (a season of a catalog is one).
    group_names: list[str] = Field(default_factory=list)


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
    # Where the rows of its table come from (release_table.place_of); empty before a table was read.
    place: list[str] = Field(default_factory=list)


class CompositionGetRequest(BaseModel):
    """Excluded rows are for the window that counts what a source gave; no consumer of the layout asks for them."""

    series_id: int
    include_excluded: bool = False
    # Only the summaries of the sources: no rows, levels or settings (a window that lists the sources needs no more).
    sources_only: bool = False


class VoiceSpan(BaseModel):
    """A voice-over group of a season and the episodes it has, as the window words them (`1–50, 52`)."""

    label: str
    episodes: str


class SeasonVoiceOrder(BaseModel):
    """The order of the voice-overs of a season in the container of every episode: the first is the default track."""

    season: int
    voices: list[VoiceSpan] = Field(default_factory=list)


class VoiceOrderSet(BaseModel):
    """The command `composition.voice_order.set`: the user's order of the voice-overs a season has now."""

    series_id: int
    season: int
    labels: list[str]


class CompositionReply(BaseModel):
    series_id: int
    rev: int
    rows: list[CompositionRow] = Field(default_factory=list)
    sources: list[SourceSummary] = Field(default_factory=list)
    levels: list[LevelSettings] = Field(default_factory=list)
    settings: list[EpisodeSettings] = Field(default_factory=list)
    # The levers there are, as the registry has them: a window draws the controls of the output from them.
    levers: list[LeverSpec] = Field(default_factory=list)
    # What may be done with an object in each of its states (core/contracts/availability.py): a window draws a control only
    # where this allows it.
    availability: dict[str, list[str]] = Field(default_factory=dict)
    # The order of the voice-overs of each season (stage 11): the order of the tracks in the container.
    voice_orders: list[SeasonVoiceOrder] = Field(default_factory=list)


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
    # The resolutions the page gives the row, when the row can be played; empty for a line a window holds itself.
    heights: list[int] = Field(default_factory=list)


class CompositionPreviewRequest(BaseModel):
    """What a window shows of an address before it belongs to a series. `refresh` False reads nothing.
    With `lines` (typed by the user, or the file names of a release) no page is read: the profile is applied to
    those lines as they are, and the address is not needed."""

    url: str = ""
    profile_id: int | None = None
    series_season: str | int | None = None
    refresh: bool = True
    lines: list[str] | None = None
    # The choices a source of a series keeps; they decide which rows of the page are in play, so the preview takes them
    # to make the rows the series would make.
    players_off: list[str] = Field(default_factory=list)
    vibix_season: int | None = None

    @model_validator(mode="after")
    def _names_a_page_or_lines(self) -> "CompositionPreviewRequest":
        if self.lines is None and not self.url:
            raise ValueError("an address or lines are needed")
        return self


class CompositionPreviewReply(BaseModel):
    """`state` is the state of the table of the page; `summary` and `lines` come from the last complete table."""

    url: str
    state: TableState
    reason: str | None = None
    # The names of the release as the site writes them (the series gets its own from them).
    names: list[str] = Field(default_factory=list)
    summary: SourceSummary | None = None
    lines: list[PreviewLine] = Field(default_factory=list)
    # The rows of the layout this address would make with the profile, without the rows the constructor left out: the
    # same rows a series keeps (source_id 0), so a window draws the episodes and voice-overs of a draft from them too.
    rows: list[CompositionRow] = Field(default_factory=list)
