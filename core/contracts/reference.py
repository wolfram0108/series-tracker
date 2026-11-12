"""The reference of an episode: the one single-episode video the alignment is made against, and why it is that one."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

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


class CutPicture(BaseModel):
    """The row whose picture a cut video of an episode is: a compilation, or a single row higher than the markup
    reference (stage 14)."""

    source_id: int
    player: str
    height: int | None = None
    # The voice-over of the picture's row: the own sound of the cut video.
    voice_group: str | None = None


class Reference(BaseModel):
    """`reason` is a code of core/reasons: for `other_player` it names the player taken and the one missing.

    `compilation` is the compilation the episode stands in (stage 12): its video is cut out of it on the timeline of the
    single video this reference names (REF 2). `picture` is the row the cut video takes its picture from, whatever it is;
    `markup`: the single video is the markup reference of the series (stage 14)."""

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
    picture: CutPicture | None = None
    markup: bool = False


class OutputSwitch(BaseModel):
    """The command `scan.output.switch`: episodes of a season (or, without `episodes`, the whole season) are switched off
    or on, one episode or a thousand at once: one change of the memory of the series. Switching a season on switches on
    everything of it, the episodes switched off one by one included."""

    series_id: int
    season: int
    episodes: list[int] | None = Field(default=None, min_length=1)
    off: bool


class ReferenceChanged(Reference):
    """The event `scan.reference.changed`: the reference as it stands now, whole, so that a window needs no second
    request to show it."""

    series_id: int
