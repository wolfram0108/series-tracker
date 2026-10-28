"""What the user sees of a voice-over: seven states over the finer steps the module works through.

planned         the voice-over is in the plan, nothing is done yet
download_queued its download is sent and waits for a free place
downloading     its file is being downloaded
align_queued    it waits for alignment: downloaded and held for its reference and the other
                voice-overs of the episode, or sent to the alignment service and waiting there
running         it is being aligned in the service
done            the track lies next to the video
error           something went wrong (not downloaded, not aligned, rejected, ...); the reason is in `detail`
"""
from __future__ import annotations

# Job states of the alignment service in which a job waits for its turn.
WAITING_JOBS = (None, "queued", "paused")


def public_state(status: str, job_status: str | None = None, fetching: bool = True) -> str:
    """The state of a voice-over by its step (`status`), by its job's state while it is aligned, and by
    whether its download has really begun (a download may wait for a place after it was sent)."""
    if status == "aligning":
        return "align_queued" if job_status in WAITING_JOBS else "running"
    if status == "downloading":
        return "downloading" if fetching else "download_queued"
    return {
        "pending": "planned", "stale": "planned",
        "aligned": "running",
        "ready": "align_queued",
        "placed": "done", "moved": "done",
        "rejected": "error", "failed": "error",
    }.get(status, "planned")
