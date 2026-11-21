"""What the run of a series knows of the work of its executors.

An executor is working until a state of it says it is not (one not heard yet is working); of two states of one executor the later
counted (`seq`) is in force, so a reply and an event that cross on the bus cannot undo each other. While the run is `polling` it asks
every executor, and it is not closed from an event before every answer is in. A `Work` is never changed: a function gives the next one."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping

from core.contracts import WorkState


@dataclass(frozen=True)
class Work:
    working: frozenset[str]
    seen: Mapping[str, int]
    polling: bool = True
    failures: Mapping[str, str] = field(default_factory=dict)  # the executors whose work ended in failure, with why


def begin(executors: Iterable[str]) -> Work:
    """A run that asks its executors: every one of them is working until it answers."""
    return Work(working=frozenset(executors), seen={})


def apply(work: Work, state: WorkState) -> Work:
    """The run once a state of an executor is taken in; a state older than the one in force changes nothing."""
    if state.seq <= work.seen.get(state.owner, -1):
        return work
    seen = {**work.seen, state.owner: state.seq}
    if state.active:
        return replace(work, seen=seen, working=work.working | {state.owner})
    failures = {**work.failures, state.owner: state.failure} if state.failure else work.failures
    return replace(work, seen=seen, working=work.working - {state.owner}, failures=failures)


def without(work: Work, owner: str) -> Work:
    """Nobody does that kind of work in this process: the run does not wait for it."""
    return replace(work, working=work.working - {owner})


def polled(work: Work) -> Work:
    """Every executor was asked."""
    return replace(work, polling=False)


def done(work: Work) -> bool:
    return not work.polling and not work.working


def failure(work: Work) -> str | None:
    """Why the run failed: the failure of the first executor that told one; None when every work ended well."""
    return next((work.failures[owner] for owner in sorted(work.failures)), None)
