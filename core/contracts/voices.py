"""What the voices module tells the rest of the system: statuses, states, events, snapshot."""
from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field


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
    AWAITING_ANSWER = "awaiting_answer"
    ALIGNING_WAIT = "aligning_wait"
    ALIGNING_RUN = "aligning_run"
    ALIGNED = "aligned"
    DONE = "done"
    PACKED = "packed"


class Reason(BaseModel):
    """Why a voice-over is in its state: a code and the facts that fill its text in the interface."""

    code: str
    params: dict[str, str | int | float] = Field(default_factory=dict)


class DubItem(BaseModel):
    """`part` is None for an episode without parts: a voice-over belongs to one part of an episode."""

    season: int
    episode: int
    part: int | None = None
    voice_key: str
    voice_label: str
    status: DubStatus
    state: DubState
    hint: DubHint | None
    detail: Reason | None
    # The source and the player the work is on: the candidate of the chain it began with, the first one for a row that
    # has not begun; None for a voice-over with no candidate. A window names the variant the voice-over comes from by them.
    source_id: int | None = None
    player: str | None = None


class SnapshotItem(DubItem):
    series_id: int
    # With `file`, how the alignment queue names the voice-over: the job that aligns it and the file it was sent as.
    job_id: str | None = None
    file: str | None = None


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
    """One voice-over, whole, so the interface asks nothing back; the newest (`epoch`, `rev`) wins."""

    series_id: int
    season: int
    episode: int
    part: int | None = None
    voice_key: str
    voice_label: str
    status: DubEventStatus
    state: DubState
    hint: DubHint | None
    detail: Reason | None
    epoch: int
    rev: int
    job_id: str | None = None
    file: str | None = None
    source_id: int | None = None
    player: str | None = None


class DubsSnapshot(BaseModel):
    epoch: int
    rev: int
    items: list[SnapshotItem]


class VoiceSettingsDraft(BaseModel):
    """What the settings screen of the voices sends: the order of the sources and the groups of voice-overs.
    A group is a name with the list of names that put a voice-over into it; a save replaces both values."""

    priority: list[str]
    voice_groups: dict[str, list[str]]


class SettingProblem(BaseModel):
    """What is wrong with one field of the settings; the interface words the reason."""

    field: str
    detail: Reason


class VoiceSettingsView(VoiceSettingsDraft):
    """The reply of `voices.settings.get`: what applies now, the order to return to, and the fields whose stored
    value was refused."""

    default_priority: list[str]
    rejected: list[str]
    # Players the user put into one group take one place in the order; no screen edits them yet.
    player_groups: dict[str, list[str]] = Field(default_factory=dict)


class VoiceSettingsSaved(BaseModel):
    """The reply of `voices.settings.set`: saved, or the problems that kept it from being saved."""

    saved: bool
    problems: list[SettingProblem]
    settings: VoiceSettingsView
