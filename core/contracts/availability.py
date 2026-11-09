"""What the user may do with an object that is not ready: one table for every module, the API and the window.

An object has a state (a series is idle or being scanned, a download waits or runs or failed, a variant of a voice-over
plays or does not); an action is allowed in some states and refused in the others. The owner of the object asks this table
and refuses with the same reason whoever asked, and the window is given the table and draws a control only where the
table allows it, so no place decides for itself.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Iterable

from core.reasons import reason

from .composition import CompositionRow
from .release_table import RowState


class Action(StrEnum):
    SYNC = "sync"                          # of a series: fetch what the plan wants
    EDIT = "edit"                          # of a series: save its card, its sources, the levers of its output
    CANCEL_DOWNLOAD = "cancel_download"    # of a download task
    CHOOSE_REFERENCE = "choose_reference"  # of a variant: make it the reference
    PREFER_VARIANT = "prefer_variant"      # of a variant: take it first for the voice-over of its group


# A series is scanning from the start of a sync until every work of that run has ended (`work.RunChanged`).
SERIES_IDLE, SERIES_SCANNING = "idle", "scanning"
# The states of a download task are the ones of `download_tasks.status`.
VARIANT_REFERABLE = "referable"  # a line of it is one episode that plays
VARIANT_PLAYABLE = "playable"    # only compilations play
VARIANT_ABSENT = "absent"        # nothing of it plays

ALLOWED: dict[Action, frozenset[str]] = {
    Action.SYNC: frozenset({SERIES_IDLE}),
    Action.EDIT: frozenset({SERIES_IDLE}),
    Action.CANCEL_DOWNLOAD: frozenset({"pending", "downloading", "processing"}),
    Action.CHOOSE_REFERENCE: frozenset({VARIANT_REFERABLE}),
    Action.PREFER_VARIANT: frozenset({VARIANT_REFERABLE, VARIANT_PLAYABLE}),
}


class NotAllowed(ValueError):
    """The action is not allowed in the state of the object; the message is the reason (core/reasons)."""


def allowed(action: Action | str, state: str) -> bool:
    return state in ALLOWED.get(Action(action), frozenset())


def refusal(action: Action | str, state: str) -> str:
    return reason("not_allowed", action=str(Action(action)), state=state)


def require(action: Action | str, state: str) -> None:
    if not allowed(action, state):
        raise NotAllowed(refusal(action, state))


def table() -> dict[str, list[str]]:
    """The table as a window is given it: for each action the states in which it is allowed."""
    return {str(action): sorted(states) for action, states in ALLOWED.items()}


def lines_of_level(rows: Iterable[CompositionRow], season: int | None = None, episode: int | None = None) -> list[CompositionRow]:
    """The lines a level of the window shows: those of the output (`CompositionRow.in_output`) that cover an episode of the
    level (the whole series, a season, or one episode)."""
    found = []
    for row in rows:
        if not row.in_output:
            continue
        end = row.episode_end if row.episode_end is not None else row.episode_start
        if (season is None or row.season == season) and (episode is None or row.episode_start <= episode <= end):
            found.append(row)
    return found


def variant_state(rows: Iterable[CompositionRow]) -> str:
    """The state of a variant (a voice-over group at a player of a source) by its lines."""
    playing = [row for row in rows if row.state == RowState.PLAYABLE]
    if not playing:
        return VARIANT_ABSENT
    return VARIANT_REFERABLE if any(not row.is_compilation for row in playing) else VARIANT_PLAYABLE
