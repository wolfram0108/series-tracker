"""The reference of an episode: the one single-episode video the alignment is made against, and why it is that one."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ReferenceMark(StrEnum):
    BY_LEVEL = "by_level"
    OWN = "own"
    OTHER_PLAYER = "other_player"
    NONE = "none"
    UNFIT = "unfit"


class Reference(BaseModel):
    """`reason` is a code of core/reasons: for `other_player` it names the player taken and the one missing."""

    season: int
    episode: int
    mark: ReferenceMark
    source_id: int | None = None
    row_key: str | None = None
    player: str | None = None
    voice_group: str | None = None
    height: int | None = None
    reason: str | None = None


class ReferenceChanged(BaseModel):
    series_id: int
    season: int
    episode: int
    mark: ReferenceMark
