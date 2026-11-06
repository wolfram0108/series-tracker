"""The reference of an episode: the one single-episode video the alignment is made against, and why it is that one."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from .compilation import CompilationRef


class ReferenceMark(StrEnum):
    """`off`: the user switched the episode off (its season, its number, or its line of the plan); it has no
    reference and plans no voice-over, and the window shows it as switched off, not as an episode with no reference."""

    BY_LEVEL = "by_level"
    OWN = "own"
    OTHER_PLAYER = "other_player"
    NONE = "none"
    UNFIT = "unfit"
    OFF = "off"


class Reference(BaseModel):
    """`reason` is a code of core/reasons: for `other_player` it names the player taken and the one missing.

    `compilation` is the compilation the episode stands in (stage 12): its video is cut out of it on the timeline of the
    single video this reference names (REF 2)."""

    season: int
    episode: int
    part: int | None = None
    mark: ReferenceMark
    source_id: int | None = None
    row_key: str | None = None
    player: str | None = None
    voice_group: str | None = None
    height: int | None = None
    reason: str | None = None
    compilation: CompilationRef | None = None


class OutputSwitch(BaseModel):
    """The command `scan.output.switch`: an episode (or, without `episode`, a whole season) is switched off or on.
    Switching a season on switches on everything of it, the episodes switched off one by one included."""

    series_id: int
    season: int
    episode: int | None = None
    off: bool


class ReferenceChanged(Reference):
    """The event `scan.reference.changed`: the reference as it stands now, whole, so that a window needs no second
    request to show it."""

    series_id: int
