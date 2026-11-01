"""The table of one site page: what the site and its players give, in one form for every consumer.

The table carries no episode or season numbers of its own making: the text of a row goes to the rules constructor,
which is the only source of numbers. `line_of` is the one place that turns a row into the constructor's input.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

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
    # The site's own numbers and marks, for a human and for debugging; no decision reads them.
    site_fields: dict[str, Any] = Field(default_factory=dict)

    @property
    def height(self) -> int | None:
        return max(self.heights) if self.heights else None


class ReleaseTable(BaseModel):
    """The head and the rows of one page; published to consumers only when `complete`."""

    model_config = ConfigDict(extra="forbid")

    url: str
    site: str
    names: list[str] = Field(default_factory=list)
    site_voice: str = ""
    read_at: str | None = None
    complete: bool = False
    rows: list[TableRow] = Field(default_factory=list)

    def row(self, key: str) -> TableRow | None:
        return next((row for row in self.rows if row.key == key), None)


def line_of(table: ReleaseTable, row: TableRow) -> str:
    """The text the constructor gets for a row; a missing field is left out together with its separator."""
    height = row.height if row.state == RowState.PLAYABLE else None
    fields = [table.names[0] if table.names else "", row.player_label or row.player, row.voice.name, row.voice.kind,
              row.group, f"{height}p" if height else "", row.text]
    return LINE_SEPARATOR.join(field for field in fields if field)


class TableState(StrEnum):
    """`absent`: no complete table exists and the request asked for no read."""

    READY = "ready"
    READING = "reading"
    FAILED = "failed"
    ABSENT = "absent"


class TableGetRequest(BaseModel):
    """`options` are the choices of a source that change what is read (the search mode of a VK channel); a site
    ignores those it does not know. `refresh` False reads nothing: the last complete table is returned as it is,
    old or not, so that recounting a layout never costs a read of the site."""

    url: str
    options: dict[str, str] = Field(default_factory=dict)
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
    options: dict[str, str] = Field(default_factory=dict)


class SiteResolveReply(BaseModel):
    """Which site reads an address and the canonical form of it; both None when no site does."""

    site: str | None = None
    canonical_url: str | None = None


class TableProgress(BaseModel):
    url: str
    phase: str
    current: int
    total: int


class TableReady(BaseModel):
    url: str
    read_at: str


class TableFailed(BaseModel):
    url: str
    reason: str
