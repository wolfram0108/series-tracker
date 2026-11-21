"""Which dumps stay: the newest `keep` of a folder, and all of them together within a budget of bytes, the oldest going first."""
from __future__ import annotations

from typing import Iterable

SUFFIX = ".jsonl.gz"


def surplus(names: Iterable[str], keep: int) -> list[str]:
    """The dumps of a folder past the newest `keep` (the names are the start of the run in UTC, so the order of the names is that of time)."""
    dumps = sorted(name for name in names if name.endswith(SUFFIX))
    return dumps[:max(0, len(dumps) - keep)]


def oldest_first(found: Iterable[tuple[float, int, str]], writing: set[str]) -> list[tuple[int, str]]:
    """(size, path) of the dumps that may go, the oldest first; `found` is (modified, size, path) of every dump, and the ones being
    written are never among them."""
    return [(size, path) for _at, size, path in sorted(found) if path not in writing]
