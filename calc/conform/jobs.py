"""The jobs of the alignment service as the bridge counts them: what a job and its results come to for the window and for the planner,
the requests the service is sent, how its events change the mirror of its history, and what its answers prove.

The service owns the state; the module `conform` keeps a mirror of it and talks to it. The jobs here are the dictionaries the service
sends (its own protocol); what leaves this file is a model of core/contracts/alignment.py.
"""
from __future__ import annotations

import os
import re
from typing import Any, Mapping, Sequence

from core.contracts import (AlignmentJob, ConformParams, ConformQueue, ConformTask, DaemonState, DubResult, JobKind, JobSubmit, PlotRef,
                            ReverseSubmit, TaskResult, series_of_job_key)
from core.reasons import reason

from . import settings as conform_settings
from .paths import name_of, translate

# The word of the service's trace for a gate that turns a pair down (the one gate there is: a foreign video).
ABORT = "abort"
ENDED = ("done", "failed", "cancelled")

_KEY_SUFFIX = re.compile(r"(?:^|\s+)#(\S+)$")
_JOB_ID = re.compile(r"[\w-]+")
# A plot is named after the file of its voice-over (`<series> s06e08 — <voice-over> AniLiberty__track.png`): any name of one file
# of the folder of the job, nothing that leaves it (no separator of a path, no control character).
_PLOT_NAME = re.compile(r"[^/\\\x00-\x1f]+\.png")

Job = Mapping[str, Any]


# --- a job as the window and the planner see it ----------------------------------------------------------------------------

def job_label(label: str | None, key: str | None) -> str | None:
    """The label the job carries: the human one and, when given, the key that finds the job again."""
    return f"{label or ''} #{key}".strip() if key else label


def split_label(label: str | None) -> tuple[str | None, str | None]:
    """The inverse of job_label: the human part of a job's label and the key it carries, if any."""
    found = _KEY_SUFFIX.search(label or "")
    return ((label or "")[:found.start()], found.group(1)) if found else (label, None)


def refused_by_service(result: Job) -> bool:
    """The service itself turned the pair down: its trace holds a decision that went the way of `abort`. A pair that is not `ok`
    without such a decision crashed in the service (its trace may end in an `exception`, or hold nothing): no judgement of
    the voice-over, only a failure to make one."""
    return not result.get("ok") and any(
        record.get("kind") == "decision" and record.get("verdict") == ABORT for record in result.get("trace") or [])


def plot_of(result: Job) -> str | None:
    """The name of the picture of a voice-over's alignment plot; the interactive page of it is not shown."""
    return next((str(ref["name"]) for ref in result.get("plots") or []
                 if ref.get("kind") == "track" and str(ref.get("name") or "").endswith(".png")), None)


def task_of(job: Job) -> ConformTask:
    """The part of a daemon job the interface shows; traces, plots and operation logs stay behind."""
    label, key = split_label(job.get("label"))
    return ConformTask(
        id=job.get("id"), kind=job.get("kind"), label=label, status=job.get("status"), progress=job.get("progress"),
        stage=job.get("stage"), stage_pct=job.get("stage_pct"), detail=job.get("detail"), dub_index=job.get("dub_index"),
        dub_total=job.get("dub_total"), cur_dub=job.get("cur_dub"), elapsed_s=job.get("elapsed_s"), error=job.get("error"),
        created_at=job.get("created_at"), updated_at=job.get("updated_at"),
        key=key, series_id=series_of_job_key(key), dubs=tuple(name_of(str(dub)) for dub in job.get("dubs") or []),
        results=tuple(TaskResult(
            dub=result.get("dub"), plot=plot_of(result), ok=bool(result.get("ok")), refused=refused_by_service(result),
            skipped=bool(result.get("skipped")), resid_ms=result.get("audio_resid_ms"), coverage=result.get("audio_coverage"),
            cuts=result.get("audio_cuts"), suspect=bool(result.get("suspect")), warnings=tuple(result.get("warnings") or ()),
            critical=tuple(result.get("critical") or ()), error=result.get("error"), from_ref_s=result.get("from_ref_s"),
            from_dub_s=result.get("from_dub_s")) for result in job.get("results") or []))


def picture_of(jobs: Mapping[str, Job], daemon: DaemonState) -> ConformQueue:
    """The whole picture of the service: its state and its jobs in the order it numbered them."""
    return ConformQueue(daemon=daemon, tasks=tuple(task_of(job) for job in sorted(jobs.values(), key=lambda job: job.get("seq") or 0)))


def finished_view(job: Job, to_local: Mapping[str, str]) -> AlignmentJob:
    """How a job ended, with the output paths translated back to this server."""
    def local(path: str | None) -> str | None:
        if not path:
            return None
        try:
            return translate(path, to_local)
        except ValueError:
            return path

    return AlignmentJob(
        job_id=job.get("id"), kind=job.get("kind") or JobKind.EPISODE, status=job.get("status"), error=job.get("error"),
        results=tuple(DubResult(
            dub=result.get("dub"), ok=bool(result.get("ok")), refused=refused_by_service(result),
            skipped=bool(result.get("skipped")),
            out_path=local(result.get("out_path")), suspect=bool(result.get("suspect")),
            critical=tuple(result.get("critical") or ()), warnings=tuple(result.get("warnings") or ()),
            resid_ms=result.get("audio_resid_ms"), error=result.get("error"),
            from_ref_s=result.get("from_ref_s"), from_dub_s=result.get("from_dub_s"),
        ) for result in job.get("results") or []))


def unannounced(jobs: Mapping[str, Job], announced: set[str]) -> list[Job]:
    """The jobs that ended and were not reported yet."""
    return [job for job in jobs.values() if job.get("status") in ENDED and job["id"] not in announced]


# --- the events of the service ---------------------------------------------------------------------------------------------

def mirrored(jobs: Mapping[str, Job], name: str, payload: Mapping[str, Any]) -> tuple[dict[str, Job], tuple[str, ...]]:
    """The mirror of the history of the service after one of its events (`conform.snapshot`: the whole history; `conform.changed`: the
    jobs that changed and the ones removed; `conform.progress`: the figures of one job), and the numbers of the jobs it removed."""
    if name == "conform.snapshot":
        return {job["id"]: job for job in payload.get("items") or []}, ()
    if name == "conform.changed":
        after = {**jobs, **{job["id"]: job for job in payload.get("items") or []}}
        removed = tuple(payload.get("removed") or ())
        for job_id in removed:
            after.pop(job_id, None)
        return after, removed
    if name == "conform.progress":
        job = jobs.get(payload.get("id"))
        if job is not None:
            return {**jobs, job["id"]: {**job, **{key: value for key, value in payload.items() if key != "id"}}}, ()
    return dict(jobs), ()


# --- what the service is sent ----------------------------------------------------------------------------------------------

def episode_body(request: JobSubmit, mapping: Mapping[str, str], params: ConformParams) -> dict[str, Any]:
    """The body of `POST /conform/enqueue`: the files in the service's names, the label with the key, the parameters; a path outside the
    share is a ValueError (a certain refusal, before anything is sent)."""
    return {"ref": translate(request.ref, mapping),
            "dubs": [translate(path, mapping) for path in request.dubs],
            "out_dir": translate(request.out_dir, mapping),
            "cache_dir": translate(request.cache_dir, mapping),
            "label": job_label(request.label, request.key), **conform_settings.episode_fields(params),
            # A voice-over that is a container gives the track of its number (a track aligned out of an old file).
            **({"dub_atracks": [int(each) for each in request.dub_atracks]} if request.dub_atracks else {}),
            # A reference that is a container holds several tracks: the alignment listens to the video's own sound.
            **({"ref_atrack": int(request.ref_atrack)} if request.ref_atrack else {})}


def reverse_label(request: ReverseSubmit) -> str:
    return request.label or os.path.basename(request.compilation)


def reverse_keys(request: ReverseSubmit) -> list[str | None]:
    """The key of each episode of a batch: the label of the folder of its video."""
    return [split_label(os.path.basename(os.path.dirname(each.path)))[1] for each in request.episodes]


def reverse_body(request: ReverseSubmit, label: str, keys: Sequence[str | None], mapping: Mapping[str, str],
                 params: ConformParams) -> dict[str, Any]:
    """The body of `POST /conform/reverse`; an episode without a key to find its job again by, or a path outside the share, is a
    ValueError (a certain refusal)."""
    if not all(keys):
        raise ValueError(reason("conform_reverse_no_key"))
    return {"compilation": translate(request.compilation, mapping),
            "episodes": [translate(each.path, mapping) for each in request.episodes],
            "out_dir": translate(request.out_dir, mapping), "label": label,
            "ref_atrack": request.compilation_atrack, "dub_atracks": [each.atrack for each in request.episodes],
            **conform_settings.compilation_fields(params)}


def named_jobs(jobs: Sequence[Job], keys: Sequence[str | None]) -> dict[str, str]:
    """The jobs the service named for a batch: the number of each whose key is one of the batch's."""
    return {key: str(job["id"]) for job in jobs if (key := split_label(job.get("label"))[1]) in keys}


def plot_is_plain(ref: PlotRef) -> bool:
    """Whether the picture asked for is one the service may be asked for: a job number and the plain PNG name of a file of its folder."""
    return bool(_JOB_ID.fullmatch(ref.job_id) and _PLOT_NAME.fullmatch(ref.name))


# --- what the answers of the service prove ---------------------------------------------------------------------------------

def job_with_key(jobs: Sequence[Job], key: str) -> str | None:
    """The number of the job sent with this key (the end of its label)."""
    mark = f"#{key}"
    return next((job["id"] for job in jobs if str(job.get("label") or "").endswith(mark)), None)


def keys_of(jobs: Sequence[Job]) -> tuple[str, ...]:
    """The keys of the jobs the history holds, sorted (`… #key` of their labels)."""
    return tuple(sorted({key for job in jobs if (key := split_label(job.get("label"))[1])}))


def stopped_or_gone(left: Job | None) -> bool:
    """What the service holds of a job after an answer of failure to its cancel: no record, or a `cancelled` one, is done."""
    return left is None or left.get("status") == "cancelled"
