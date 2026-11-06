"""What the user sees of a voice-over: seven states over the finer steps the module works through.

planned         the voice-over is in the plan, nothing is done yet
download_queued its download is sent and waits for a free place
downloading     its file is being downloaded
align_queued    it waits for alignment: downloaded and held for its reference and the other
                voice-overs of the episode, or sent to the alignment service and waiting there
running         it is being aligned in the service
done            the track lies next to the video, or is inside the container of the episode (ЭТАП_11)
error           something went wrong (not downloaded, not aligned, rejected, ...); the reason is in `detail`

Where one state has several causes, the hint names the sentence that tells them apart.
"""
from __future__ import annotations

from core.contracts import DubEventStatus, DubHint, DubState

# Job states of the alignment service in which a job waits for its turn.
WAITING_JOBS = (None, "queued", "paused")

_STATE_OF = {
    DubEventStatus.PENDING: DubState.PLANNED, DubEventStatus.STALE: DubState.PLANNED,
    DubEventStatus.ALIGNED: DubState.RUNNING,
    DubEventStatus.READY: DubState.ALIGN_QUEUED,
    DubEventStatus.PLACED: DubState.DONE, DubEventStatus.MOVED: DubState.DONE,
    DubEventStatus.REJECTED: DubState.ERROR, DubEventStatus.FAILED: DubState.ERROR,
    DubEventStatus.ABSENT: DubState.ABSENT,
}


def public_state(status: str, job_status: str | None = None, fetching: bool = True) -> DubState:
    """The state of a voice-over by its step (`status`), by its job's state while it is aligned, and by
    whether its download has really begun (a download may wait for a place after it was sent)."""
    step = DubEventStatus(status)
    if step is DubEventStatus.ALIGNING:
        return DubState.ALIGN_QUEUED if job_status in WAITING_JOBS else DubState.RUNNING
    if step is DubEventStatus.DOWNLOADING:
        return DubState.DOWNLOADING if fetching else DubState.DOWNLOAD_QUEUED
    return _STATE_OF.get(step, DubState.PLANNED)


def public_hint(status: str, state: DubState, job_id: str | None = None, packed: bool = False) -> DubHint | None:
    """The sentence behind a state; None for an error, whose reason is shown instead. A voice-over that is
    being aligned and has no job number was sent and the service's answer is not known; a done one with no file of
    its own is inside the container of its episode."""
    step = DubEventStatus(status)
    if state is DubState.ERROR:
        return None
    if state is DubState.ALIGN_QUEUED:
        if step is DubEventStatus.READY:
            return DubHint.READY
        return DubHint.ALIGNING_WAIT if job_id else DubHint.AWAITING_ANSWER
    if state is DubState.RUNNING:
        return DubHint.ALIGNED if step is DubEventStatus.ALIGNED else DubHint.ALIGNING_RUN
    if state is DubState.DONE and packed:
        return DubHint.PACKED
    return DubHint(state.value)
