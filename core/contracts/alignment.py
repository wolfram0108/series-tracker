"""How the alignment service ended a job, as the conform module reports it."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    """The states of a job in the service, and `gone` for a job the service no longer knows."""

    QUEUED = "queued"
    PAUSED = "paused"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    GONE = "gone"


class DubResult(BaseModel):
    """The result for one voice-over (a pair: the reference and the voice-over)."""

    dub: str | None = None
    ok: bool = False
    skipped: bool = False
    out_path: str | None = None
    suspect: bool = False
    critical: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    resid_ms: float | None = None
    error: str | None = None


class AlignmentJob(BaseModel):
    """The end of a job: `conform.job.finished` and the reply of `conform.job.get`."""

    job_id: str | None = None
    status: JobStatus
    error: str | None = None
    results: list[DubResult] = Field(default_factory=list)
