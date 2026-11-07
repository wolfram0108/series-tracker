"""The table of one site page: what the site and its players give, in one form for every consumer.

The table carries no episode or season numbers of its own making: the text of a row goes to the rules constructor,
which is the only source of numbers. `line_of` is the one place that turns a row into the constructor's input.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

LINE_SEPARATOR = " | "


class RowState(StrEnum):
    """`not_obtained` is not `absent`: the answer stayed unsure to the end, the player may still have the video."""

    PLAYABLE = "playable"
    ABSENT = "absent"
    NOT_OBTAINED = "not_obtained"


class RowVoice(BaseModel):
    """The voice-over as the player or the site words it; `kind` is the type the player names (voice, subtitles, ...)."""

    name: str = ""
    kind: str = ""


class TableRow(BaseModel):
    """One element of one player's list, as the site gave it."""

    model_config = ConfigDict(extra="forbid")

    key: str
    player: str
    player_label: str = ""
    group: str = ""
    text: str = ""
    voice: RowVoice = Field(default_factory=RowVoice)
    heights: list[int] = Field(default_factory=list)
    state: RowState
    reason: str | None = None
    duration_s: float | None = None
    published: str | None = None
    # The stable address of the candidate (a page of the video); a signed stream address never goes here: it changes
    # with every reading and would make every reading a change of the layout. The stream is resolved before download.
    link: str = ""
    # Which video the row is, as its host numbers it (release players' `video_of` of the link): two rows of one player with
    # one video are the same video whatever page shows them. Empty when the link names no video of its host's own.
    video: str = ""
    # The site's own numbers and marks, for a human and for debugging; no decision reads them.
    site_fields: dict[str, Any] = Field(default_factory=dict)
    # The part of the page the row was read from (the catalog of a player, the page of an episode); empty for the rows the
    # page lists itself. A part that cannot be read keeps the rows it gave before (`ReleaseTable.unread`).
    part: str = ""

    @property
    def height(self) -> int | None:
        return max(self.heights) if self.heights else None


class UnreadPart(BaseModel):
    """A part of a page the read could not get: its rows are the ones it gave the last read that got it, if any.
    `names` are what the site calls its content (the voice-overs of a catalog, the title of an episode)."""

    model_config = ConfigDict(extra="forbid")

    part: str
    player_label: str = ""
    names: list[str] = Field(default_factory=list)
    reason: str


class ReleaseTable(BaseModel):
    """The head and the rows of one page; published to consumers only when `complete`. A page some part of which could
    not be read is still complete: the part is named in `unread` and keeps its rows of the read before."""

    model_config = ConfigDict(extra="forbid")

    url: str
    site: str
    names: list[str] = Field(default_factory=list)
    site_voice: str = ""
    # The channel the page belongs to (the community of a VK channel), so that rules tell apart two channels of one site.
    channel: str = ""
    read_at: str | None = None
    complete: bool = False
    rows: list[TableRow] = Field(default_factory=list)
    unread: list[UnreadPart] = Field(default_factory=list)

    def row(self, key: str) -> TableRow | None:
        return next((row for row in self.rows if row.key == key), None)


def pid_of(table: ReleaseTable) -> str:
    """The host of the page the table was read from (`PID` of the line): it tells which site a row comes from."""
    return (urlparse(table.url).hostname or "").lower()


def place_of(table: ReleaseTable) -> tuple[str, ...]:
    """Where the rows of a table come from, as the line names it: the site, then the channel where the site has them
    (two channels of VK are one site). The constructor and the names of the files of voice-overs read it alike."""
    return tuple(part for part in (pid_of(table), table.channel) if part)


def _resolution_of(table: ReleaseTable, row: TableRow) -> str:
    height = row.height if row.state == RowState.PLAYABLE else None
    return f"{height}p" if height else ""


# The fields of the line the constructor gets, in their order: (code, value of a row). A field with a code reads
# `CODE:value`, so a rule can tell it from the text; the name of the release and the text of the row go without one.
# Only what a rule needs is here: another column of the table joins the line by one more entry. A field with no value is
# left out together with its separator.
LINE_FIELDS: tuple[tuple[str, Any], ...] = (
    ("PID", lambda table, row: pid_of(table)),
    ("", lambda table, row: table.channel),
    ("P", lambda table, row: row.player_label or row.player),
    ("", lambda table, row: table.names[0].strip() if table.names else ""),
    ("", lambda table, row: row.text),
    ("G", lambda table, row: row.group),
    ("V", lambda table, row: row.voice.name),
    ("VT", lambda table, row: row.voice.kind),
    ("R", _resolution_of),
)


def line_of(table: ReleaseTable, row: TableRow) -> str:
    """The text the constructor gets for a row: the fields of `LINE_FIELDS` joined by the separator."""
    values = [(code, value(table, row)) for code, value in LINE_FIELDS]
    return LINE_SEPARATOR.join(f"{code}:{text}" if code else text for code, text in values if text)


class TableState(StrEnum):
    """`absent`: no complete table exists and the request asked for no read."""

    READY = "ready"
    READING = "reading"
    FAILED = "failed"
    ABSENT = "absent"


class TableGetRequest(BaseModel):
    """`refresh` False reads nothing: the last complete table is returned as it is, old or not, so that recounting
    a layout never costs a read of the site."""

    url: str
    refresh: bool = True


class TableGetReply(BaseModel):
    """`url` is the canonical address of the page: the one the events `ready` and `failed` carry.

    `table` is the last complete table: present with `reading` and `failed` when an earlier read finished."""

    url: str
    state: TableState
    table: ReleaseTable | None = None
    reason: str | None = None


class SiteResolveRequest(BaseModel):
    url: str


class SiteResolveReply(BaseModel):
    """Which site reads an address and the canonical form of it; both None when no site does."""

    site: str | None = None
    canonical_url: str | None = None
    # What kind of source a page of this site makes (`vk_video` or `web_video`); the window does not guess it.
    source_type: str | None = None


class TableProgress(BaseModel):
    """How far a read of a page is; `rate` is the steps of the phase per second since the phase began (None before the
    first step)."""

    url: str
    phase: str
    current: int
    total: int
    rate: float | None = None


class SeriesReadProgress(BaseModel):
    """The event `scan.series.progress`: how far the read of a page is that the scan of a series waits for. `active`
    false says the wait for that source is over (read, failed or given up); the other fields are then those it last had."""

    series_id: int
    source_id: int
    url: str
    phase: str = ""
    current: int = 0
    total: int = 0
    rate: float | None = None
    active: bool = True


class TableReady(BaseModel):
    url: str
    read_at: str


class TableFailed(BaseModel):
    url: str
    reason: str
