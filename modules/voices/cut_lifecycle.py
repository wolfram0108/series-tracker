"""The life of a cut out of a compilation (stage 12): which step may follow which, in one table.

Every write that changes the step of a cut names its transition here; the repository lets it through only from the steps
the transition lists, as for a voice-over (lifecycle.py).
"""
from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple


class CutStep(StrEnum):
    PLANNED = "planned"
    DOWNLOADING = "downloading"
    READY = "ready"
    SUBMITTING = "submitting"
    ALIGNING = "aligning"
    DONE = "done"
    OFFERED = "offered"
    FAILED = "failed"


class CutTransition(NamedTuple):
    name: str
    sources: frozenset[CutStep]
    target: CutStep


def _t(name: str, sources: set[CutStep], target: CutStep) -> CutTransition:
    return CutTransition(name, frozenset(sources), target)


S = CutStep
# The file of the compilation is being downloaded, or is on disk already; the single video of a slot taken back is fetched.
FETCH = _t("fetch", {S.PLANNED}, S.DOWNLOADING)
FILE_READY = _t("file_ready", {S.PLANNED, S.DOWNLOADING}, S.READY)
SEND = _t("send", {S.READY}, S.SUBMITTING)
UNSEND = _t("unsend", {S.SUBMITTING}, S.READY)
ATTACH = _t("attach", {S.SUBMITTING}, S.ALIGNING)
# The service does not know the job any more: the cut is sent again.
JOB_GONE = _t("job_gone", {S.ALIGNING}, S.READY)
CUT = _t("cut", {S.ALIGNING, S.DOWNLOADING}, S.DONE)
OFFER = _t("offer", {S.DONE}, S.OFFERED)
FAIL = _t("fail", {S.PLANNED, S.DOWNLOADING, S.READY, S.SUBMITTING, S.ALIGNING}, S.FAILED)
RETRY = _t("retry", {S.FAILED}, S.PLANNED)

TRANSITIONS = (FETCH, FILE_READY, SEND, UNSEND, ATTACH, JOB_GONE, CUT, OFFER, FAIL, RETRY)

# Steps of a cut that are not its end: work of its episode.
LIVE = frozenset({S.PLANNED, S.DOWNLOADING, S.READY, S.SUBMITTING, S.ALIGNING, S.DONE})
# Steps in which a cut reads the file of its compilation.
READING = frozenset({S.DOWNLOADING, S.READY, S.SUBMITTING, S.ALIGNING})
# The ends of a cut.
FINAL = frozenset({S.OFFERED, S.FAILED})
