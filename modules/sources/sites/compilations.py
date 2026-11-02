"""A video that covers several episodes and is labelled by the last of them, found from what the table itself shows.

A voice-over studio of a player sometimes publishes one video for two or more episodes and names it by the last number:
the list has "110 серия" where the video is episodes 109 and 110, and has no 109. The list says nothing else about it, so
a row like that would be taken for the single episode 110 and could become its reference.

Two facts of the table say it, and only both together:
  - the numbering of the group has a gap before the number: the previous number the studio lists is `missing` numbers
    below it;
  - the length of the video is a whole `missing + 1` times the length of one episode in that stretch of the group.
A gap alone is an episode the studio did not publish (its video is one episode long); a length alone is an episode that
runs long (a finale, an early cut: one studio's episodes are 770 s at the start and 437 s later). The length of one
episode is read from the neighbours, not from the whole group, because it changes along the series.

A row with both facts is worded the way a player that lists a compilation words it ("109-110 серия"), and the constructor
takes the range from the text as it does for any other row: the numbers still come from the constructor alone. A row with
one fact, or with lengths that do not fit the gap, is left as it was listed: the table does not guess.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from core.contracts import RowState, TableRow

# How many numbers on each side tell the length of one episode here. Counted on the 330 videos of one studio (K-3): the
# number of videos found to be compilations is the same for 10 and 20 numbers (32) and rises to the transitions of the
# cut at 5, where the unit is read from a stretch with none of the old or none of the new episodes.
WINDOW = 10
# Fewer lengths than this tell no unit, and nothing is claimed.
MIN_NEIGHBOURS = 3
# How far, as a share of the count, the ratio of a length to the unit may be from the whole number of episodes it stands
# for. The lengths of single episodes of the studio differ by 14 s of 437 (median absolute deviation, 3.2 %, a standard
# deviation of 4.7 %), so 0.12 is about two and a half deviations of one episode, and the deviation of a sum of episodes
# is smaller still: past it the length is not an episode count, whatever the gap says.
TOLERANCE = 0.12


def _number(row: TableRow) -> int | None:
    """The episode number the player lists the row by; None for a row the player lists by no whole number."""
    value = str(row.site_fields.get("episode") or "").strip()
    return int(value) if value.isdigit() else None


def _unit(number: int, lengths: dict[int, list[float]]) -> float | None:
    """The length of one episode near `number`: the lower quartile of the lengths of the neighbours, so that a few
    compilations among them do not raise it."""
    near = sorted(length for other, each in lengths.items() if other != number and abs(other - number) <= WINDOW
                  for length in each)
    return near[len(near) // 4] if len(near) >= MIN_NEIGHBOURS else None


def covers(group: Iterable[TableRow]) -> dict[str, tuple[int, int]]:
    """The first and the last episode each compilation of one group covers, by the key of its row."""
    rows = [row for row in group if _number(row) is not None]
    numbers = sorted({_number(row) for row in rows})
    lengths: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        if row.state == RowState.PLAYABLE and row.duration_s:
            lengths[_number(row)].append(row.duration_s)
    found: dict[str, tuple[int, int]] = {}
    for row in rows:
        number = _number(row)
        before = [each for each in numbers if each < number]
        if not before or row.state != RowState.PLAYABLE or not row.duration_s:
            continue
        missing = number - before[-1] - 1
        unit = _unit(number, lengths)
        if missing < 1 or unit is None:
            continue
        ratio = row.duration_s / unit
        if round(ratio) == missing + 1 and abs(ratio / round(ratio) - 1) <= TOLERANCE:
            found[row.key] = (before[-1] + 1, number)
    return found


def label_compilations(rows: list[TableRow]) -> list[TableRow]:
    """The rows, with each compilation worded by the episodes it covers; the listed text stays in `site_fields`."""
    groups: dict[tuple, list[TableRow]] = defaultdict(list)
    for row in rows:
        groups[(row.player, row.voice.name, row.voice.kind, row.group)].append(row)
    ranges: dict[str, tuple[int, int]] = {}
    for group in groups.values():
        ranges.update(covers(group))
    return [row.model_copy(update={
        "text": f"{ranges[row.key][0]}-{ranges[row.key][1]} серия",
        "site_fields": {**row.site_fields, "listed_as": row.text, "covers": list(ranges[row.key])},
    }) if row.key in ranges else row for row in rows]
