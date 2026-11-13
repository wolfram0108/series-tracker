"""How many rows of each line of a card an application source reads: the one place that words the setting.

`all` (or nothing) reads every row of a line; `10` the first ten, `-10` the last ten, `50-60` the rows from the 50th to
the 60th (both included), in the order the application lists the episodes of the line. The setting belongs to a source and
is given at the read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ALL = "all"
_COUNT = re.compile(r"-?[1-9][0-9]*")
_SPAN = re.compile(r"([1-9][0-9]*)-([1-9][0-9]*)")


@dataclass(frozen=True)
class RowLimit:
    """The rows of a line a read takes: `n` of them from the start (`last` False) or from the end, or the rows from the
    place `start` to the place `end` (the first row of a line is the place 1)."""
    n: int = 0
    last: bool = False
    start: int = 0
    end: int = 0


def normalized(value: object) -> str | None:
    """The setting as it is stored: `all`, `N`, `-N` or `A-B` (A not above B); None for a value that names none of them."""
    text = re.sub(r"\s+", "", str(value)).replace("–", "-").replace("—", "-").lower() if value is not None else ""
    if text in ("", ALL):
        return ALL
    span = _SPAN.fullmatch(text)
    if span:
        return text if int(span[1]) <= int(span[2]) else None
    return text if _COUNT.fullmatch(text) else None


def limit_of(value: object) -> RowLimit | None:
    """The limit a setting names; None for `all` — and for a value that is no setting, which reads all and is refused at
    the place that takes the value from a user."""
    text = normalized(value)
    if text is None or text == ALL:
        return None
    span = _SPAN.fullmatch(text)
    if span:
        return RowLimit(start=int(span[1]), end=int(span[2]))
    return RowLimit(abs(int(text)), last=text.startswith("-"))


def limited(items: list, limit: RowLimit | None) -> list:
    """The rows of one line a limit takes, in the order they came."""
    if limit is None:
        return items
    if limit.start:
        return items[limit.start - 1:limit.end]
    return items[-limit.n:] if limit.last else items[:limit.n]
