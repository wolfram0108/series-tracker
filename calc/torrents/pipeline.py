"""Stage state machine of the torrent pipeline — a pure decision function.

KERNEL INVARIANT (fixed by the owner, Р-14): the torrent stays paused from
the moment it is added until renaming completes. The only exception is a
magnet: there is no file list until metadata is downloaded, so the torrent
is started EXACTLY for the metadata fetch and paused again right away. Any
download before renaming puts files under their original names and breaks
the credit for existing data (recheck).

Why recheck: when a release is replaced, the old release's files already
sit on disk under the final names; the new torrent is renamed to the same
names and recheck credits the matching pieces, so only the new data is
downloaded.

Stage values in the DB are legacy strings (data constraint + the stage to
status map from Р-11). One difference from the original: renaming runs in
stage 'renaming' (previously a placeholder, the work was done at the end of
'awaiting_pause_before_rename'); behaviour is identical, the names are honest.
"""
from __future__ import annotations

from .states import CHECKING

# Stages (values in agent_tasks.stage)
AWAITING_METADATA = "awaiting_metadata"          # magnet: start the torrent
POLLING_FOR_SIZE = "polling_for_size"            # magnet: wait for metadata
AWAITING_PAUSE = "awaiting_pause_before_rename"  # wait for a stable pause
RENAMING = "renaming"                            # rename files
RECHECKING = "rechecking"                        # credit existing data
ACTIVATING = "activating"                        # start and finish
ERROR = "error"                                  # error carrier (Р-11)

INITIAL_STAGE = {"file": AWAITING_PAUSE, "magnet": AWAITING_METADATA}

# Stage → status flag map for the rollup (legacy AGENT_STAGES_TO_FLAG_MAP)
STAGE_FLAGS = {
    AWAITING_METADATA: "metadata",
    POLLING_FOR_SIZE: "metadata",
    AWAITING_PAUSE: "metadata",
    RENAMING: "renaming",
    RECHECKING: "checking",
    ACTIVATING: "activating",
    ERROR: "error",
}

# qBittorrent state families (as in the original)
STABLE_PAUSED = {"pausedUP", "pausedDL", "stalledUP", "stalledDL",
                 "stoppedUP", "stoppedDL"}  # stopped* — qBit 5.x generation
POST_RECHECK = {"queuedUP", "queuedDL", "stalledUP", "stalledDL",
                "uploading", "downloading", "pausedUP", "pausedDL",
                "stoppedUP", "stoppedDL"}
RUNNING = {"uploading", "stalledUP", "forcedUP", "downloading",
           "stalledDL", "forcedDL", "queuedUP", "queuedDL"}
PAUSED = {"pausedUP", "pausedDL", "stoppedUP", "stoppedDL"}


def decide(stage: str, info: dict, recheck_initiated: bool,
           ) -> tuple[str | None, str | None]:
    """(stage, qBittorrent info) -> (action, next stage).

    The module executes the actions: resume / pause / force_pause / rename /
    recheck / resume_and_complete / complete. (None, None) — wait.
    """
    state = info.get("state")

    if stage == AWAITING_METADATA:
        # The one exception to the invariant: start only until metadata arrives
        return "resume", POLLING_FOR_SIZE

    if stage == POLLING_FOR_SIZE:
        if info.get("total_size", 0) > 0:
            return "pause", AWAITING_PAUSE  # metadata is here — back to pause
        return None, None

    if stage == AWAITING_PAUSE:
        if state in STABLE_PAUSED:
            return None, RENAMING
        if state:  # unexpectedly active — stop immediately (invariant)
            return "force_pause", None
        return None, None

    if stage == RENAMING:
        return "rename", RECHECKING

    if stage == RECHECKING:
        if not recheck_initiated:
            return "recheck", None
        if state in POST_RECHECK and state not in CHECKING:
            return None, ACTIVATING
        return None, None

    if stage == ACTIVATING:
        if state in RUNNING:
            return "complete", None
        if state in PAUSED:
            return "resume_and_complete", None
        return None, None

    raise ValueError(f"неизвестная стадия конвейера: {stage}")
