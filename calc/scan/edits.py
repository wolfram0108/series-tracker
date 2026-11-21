"""The changes of a series by the user that a sync waits for (the admission of a change, И-60).

A change begins with a number the caller made before it was granted, and ends with that number once every request of the change is
answered. A caller that stops waiting for the grant gives it back by the number, and an end that comes before its beginning is kept: the
beginning that comes later opens nothing. `Edits` is never changed: a function gives the next one."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class Edits:
    open: Mapping[int, frozenset[str]] = field(default_factory=dict)   # the series -> the numbers of the changes under way
    early: Mapping[int, frozenset[str]] = field(default_factory=dict)  # the series -> the numbers whose end came before their beginning


def _with(mapping: Mapping[int, frozenset[str]], series_id: int, numbers: frozenset[str]) -> dict[int, frozenset[str]]:
    rest = {key: each for key, each in mapping.items() if key != series_id}
    return {**rest, series_id: numbers} if numbers else rest


def ended_early(edits: Edits, series_id: int, token: str) -> tuple[Edits, bool]:
    """Whether the end of this number came before its beginning; if so, it is used up (the beginning opens nothing)."""
    numbers = edits.early.get(series_id, frozenset())
    if token not in numbers:
        return edits, False
    return Edits(edits.open, _with(edits.early, series_id, numbers - {token})), True


def admitted(edits: Edits, series_id: int, token: str) -> Edits:
    """The change of that number is under way."""
    return Edits(_with(edits.open, series_id, edits.open.get(series_id, frozenset()) | {token}), edits.early)


def ended(edits: Edits, series_id: int, token: str) -> tuple[Edits, bool]:
    """The change of that number is written whole (an end told twice is the same as once). An end of a number not admitted is the end
    of a grant its caller gave up waiting for: kept for that grant. Returns the edits and whether no change of the series is left under way
    because of this end."""
    numbers = edits.open.get(series_id)
    if numbers is None or token not in numbers:
        if token:
            return Edits(edits.open, _with(edits.early, series_id, edits.early.get(series_id, frozenset()) | {token})), False
        return edits, False
    left = numbers - {token}
    return Edits(_with(edits.open, series_id, left), edits.early), not left


def under_way(edits: Edits, series_id: int) -> frozenset[str]:
    return edits.open.get(series_id, frozenset())


def forgotten(edits: Edits, series_id: int) -> Edits:
    """The series was deleted: the numbers kept for the ends that came early go with it."""
    return Edits(edits.open, _with(edits.early, series_id, frozenset()))
