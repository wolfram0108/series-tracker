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
    READY = "ready"
    READING = "reading"
    FAILED = "failed"


class TableGetRequest(BaseModel):
    url: str


class TableGetReply(BaseModel):
    """`table` is the last complete table: present with `reading` and `failed` when an earlier read finished."""

    state: TableState
    table: ReleaseTable | None = None
    reason: str | None = None


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
