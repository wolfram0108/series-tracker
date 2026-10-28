"""What the user sees of a voice-over: five states over the finer steps the module works through.

planned  the voice-over is in the plan, nothing is done yet
queued   it waits: downloaded and held for its reference and the other voice-overs of the episode,
         or sent to the alignment service and waiting there
running  it is being worked on: downloading, or being aligned in the service
done     the track lies next to the video
error    something went wrong (not downloaded, not aligned, rejected, ...); the reason is in `detail`
"""
from __future__ import annotations

# Job states of the alignment service in which a job waits for its turn.
WAITING_JOBS = (None, "queued", "paused")


def public_state(status: str, job_status: str | None = None) -> str:
    """The state of a voice-over by its step (`status`) and, while it is aligned, by its job's state."""
    if status == "aligning":
        return "queued" if job_status in WAITING_JOBS else "running"
    return {
        "pending": "planned", "stale": "planned",
        "downloading": "running", "aligned": "running",
        "ready": "queued",
        "placed": "done", "moved": "done",
        "rejected": "error", "failed": "error",
    }.get(status, "planned")
