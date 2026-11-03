"""How the alignment service ended a job, as the conform module reports it."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


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
    """The result for one voice-over (a pair: the reference and the voice-over).

    A pair that is not `ok` is one of two things: the service looked at it and turned it down (`refused`), or it could not
    process it (an error, a crash, no memory): the second says nothing about the voice-over itself."""

    dub: str | None = None
    ok: bool = False
    refused: bool = False
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


class SubmitOutcome(StrEnum):
    """What is known of a job that was sent to the service, once the sending is over."""

    ACCEPTED = "accepted"  # the service named the job
    REJECTED = "rejected"  # the service understood and refused: sending it again will not help
    UNAVAILABLE = "unavailable"  # certainly not delivered: nothing is left at the service
    UNCERTAIN = "uncertain"  # may have been delivered: only the key can tell


class SubmitReply(BaseModel):
    """The reply of `conform.submit`. Whatever is not a clear answer is no answer: it is read as uncertain."""

    outcome: SubmitOutcome
    job_id: str | None = None
    reason: str | None = None
    retry: bool = False

    @model_validator(mode="after")
    def _complete(self) -> "SubmitReply":
        if self.outcome is SubmitOutcome.ACCEPTED and not self.job_id:
            raise ValueError("an accepted job has a number")
        if self.outcome is not SubmitOutcome.ACCEPTED and not self.reason:
            raise ValueError("an outcome other than accepted has a reason")
        return self

    @classmethod
    def accepted(cls, job_id: str) -> "SubmitReply":
        return cls(outcome=SubmitOutcome.ACCEPTED, job_id=job_id)

    @classmethod
    def rejected(cls, reason: str) -> "SubmitReply":
        return cls(outcome=SubmitOutcome.REJECTED, reason=reason)

    @classmethod
    def unavailable(cls, reason: str, retry: bool) -> "SubmitReply":
        return cls(outcome=SubmitOutcome.UNAVAILABLE, reason=reason, retry=retry)

    @classmethod
    def uncertain(cls, reason: str) -> "SubmitReply":
        return cls(outcome=SubmitOutcome.UNCERTAIN, reason=reason)
