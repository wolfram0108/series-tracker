"""The life of a voice-over row: which step may follow which, in one table.

Every write that changes the step of a row names its transition here; the repository lets a write
through only from the steps the transition lists, so a row cannot get into a step by a path the table
does not know. Sets of steps that several parts of the module ask about are defined here too.
"""
from __future__ import annotations

from typing import NamedTuple

from core.contracts import DubStatus as S


class Transition(NamedTuple):
    name: str
    sources: frozenset[S]
    target: S
    # A write that may legitimately find the row elsewhere (two workers race for it).
    contested: bool = False


def _t(name: str, sources: set[S], target: S, contested: bool = False) -> Transition:
    return Transition(name, frozenset(sources), target, contested)


CLAIM = _t("claim", {S.PENDING}, S.DOWNLOADING, contested=True)
DOWNLOADED = _t("downloaded", {S.DOWNLOADING}, S.READY)
RETRY = _t("retry", {S.DOWNLOADING}, S.PENDING)
FILE_LOST = _t("file_lost", {S.READY}, S.PENDING)
SEND = _t("send", {S.READY}, S.ALIGNING)
UNSEND = _t("unsend", {S.ALIGNING}, S.READY, contested=True)
ALIGNED = _t("aligned", {S.ALIGNING}, S.ALIGNED)
PLACE = _t("place", {S.ALIGNED}, S.PLACED)
REFUSE = _t("refuse", {S.ALIGNING, S.PLACED}, S.REJECTED)
FAIL = _t("fail", {S.PENDING, S.DOWNLOADING, S.ALIGNING, S.ALIGNED, S.PLACED}, S.FAILED)
RESTART = _t("restart", {S.READY, S.ALIGNING, S.ALIGNED, S.PLACED, S.REJECTED, S.FAILED}, S.PENDING)
OFFER = _t("offer", {S.PENDING, S.FAILED}, S.PENDING)

TRANSITIONS = (CLAIM, DOWNLOADED, RETRY, FILE_LOST, SEND, UNSEND, ALIGNED, PLACE, REFUSE, FAIL, RESTART, OFFER)

# Steps in which a voice-over has not reached its end.
WORKING = frozenset({S.PENDING, S.DOWNLOADING, S.ALIGNING})
# Steps in which the work on a voice-over is over (until its reference is replaced or it is offered again).
FINAL = frozenset({S.PLACED, S.REJECTED, S.FAILED})
# Steps in which a voice-over holds files in the temporary directory or is on its way to them.
HOLDS_FILES = frozenset({S.DOWNLOADING, S.READY, S.ALIGNING, S.ALIGNED})
# Steps that wait for the reference video and the other voice-overs of the episode.
WAITS_FOR_REFERENCE = frozenset({S.READY, S.ALIGNED})
# Steps in which a voice-over still reads the file of a source row it is linked to (a waiting one is linked while the
# file serves the episodes that wait for their turn).
READS_FILE = frozenset({S.PENDING, S.DOWNLOADING, S.READY, S.ALIGNING})
