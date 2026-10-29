"""How the voices module speaks with the alignment service: the folders it asks for, which result
belongs to which voice-over, and whether a result may be placed next to the video.

Everything the module knows about the service's naming and about what a good result is lives here.
"""
from __future__ import annotations

import os

from core.contracts import AlignmentJob, DubResult, JobStatus
from core.reasons import reason

OUT_DIR = "_aligned"
CACHE_DIR = "_cache"
# The service makes this directory next to the reference when a job starts and leaves it empty.
SCRATCH_DIR = "_tmp"
ENDED = (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)


def stem(path: str | None) -> str:
    return os.path.splitext(os.path.basename(path or ""))[0]


def job_folders(dub_folder: str) -> tuple[str, str]:
    """(where the service writes aligned tracks, where it keeps its cache) for the voice-overs in a folder."""
    return os.path.join(dub_folder, OUT_DIR), os.path.join(dub_folder, CACHE_DIR)


def dub_folder_of_track(track: str) -> str:
    """The folder of the voice-overs a track was aligned from."""
    return os.path.dirname(os.path.dirname(track))


def scratch_of(reference_folder: str) -> str:
    return os.path.join(reference_folder, SCRATCH_DIR)


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
    """Why a result may not be placed; None when it may. The service's own verdict is the only measure:
    a pair that did not converge, or that it marks suspect or critical, is refused; so is a success that
    names no file."""
    if result is None:
        return reason("no_result")
    if not result.ok:
        return result.error or reason("align_failed")
    if result.critical:
        return reason("critical", text="; ".join(result.critical))
    if result.suspect:
        return reason("suspect", text="; ".join(result.warnings)) if result.warnings else reason("suspect_residual")
    if not result.out_path:
        return reason("output_missing")
    return None


def job_failure(job: AlignmentJob) -> str:
    return job.error or reason("job_ended", status=job.status)
