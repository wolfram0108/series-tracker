"""The plan of a series: which row of media_items stands for each slot, and which rows it displaced.

A pure function. The winner of a slot is not decided here: it is the reference the choice of `reference.py` made,
given as `winners` (slot -> unique_id). Every other row of a slot with a winner is `replaced` by it; the file of such
a row is dropped by `downloads` once the winner is on disk (decision R-37). A compilation is never in the plan.

Statuses: in_plan_single (the reference of its slot), replaced (displaced by the winner of its slot), redundant (no
winner holds its slot, or it is a compilation). A row the user switched off, or one of a switched-off season, gets no
status: the repository leaves it `candidate`.
"""
from __future__ import annotations

from typing import Any, Iterable

Slot = tuple[int | None, int, int | None]  # (season, episode, part)


def slot_of(item: dict[str, Any]) -> Slot:
    return item.get("season"), item["episode_start"], _part(item)


def is_compilation(item: dict[str, Any]) -> bool:
    return bool(item.get("episode_end")) and item["episode_end"] > item["episode_start"]


def _part(item: dict[str, Any]) -> int | None:
    part = item.get("part")
    if part is None:
        return None
    try:
        return int(part)
    except (ValueError, TypeError):
        return None


def plan_with_supersession(rows: list[dict[str, Any]], winners: dict[Slot, str],
                           ignored_seasons: Iterable[int] | None = None,
                           ) -> tuple[dict[str, str], dict[str, str]]:
    """Rows and the winner of every slot -> ({unique_id: plan_status}, {displaced: winner})."""
    ignored = {int(season) for season in (ignored_seasons or ())}

    def switched_off(item: dict[str, Any]) -> bool:
        return bool(item.get("is_ignored_by_user")) or item.get("season") in ignored

    # A winner that is switched off holds nothing: the rest of its slot is not displaced by a row that is not in the plan.
    off = {row["unique_id"] for row in rows if switched_off(row)}
    holders = {slot: unique_id for slot, unique_id in winners.items() if unique_id not in off}
    statuses: dict[str, str] = {}
    superseded: dict[str, str] = {}
    for item in rows:
        unique_id = item["unique_id"]
        if switched_off(item):
            continue
        holder = holders.get(slot_of(item))
        if is_compilation(item) or holder is None:
            statuses[unique_id] = "redundant"
        elif holder == unique_id:
            statuses[unique_id] = "in_plan_single"
        else:
            statuses[unique_id] = "replaced"
            superseded[unique_id] = holder
    return statuses, superseded
