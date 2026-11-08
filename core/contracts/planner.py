"""The plan of a video series as the window reads it (modules/planner, stage 12): the asset with the provenance of every
file, the goal of every episode as the last sync counted it, the attempts that failed and the commands «Удалить» that wait."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .reference import Reference


class PlanRowRef(BaseModel):
    """A row of a source table: the source, the key of the row and the video behind it as its host numbers it."""

    source_id: int
    row_key: str
    video: str = ""


class PlanSnapshot(BaseModel):
    """Where a file came from, written once when it was obtained: it stays when the source forgets the row."""

    source: str = ""
    player: str = ""
    group: str | None = None
    height: int = 0
    link: str = ""
    label: str = ""


class PlanAssetVideo(PlanSnapshot):
    row: PlanRowRef
    cut_of: PlanRowRef | None = None
    # Whether the row of the video was in the layout the last sync read: None before any sync of the planner.
    online: bool | None = None


class PlanAssetTrack(PlanSnapshot):
    row: PlanRowRef
    online: bool | None = None


class PlanAsset(BaseModel):
    """A file of the library: one video and the tracks aligned to it; `off` is hidden from the media server (`.off`)."""

    name: str
    off: bool = False
    episode: list[int]
    video: PlanAssetVideo
    tracks: list[PlanAssetTrack] = Field(default_factory=list)


class PlanGoalVideo(PlanSnapshot):
    """How the video of an episode is obtained: `asset` (the file holds it), `download`, `cut` (out of a compilation)."""

    kind: Literal["asset", "download", "cut"]
    row: PlanRowRef
    file: str | None = None
    # The compilation the video is cut out of (a cut, or a file that holds a cut video).
    cut_of: PlanRowRef | None = None


class PlanGoalTrack(PlanSnapshot):
    """How a voice-over of an episode is obtained: `reuse` (the file holds it aligned), `download`, `realign` (out of the
    file it lies in, aligned to the new video). `own` is the own sound of a video the episode no longer has."""

    voice: str
    kind: Literal["reuse", "download", "realign"]
    row: PlanRowRef
    own: bool = False


class PlanEpisode(BaseModel):
    slot: list[int]
    name: str
    off: bool = False
    video: PlanGoalVideo
    tracks: list[PlanGoalTrack] = Field(default_factory=list)


class PlanFailure(BaseModel):
    """An attempt that failed: a download of a row, an alignment of a voice-over to a video, a cut of an episode out of a
    compilation by its single video. The plan passes over it until the user resets the failures."""

    kind: Literal["download", "align", "cut"]
    row: PlanRowRef
    video: PlanRowRef | None = None


class PlanCommand(BaseModel):
    """A command «Удалить» that waits for the next sync: of a file (`video`) or of a track in it (`track`, `dub`)."""

    kind: Literal["video", "track"]
    name: str
    dub: PlanRowRef | None = None


class PlannerView(BaseModel):
    series_id: int
    running: bool = False
    # The reference of every episode and why it is that one, told by the plan.
    references: list[Reference] = Field(default_factory=list)
    assets: list[PlanAsset] = Field(default_factory=list)
    episodes: list[PlanEpisode] = Field(default_factory=list)
    failures: list[PlanFailure] = Field(default_factory=list)
    commands: list[PlanCommand] = Field(default_factory=list)


class PlanDelete(BaseModel):
    """The command «Удалить» of the window: a file of the asset, or a track in it."""

    series_id: int
    kind: Literal["video", "track"]
    name: str
    dub: PlanRowRef | None = None
