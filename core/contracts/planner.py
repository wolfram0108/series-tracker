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
    file it lies in, aligned to the new video), `timeline` (the sound of the single video a cut video is laid onto: taken
    from its download, in step with no alignment). `own` is the own sound of a video the episode no longer has."""

    voice: str
    kind: Literal["reuse", "download", "realign", "timeline"]
    row: PlanRowRef
    own: bool = False


class PlanEpisode(BaseModel):
    slot: list[int]
    name: str
    off: bool = False
    video: PlanGoalVideo
    tracks: list[PlanGoalTrack] = Field(default_factory=list)
    # The voice-over groups the user switched off for the episode: the plan takes no track of theirs.
    voices_off: list[str] = Field(default_factory=list)
    # The voice-over of the markup reference of the episode (stage 14): its sound is the last of the file, out of the
    # order of the season; none without one.
    markup: str | None = None


class PlanFailure(BaseModel):
    """An attempt that failed: a download of a row, an alignment of a voice-over to a video, a cut of an episode out of a
    compilation by its single video. The plan passes over it until the user resets the failures."""

    kind: Literal["download", "align", "cut"]
    row: PlanRowRef
    video: PlanRowRef | None = None
    # Where the attempt belongs, as the layout the view is counted by has its row: the episodes the row gives (a compilation:
    # every one it holds; [season, episode] or [season, episode, part]) and its voice-over group. Empty when the row is
    # offline: the failure is still counted, only not placed.
    episodes: list[list[int]] = Field(default_factory=list)
    voice: str | None = None
    # The series lacks nothing for it: every episode of its row has a file holding what the attempt was for (another
    # source gave it). Only a failure not covered makes the series an error.
    covered: bool = False


class PlanProblem(BaseModel):
    """A step of the last sync that failed for the moment (a crash of the service, a download cut short): no failure, the
    next sync tries again. `slot` is the episode (none for the download of a compilation), `voice` the voice-over of an
    alignment or of a download of a voice-over."""

    slot: list[int] | None = None
    kind: str
    voice: str | None = None
    reason: str = ""


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
    # The steps of the last sync that failed for the moment, with why (PlanProblem).
    problems: list[PlanProblem] = Field(default_factory=list)
    commands: list[PlanCommand] = Field(default_factory=list)


class PlanDelete(BaseModel):
    """The command «Удалить» of the window: a file of the asset, or a track in it."""

    series_id: int
    kind: Literal["video", "track"]
    name: str
    dub: PlanRowRef | None = None


class PlanForget(BaseModel):
    """The reset of the failed attempts of a series: of all of them, or of those that belong to a level of it — a season, an
    episode of it, a voice-over group, the cuts out of compilations. The attempts of the other levels stay: the plan goes
    on passing over them, so a reference that stands because another one failed does not go back to the one that failed."""

    series_id: int
    season: int | None = None
    episode: int | None = None
    voice: str | None = None
    kind: Literal["download", "align", "cut"] | None = None

    @property
    def whole(self) -> bool:
        return self.season is None and self.episode is None and self.voice is None and self.kind is None

    def holds(self, failure: dict) -> bool:
        """Whether a failure as the view places it (its `kind`, `episodes`, `voice`) belongs to the level. A failure the
        view cannot place (its row is offline) belongs to no level but the whole series."""
        if self.kind is not None and failure["kind"] != self.kind:
            return False
        if self.voice is not None and failure["voice"] != self.voice:
            return False
        if self.season is None and self.episode is None:
            return True
        return any((self.season is None or slot[0] == self.season) and (self.episode is None or slot[1] == self.episode)
                   for slot in failure["episodes"])
