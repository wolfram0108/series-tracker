"""How the voices module speaks with the alignment service: the folders it asks for, which result
belongs to which voice-over, and whether a result may be placed next to the video.

Everything the module knows about the service's naming and about what a good result is lives here.
"""
from __future__ import annotations

import os

from core.contracts import AlignmentJob, DubResult, JobStatus
from core.reasons import reason

ENDED = (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)


def stem(path: str | None) -> str:
    return os.path.splitext(os.path.basename(path or ""))[0]


def result_for(job: AlignmentJob, dub_path: str) -> DubResult | None:
    """The result of the voice-over that was sent as this file: the service names results by file."""
    wanted = stem(dub_path)
    return next((result for result in job.results if stem(result.dub) == wanted), None)


def track_of(out_dir: str, result: DubResult | None) -> str | None:
    """Where this server holds the aligned track: in the folder the job asked the service to write to."""
    if result is None or not result.out_path:
        return None
    return os.path.join(out_dir, os.path.basename(result.out_path))


def refusal(result: DubResult | None) -> str | None:
    """Why a result may not be placed; None when it may. The service's own verdict, `ok`, is the only measure: a pair it
    did not bring together is refused, and so is a success that names no file. What it adds (`suspect`, `critical`,
    `warnings`) tells what it found and put right in the track it wrote: the track is in step with the reference, so it
    is no ground to refuse (`told` gives the words for the journal)."""
    if result is None:
        return reason("no_result")
    if not result.ok:
        return result.error or reason("align_failed")
    if not result.out_path:
        return reason("output_missing")
    return None


def turned_down(result: DubResult | None) -> bool:
    """The refusal of a result is the service's judgement of the voice-over (it looked at the pair and turned it down). Every
    other reason a result may not be placed (a crash in the service, no result for the file, a success with no file) is a
    failure to align: it says nothing about the voice-over, so the voice-over is offered again, not written off."""
    return result is not None and not result.ok and result.refused


def told(result: DubResult | None) -> str:
    """What the service said besides its verdict, for the journal; empty when it said nothing."""
    if result is None:
        return ""
    return "; ".join([*result.critical, *result.warnings])


def job_failure(job: AlignmentJob) -> str:
    return job.error or reason("job_ended", status=job.status)
