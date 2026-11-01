"""The output of a series as other modules see it: the stored references minus what the user switched off, and what
changed between two such outputs.

Pure functions over the stored records of `scan_references` (dicts with the columns of the table) and the rows of
media_items. The stored records are the choice itself; the memory of switched-off episodes and seasons is applied when
the output is read, so a change of that memory shows at once and the choice is never made twice.
"""
from __future__ import annotations

from typing import Any, Collection, Iterable, Mapping

from core.contracts import Reference, ReferenceMark
from core.ignored import covered

Slot = tuple[int, int, int | None]

_KEYS = ("season", "episode", "part", "mark", "source_id", "row_key", "player", "voice_group", "height", "reason")


def slot_key(record: Mapping[str, Any]) -> Slot:
    return record["season"], record["episode"], record["part"]


def reference_of(record: Mapping[str, Any]) -> Reference:
    return Reference(**{key: record[key] for key in _KEYS})


def visible(records: Iterable[Mapping[str, Any]], items: Mapping[str, Mapping[str, Any]],
            memory: Mapping[int, set[int]], seasons: Collection[int]) -> list[dict[str, Any]]:
    """The records of the episodes that are wanted: not in a switched-off season, not switched off in advance, and
    whose row in the plan is not switched off by the user. The order is season, episode, part (no part first)."""
    wanted = []
    for record in records:
        if record["season"] in seasons or covered(record["season"], {record["episode"]}, dict(memory)):
            continue
        item = items.get(record.get("unique_id") or "")
        if item is not None and item.get("is_ignored_by_user"):
            continue
        wanted.append(dict(record))
    return sorted(wanted, key=lambda each: (each["season"], each["episode"], each["part"] is not None, each["part"] or 0))


def changes(before: Iterable[Mapping[str, Any]], after: Iterable[Mapping[str, Any]]
            ) -> list[tuple[int, int, int | None, ReferenceMark]]:
    """(season, episode, part, mark) of every slot whose record is not the same in the two outputs.

    A slot that left the output reports the mark `none`: the episode has no reference now."""
    old = {slot_key(each): each for each in before}
    new = {slot_key(each): each for each in after}
    found: list[tuple[int, int, int | None, ReferenceMark]] = []
    for slot in sorted(set(old) | set(new), key=lambda each: (each[0], each[1], each[2] is not None, each[2] or 0)):
        if old.get(slot) == new.get(slot):
            continue
        mark = ReferenceMark(new[slot]["mark"]) if slot in new else ReferenceMark.NONE
        found.append((*slot, mark))
    return found


def marks(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """How many slots carry each mark; every mark is named, so that the summary has one shape."""
    count = {mark.value: 0 for mark in ReferenceMark}
    for each in records:
        count[each["mark"]] += 1
    return count
