"""What the torrent client says of a torrent (qBittorrent's states) and the flag of the status of a series that it gives.

The one place that words the flag, for the torrents of the old pipeline and for the releases of the torrent sources.
"""
from __future__ import annotations

# A check of the files of a torrent is in progress.
CHECKING = {"checkingUP", "checkingDL", "checkingResumeData"}
# The torrent is in the download but no data comes (no peers or sources): the status `idle` (Р-11).
IDLE = {"stalledDL"}
# The torrent waits for a free slot in the queue of the client: the status `queued` (Р-11).
QUEUED = {"queuedDL"}
# The torrent downloads and checks nothing: no pill at all.
NOT_DOWNLOADING = {"pausedUP", "pausedDL", "stoppedUP", "stoppedDL", "uploading"}


def torrent_flag(status: str, complete: bool) -> str | None:
    """The status flag of a series that a torrent gives by what its client says (the one place that words it, for the
    torrents of the pipeline and the releases of the torrent sources): a check is shown whatever the progress; None — the
    torrent is paused or seeding and shows nothing."""
    if status in CHECKING:
        return "checking"
    if complete:
        return "ready"
    if status in IDLE:
        return "idle"
    if status in QUEUED:
        return "queued"
    if status in NOT_DOWNLOADING:
        return None
    return "downloading"
