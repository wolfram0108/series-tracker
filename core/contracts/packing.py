"""The telemetry of the packing: one card to one container of an episode a sync builds, the same standard as a download
(core/contracts/downloads.py). The planner keeps it: the stages with their time, the progress of the build, the tracks of
the container as numbers in the order of the season (the marks of the window «Выход»), what changed against the file
before, the steps of putting it in place. A card is kept while the sync of its series runs; after it only a failed card
stays, until the user removes it or the next sync of the series begins.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .downloads import DownloadVariant

# The stages of a build, in order: waiting for the others, ffmpeg copying the streams into the container, the result read
# back, the file put in place by the journal of the sync. Between the build and the journal a container is `built`: the
# journal puts every file of the sync in place at once, after the rest of the work of the sync, so it waits, in no stage.
PackStage = Literal["queue", "build", "verify", "apply"]
PACK_STAGES: tuple[PackStage, ...] = ("queue", "build", "verify", "apply")
PackStatus = Literal["queued", "build", "verify", "built", "apply", "done", "failed"]


class PackStageView(BaseModel):
    name: PackStage
    state: Literal["pending", "active", "done", "failed", "skipped"] = "pending"
    seconds: float = 0.0


class PackTrack(BaseModel):
    """A track of the container in its place: the number of its voice-over in the order of the season (None for one the
    order has not), its name, and where it comes from — `kept` (the file before held it), `new` (made in this sync), `own`
    (the own sound of the video) — or `gone` (the file before held it, this one does not)."""

    number: int | None = None
    label: str
    kind: Literal["kept", "new", "own", "gone"]


class PackFigures(BaseModel):
    progress: int | None = None
    written_mb: float | None = None
    speed_x: float | None = None
    eta: int | None = None
    seconds_video: float | None = None
    size_mb: float | None = None
    steps_done: int | None = None
    steps_of: int | None = None
    # How long a built container waited for the journal of the sync.
    wait_s: float | None = None


class PackCard(BaseModel):
    id: str
    series_id: int
    series_name: str = ""
    sync_started: str = ""
    episode: str = ""
    name: str = ""
    video: DownloadVariant | None = None
    status: PackStatus = "queued"
    stages: list[PackStageView] = Field(default_factory=lambda: [PackStageView(name=each) for each in PACK_STAGES])
    tracks: list[PackTrack] = Field(default_factory=list)
    # Why the file is built anew when no track came or went: "order" (the order of the season), "video" (another video),
    # "names" (the titles or languages of the tracks); "" when tracks came or went.
    reason: Literal["", "order", "video", "names"] = ""
    # Not built: a step of the plan it needs did not succeed (a download, an alignment); the file before stays.
    waited: bool = False
    # Why the build failed: the code of the problem of the build (core/container.py: container_length_wrong and the others);
    # "" when it did not fail or failed for another error (its words are in the log).
    failure: str = ""
    figures: PackFigures = Field(default_factory=PackFigures)
    queued_at: str = ""
    finished_at: str = ""
    seconds: float = 0.0


JournalStep = Literal["move", "remove", "publish", "retag"]


class PackJournal(BaseModel):
    """The journal of a series being carried out: every file a sync puts in place, takes away, renames or rewrites
    (`retag` rewrites the container for its new name or state), one step after another, whichever episode's it is. `kind`
    is `sync` (the end of a sync) or `recover` (a journal a stop cut short, carried out at the start). `step` and `file`
    are the step under way and the file it acts on."""

    series_id: int
    series_name: str = ""
    sync_started: str = ""
    kind: Literal["sync", "recover"] = "sync"
    steps_of: int = 0
    steps_done: int = 0
    step: JournalStep | None = None
    file: str = ""
    started_at: str = ""
    seconds: float = 0.0

