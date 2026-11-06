"""The output of a series as other modules see it: the stored references, with what the user switched off marked
`off`, and what changed between two such outputs.

Pure functions over the stored records of `scan_references` (dicts with the columns of the table) and the rows of
media_items. The stored records are the choice itself; the memory of switched-off episodes and seasons is applied when
the output is read, so a change of that memory shows at once and the choice is never made twice.
"""
from __future__ import annotations

from typing import Any, Collection, Iterable, Mapping

from core.contracts import Reference, ReferenceMark
from core.ignored import covered
from core.reasons import reason

Slot = tuple[int, int, int | None]

_KEYS = ("season", "episode", "part", "mark", "source_id", "row_key", "player", "voice_group", "height", "reason",
         "compilation")


def slot_key(record: Mapping[str, Any]) -> Slot:
    return record["season"], record["episode"], record["part"]


def reference_of(record: Mapping[str, Any]) -> Reference:
    return Reference(**{key: record[key] for key in _KEYS})


def switched_off(record: Mapping[str, Any], items: Mapping[str, Mapping[str, Any]],
                 memory: Mapping[int, set[int]], seasons: Collection[int]) -> bool:
    """Whether the user switched the slot off: its season, its number in advance, or its row in the plan."""
    if record["season"] in seasons or covered(record["season"], {record["episode"]}, dict(memory)):
        return True
    item = items.get(record.get("unique_id") or "")
    return item is not None and bool(item.get("is_ignored_by_user"))


def shown(records: Iterable[Mapping[str, Any]], items: Mapping[str, Mapping[str, Any]],
          memory: Mapping[int, set[int]], seasons: Collection[int]) -> list[dict[str, Any]]:
    """Every slot of the output: a slot the user switched off keeps its place with the mark `off` and nothing taken,
    so that no module plans anything for it and the window can tell it from an episode with no reference. The order
    is season, episode, part (no part first)."""
    found = []
    for record in records:
        if switched_off(record, items, memory, seasons):
            found.append({**record, "mark": ReferenceMark.OFF.value, "source_id": None, "row_key": None, "player": None,
                          "voice_group": None, "height": None, "reason": reason("reference_off"), "compilation": None})
        else:
            found.append(dict(record))
    return sorted(found, key=lambda each: (each["season"], each["episode"], each["part"] is not None, each["part"] or 0))


def changes(before: Iterable[Mapping[str, Any]], after: Iterable[Mapping[str, Any]]) -> list[Reference]:
    """The reference, whole, of every slot whose record is not the same in the two outputs.

    A slot that left the output (its row is gone from the layout) is reported with the mark `none` and the reason of an episode with no reference."""
    old = {slot_key(each): each for each in before}
    new = {slot_key(each): each for each in after}
    found: list[Reference] = []
    for slot in sorted(set(old) | set(new), key=lambda each: (each[0], each[1], each[2] is not None, each[2] or 0)):
        if old.get(slot) == new.get(slot):
            continue
        if slot in new:
            found.append(reference_of(new[slot]))
        else:
            found.append(Reference(season=slot[0], episode=slot[1], part=slot[2], mark=ReferenceMark.NONE,
                                   reason=reason("reference_none")))
    return found


def marks(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """How many slots carry each mark; every mark is named, so that the summary has one shape."""
    count = {mark.value: 0 for mark in ReferenceMark}
    for each in records:
        count[each["mark"]] += 1
    return count
