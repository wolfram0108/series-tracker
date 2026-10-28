"""What the voices module tells the rest of the system: statuses, states, events, snapshot, layout."""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .release import ReleaseSource


class DubStatus(StrEnum):
    """The step a voice-over of an episode is at; the row in the database holds exactly one of these."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    READY = "ready"
    ALIGNING = "aligning"
    ALIGNED = "aligned"
    PLACED = "placed"
    REJECTED = "rejected"
    FAILED = "failed"


class DubEventStatus(StrEnum):
    """The status an event carries: a step, or a change that is not a step of its own."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    READY = "ready"
    ALIGNING = "aligning"
    ALIGNED = "aligned"
    PLACED = "placed"
    REJECTED = "rejected"
    FAILED = "failed"
    MOVED = "moved"
    STALE = "stale"
    REMOVED = "removed"


class DubState(StrEnum):
    """What the user sees of a voice-over."""

    PLANNED = "planned"
    DOWNLOAD_QUEUED = "download_queued"
    DOWNLOADING = "downloading"
    ALIGN_QUEUED = "align_queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class DubHint(StrEnum):
    """Which sentence explains a state when there is no reason to show (several steps share a state)."""

    PLANNED = "planned"
    DOWNLOAD_QUEUED = "download_queued"
    DOWNLOADING = "downloading"
    READY = "ready"
    ALIGNING_WAIT = "aligning_wait"
    ALIGNING_RUN = "aligning_run"
    ALIGNED = "aligned"
    DONE = "done"


class Reason(BaseModel):
    """Why a voice-over is in its state: a code and the facts that fill its text in the interface."""

    code: str
    params: dict[str, str | int | float] = Field(default_factory=dict)


class DubItem(BaseModel):
    season: int
    episode: int
    voice_key: str
    voice_label: str
    status: DubStatus
    state: DubState
    hint: DubHint | None = None
    detail: Reason | None = None


class SnapshotItem(DubItem):
    series_id: int


class DubRecord(DubItem):
    """A row as the voices module answers `voices.dubs.get`: the item and the facts behind it."""

    attempt: int = 0
    error: str | None = None
    outcome: str | None = None
    temp_path: str | None = None
    job_id: str | None = None
    aligned_path: str | None = None
    output_path: str | None = None
    ref_uid: str | None = None
    ref_path: str | None = None


class DubEvent(BaseModel):
    """One voice-over, whole, so the interface asks nothing back; the newest `rev` wins."""

    series_id: int
    season: int
    episode: int
    voice_key: str
    voice_label: str
    status: DubEventStatus
    state: DubState
    hint: DubHint | None = None
    detail: Reason | None = None
    rev: int


class DubsSnapshot(BaseModel):
    rev: int
    items: list[SnapshotItem]


class LayoutVoice(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    label: str
    role: str
    source: ReleaseSource
    fallbacks: list[ReleaseSource] = Field(default_factory=list)


class Layout(BaseModel):
    """The reply of `voices.layout`: which source gives the reference and where every other voice comes from."""

    model_config = ConfigDict(extra="allow")

    mode: str
    reference: ReleaseSource | None = None
    voices: list[LayoutVoice]
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    priority: list[str] = Field(default_factory=list)
