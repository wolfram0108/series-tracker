"""How many rows of each line of a card an application source reads: the one place that words the setting.

`all` (or nothing) reads every row of a line; `10` the first ten, `-10` the last ten, in the order the application lists
the episodes of the line. The setting belongs to a source and is given at the read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ALL = "all"
_COUNT = re.compile(r"-?[1-9][0-9]*")


@dataclass(frozen=True)
class RowLimit:
    """The rows of a line a read takes: `n` of them from the start (`last` False) or from the end."""
    n: int
    last: bool = False


def normalized(value: object) -> str | None:
    """The setting as it is stored: `all`, `N` or `-N`; None for a value that names none of them."""
    text = str(value).strip().lower() if value is not None else ""
    if text in ("", ALL):
        return ALL
    return text if _COUNT.fullmatch(text) else None


def limit_of(value: object) -> RowLimit | None:
    """The limit a setting names; None for `all` — and for a value that is no setting, which reads all and is refused at
    the place that takes the value from a user."""
    text = normalized(value)
    if text is None or text == ALL:
        return None
    return RowLimit(abs(int(text)), last=text.startswith("-"))


def limited(items: list, limit: RowLimit | None) -> list:
    """The rows of one line a limit takes, in the order they came."""
    if limit is None:
        return items
    return items[-limit.n:] if limit.last else items[:limit.n]
