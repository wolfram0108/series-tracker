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
    # A cut out of a compilation (stage 12): seconds of the video taken from the compilation and from the episode itself.
    from_ref_s: float | None = None
    from_dub_s: float | None = None


class JobKind(StrEnum):
    """`episode`: voice-overs laid on a reference; `reverse`: an episode cut out of a compilation on its own timeline."""

    EPISODE = "episode"
    REVERSE = "reverse"


class AlignmentJob(BaseModel):
    """The end of a job: `conform.job.finished` and the reply of `conform.job.get`."""

    job_id: str | None = None
    kind: JobKind = JobKind.EPISODE
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


class ReverseEpisode(BaseModel):
    """An episode of a batch of cuts: its single video, in a folder whose name is the label of its job (it carries the
    key that finds the job again), and the audio track of it that is the video's own sound."""

    path: str
    atrack: int = 0


class ReverseSubmit(BaseModel):
    """The query `conform.reverse`: the episodes to cut out of one compilation, one job each, all writing in `out_dir`
    (the compilation is prepared once for the folder)."""

    compilation: str
    episodes: list[ReverseEpisode]
    out_dir: str
    label: str | None = None


class ReverseReply(BaseModel):
    """The reply of `conform.reverse`: the outcome of the batch as for one job (`SubmitReply`), and, when it was taken,
    the job of each key the service named. A key the answer does not name is not known to be taken: it is asked by its key."""

    outcome: SubmitOutcome
    jobs: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None
    retry: bool = False
