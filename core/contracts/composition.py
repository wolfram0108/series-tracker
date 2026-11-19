"""The layout of a series: the rows of all its sources, each with the numbers the constructor gave it."""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from .compilation import CompilationView
from .output_settings import EpisodeSettings, LeverSpec, LevelSettings
from .release_table import RowState, RowTrack, SubtitleType, TableState
from .rules import RuleResult


class Exclusion(StrEnum):
    """Why a row is out of the layout: the constructor excluded it, it gave the row no episode number, the source's own
    choice left it out (a player switched off for the source, another season of a catalog), or its player is switched
    off in the settings for every source."""

    RULE = "rule"
    NO_NUMBER = "no_number"
    SOURCE = "source"
    SETTINGS = "settings"


# The exclusions that are a choice of the user: the video is taken out on purpose. A row with no number is not one: a
# site that changed its wording is no choice, and like a row a read did not bring it proves nothing.
CHOSEN_EXCLUSIONS = frozenset({Exclusion.RULE, Exclusion.SOURCE, Exclusion.SETTINGS})
# The exclusions made before the constructor: the row is asked nothing and names no voice-over group.
LEFT_BEFORE_RULES = frozenset({Exclusion.SOURCE, Exclusion.SETTINGS})


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
    # The key of the video of the row as a timeline (TableRow.timeline); empty: the row itself.
    timeline: str = ""
    # What the row carries (TableRow.track): a row of subtitles is no voice-over and no variant of the reference.
    track: RowTrack = RowTrack.SOUND
    # The language of the track and the type of subtitles (TableRow.language, .subtitle_type).
    language: str = ""
    subtitle_type: SubtitleType = ""
    # The source did not give this episode of this player in its last read (a part of the page that did not answer, or a
    # read that failed): the row stands from an earlier read, and every window shows it as not current.
    stale: bool = False

    @property
    def grouped(self) -> bool:
        """The voice-over of the row is one of the user's groups."""
        return self.voice_group is not None

    @computed_field
    @property
    def in_output(self) -> bool:
        """The row is a row of the output (the window «Выход», its plan and the planner): the rules gave it its numbers and
        the voice-over groups took it. The one way into the output: table, rules, groups (ЭТАП_10:107); a row of an
        unknown voice-over is in none of it — no reference, no markup reference, no voice-over — until its group takes
        it. A row of subtitles needs no group: it is no voice-over (DESIGN.md 7.1.1)."""
        return (not self.excluded and self.season is not None and self.episode_start is not None
                and (self.voice_group is not None or self.track == RowTrack.SUBTITLES))

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
    # The name its site gives the source for the head of a window (ReleaseTable.title); empty when it gives none.
    title: str = ""


class EpisodeRef(BaseModel):
    season: int
    episode: int


# What of the layout a change touched, and so what a window that holds the layout reads again (the projection of the
# layout in the window, CQRS): `whole` — everything; `levers` — the levers of the episodes named (all with none), the levels,
# the compilations, the orders and names of the voice-overs and the summaries of the sources, the rows being as they were;
# `sources` — the summaries of the sources alone. Each part holds the narrower ones, so news of both are one read.
LayoutPart = Literal["whole", "levers", "sources"]

# The fields of the layout each part holds besides `series_id`, `rev` and `part` — the one table of it: the core makes a part
# of these fields, the window puts into what it holds whatever fields a part brings. The whole holds every field; a field
# that a change of a lever, an order or a summary can move is in `levers`, the rows are in the whole only.
LAYOUT_PARTS: dict[str, frozenset[str]] = {
    "sources": frozenset({"sources"}),
    "levers": frozenset({"sources", "levels", "settings", "compilations", "voice_orders", "voice_titles"}),
}


class CompositionGetRequest(BaseModel):
    """Excluded rows are for the window that counts what a source gave; no consumer of the layout asks for them."""

    series_id: int
    include_excluded: bool = False
    # Only the summaries of the sources: no rows, levels or settings (a window that lists the sources needs no more).
    sources_only: bool = False
    # The part of the layout asked (`LayoutPart`); `episodes` names the episodes of the part `levers` (none: all of them).
    part: LayoutPart = "whole"
    episodes: list[EpisodeRef] = Field(default_factory=list)


# The longest name of a track the user may give: a title a player shows on one line.
TITLE_MAX = 100


class VoiceSpan(BaseModel):
    """A voice-over group of a season and the episodes it has, as the window words them (`1–50, 52`)."""

    label: str
    episodes: str


class SeasonVoiceOrder(BaseModel):
    """The order of the voice-overs of a season in the container of every episode: the first is the default track."""

    season: int
    voices: list[VoiceSpan] = Field(default_factory=list)


class VoiceOrderSet(BaseModel):
    """The command `composition.voice_order.set`: the user's order of the voice-overs the window shows of a season (its
    rows and its files); the others keep their places."""

    series_id: int
    season: int
    labels: list[str]

    @field_validator("labels")
    @classmethod
    def _distinct(cls, labels: list[str]) -> list[str]:
        if len(set(labels)) != len(labels):
            raise ValueError("a voice-over is named twice")
        return labels


class VoiceTitleSet(BaseModel):
    """The command `composition.voice_title.set`: the user's name of the track of a voice-over group in every container of
    the series (its title there; the group stays what the track is known by). An empty title, or the name of the group,
    takes the user's name back."""

    series_id: int
    label: str
    title: str

    @field_validator("title")
    @classmethod
    def _plain(cls, title: str) -> str:
        title = title.strip()
        if len(title) > TITLE_MAX or any(ord(char) < 32 for char in title):
            raise ValueError("a title is one line of at most %d characters" % TITLE_MAX)
        return title


class CompositionReply(BaseModel):
    series_id: int
    rev: int
    # The part of the layout the reply holds (`LayoutPart`): what is not in the part is left out, not empty.
    part: LayoutPart = "whole"
    rows: list[CompositionRow] = Field(default_factory=list)
    # The rows of subtitles (DESIGN.md 7.1.1), apart from the rows of sound: no voice-over, no variant of the reference.
    subtitles: list[CompositionRow] = Field(default_factory=list)
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
    # The user's names of the tracks of the voice-over groups in the containers (group -> title).
    voice_titles: dict[str, str] = Field(default_factory=dict)
    # The compilations of every season and the episodes each holds (stage 12): the blocks of the view «Компиляции».
    compilations: list[CompilationView] = Field(default_factory=list)


class CompositionChanged(BaseModel):
    """`episodes` are the episodes the change touched; empty means the whole series.

    `summary_only` says that no row and no lever changed, only the summaries of the sources (state, reason, time
    of reading): a consumer that works from rows and levers has nothing to do, a window refreshes."""

    series_id: int
    rev: int
    episodes: list[EpisodeRef] = Field(default_factory=list)
    summary_only: bool = False
    # What a window that holds the layout reads again for this change (`LayoutPart`).
    part: LayoutPart = "whole"


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
