"""How the alignment service ended a job, as the conform module reports it."""
from __future__ import annotations

from enum import StrEnum

from typing import Any

from pydantic import BaseModel, Field, model_validator

from .frozen import Frozen


def job_key(series_id: int, part: str) -> str:
    """The key a planner gives a job it sends to the alignment service: `plan:<series>:<part>`. The service keeps it with the
    job (in the label), so a job found anywhere — the queue, the history, a folder — says which series it works for."""
    return f"plan:{series_id}:{part}"


def job_dir(key: str) -> str:
    """The name the folder of a job has: its key without the series (`plan:<series>:<part>` → `<part>`). The folder lies in
    the folder of its series already, and a path of the service holds no colon."""
    return key.rpartition(":")[2]


def series_of_job_key(key: str | None) -> int | None:
    """The series a job works for, by its key (`job_key`); None for a key of another maker."""
    head, _, rest = (key or "").partition(":")
    number, _, _ = rest.partition(":")
    return int(number) if head == "plan" and number.isdigit() else None


class JobStatus(StrEnum):
    """The states of a job in the service, and `gone` for a job the service no longer knows."""

    QUEUED = "queued"
    PAUSED = "paused"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    GONE = "gone"


class DubResult(Frozen):
    """The result for one voice-over (a pair: the reference and the voice-over).

    A pair that is not `ok` is one of two things: the service looked at it and turned it down (`refused`), or it could not
    process it (an error, a crash, no memory): the second says nothing about the voice-over itself."""

    dub: str | None = None
    ok: bool = False
    refused: bool = False
    skipped: bool = False
    out_path: str | None = None
    suspect: bool = False
    critical: tuple[str, ...] = Field(default_factory=tuple)
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    resid_ms: float | None = None
    error: str | None = None
    # A cut out of a compilation (stage 12): seconds of the video taken from the compilation and from the episode itself.
    from_ref_s: float | None = None
    from_dub_s: float | None = None


class JobKind(StrEnum):
    """`episode`: voice-overs laid on a reference; `reverse`: an episode cut out of a compilation on its own timeline."""

    EPISODE = "episode"
    REVERSE = "reverse"


class AlignmentJob(Frozen):
    """The end of a job: `conform.job.finished` and the reply of `conform.job.get`."""

    job_id: str | None = None
    kind: JobKind = JobKind.EPISODE
    status: JobStatus
    error: str | None = None
    results: tuple[DubResult, ...] = Field(default_factory=tuple)


class SubmitOutcome(StrEnum):
    """What is known of a job that was sent to the service, once the sending is over."""

    ACCEPTED = "accepted"  # the service named the job
    REJECTED = "rejected"  # the service understood and refused: sending it again will not help
    UNAVAILABLE = "unavailable"  # certainly not delivered: nothing is left at the service
    UNCERTAIN = "uncertain"  # may have been delivered: only the key can tell


class SubmitReply(Frozen):
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
    # The audio track of the picture file that is its own sound (a file of the library holds several).
    compilation_atrack: int = 0
    episodes: list[ReverseEpisode]
    out_dir: str
    label: str | None = None


class ReverseReply(Frozen):
    """The reply of `conform.reverse`: the outcome of the batch as for one job (`SubmitReply`), and, when it was taken,
    the job of each key the service named. A key the answer does not name is not known to be taken: it is asked by its key."""

    outcome: SubmitOutcome
    jobs: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None
    retry: bool = False


class JobSubmit(BaseModel):
    """`conform.submit`: one episode, a reference video and the voice-over files to align to it (local paths), and where the service
    writes and keeps its cache. `key` is written into the label of the job, so that it can be found again when the answer is lost;
    `ref_atrack` and `dub_atracks` name the tracks of a container."""

    ref: str
    dubs: list[str]
    out_dir: str
    cache_dir: str
    label: str | None = None
    key: str | None = None
    ref_atrack: int | None = None
    dub_atracks: list[int] | None = None


class JobRef(BaseModel):
    """`conform.cancel` and `conform.job.get`: the job of the service."""

    job_id: str


class JobKey(BaseModel):
    """`conform.job.find`: the key a job was sent with."""

    key: str


class PlotRef(BaseModel):
    """`conform.plot`: the picture named in a result, of a job."""

    job_id: str
    name: str


class JobFoldersOf(BaseModel):
    """`conform.job.folders`: the episode folder of a job."""

    dub_folder: str


class JobCancelled(Frozen):
    """The reply of `conform.cancel`: the job is stopped, or deleted, or the service does not know it; false when it could not be reached."""

    done: bool


class JobFound(Frozen):
    """The reply of `conform.job.find`: the job sent with the key, and whether the service could be asked."""

    reachable: bool
    job_id: str | None = None


class JobKeys(Frozen):
    """The reply of `conform.job.keys`: the keys of the jobs the history of the service holds, and whether it could be asked."""

    reachable: bool
    keys: tuple[str, ...]


class PlotReply(Frozen):
    """The reply of `conform.plot`: the picture as base64 PNG, when the service holds it."""

    found: bool
    data: str | None = None


class ConformFolders(Frozen):
    """The reply of `conform.workspace`: the two folders inside the work folder, where voice-overs wait for alignment and where the
    service works; both empty while no work folder is named."""

    downloads: str
    conform: str


class JobFolders(Frozen):
    """The reply of `conform.job.folders`: where the service is asked to write the aligned tracks and keep its cache for an episode folder."""

    out_dir: str
    cache_dir: str


class JobRemoved(Frozen):
    """The event `conform.job.removed`: the service dropped the record of a job."""

    job_id: str


class DaemonUp(Frozen):
    """The event `conform.daemon.up`: the service became reachable, so waiting work may be sent."""


class DaemonState(Frozen):
    """What is known of the service: whether it is up, why not, and what it told of itself when it answered (`params` is what it accepts
    for the fields of its jobs, as it states it)."""

    up: bool
    error: str | None
    version: Any = None
    device: Any = None
    gpu: bool | None = None
    gpu_name: Any = None
    params: Any = None


class TaskResult(Frozen):
    """The result of one voice-over of a job, as the window shows it; `plot` names the picture of the plot, if the service made one."""

    dub: str | None
    plot: str | None
    ok: bool
    refused: bool
    skipped: bool
    resid_ms: Any
    coverage: Any
    cuts: Any
    suspect: bool
    warnings: tuple[str, ...]
    critical: tuple[str, ...]
    error: str | None
    from_ref_s: Any
    from_dub_s: Any


class ConformTask(Frozen):
    """The compact view of one job of the service: what the window shows; traces, plots and operation logs stay behind. `label` is the
    human part of its label and `key` the one it carries, `series_id` the series the key says the job works for. The figures are the
    service's own."""

    id: str
    kind: Any
    label: str | None
    status: Any
    progress: Any
    stage: Any
    stage_pct: Any
    detail: Any
    dub_index: Any
    dub_total: Any
    cur_dub: Any
    elapsed_s: Any
    error: Any
    created_at: Any
    updated_at: Any
    key: str | None
    series_id: int | None
    dubs: tuple[str, ...]
    results: tuple[TaskResult, ...]


class ConformQueue(Frozen):
    """The event `conform.queue.changed`: the whole picture of the service, on every change and whenever a window connects."""

    daemon: DaemonState
    tasks: tuple[ConformTask, ...]
