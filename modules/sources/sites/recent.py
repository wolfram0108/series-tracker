"""Only the latest episodes of each voice-over: how a stand reads a site without loading it.

A table of a release of three hundred episodes costs a request to the site for each of them, and a site that sees that
many requests from one address blocks it. A check of the system (a stand) needs the newest episodes only, so a read can be
told to take the last `n` of each voice-over (`ReadContext.recent`). The product does not set it: its table is the whole
page. The limit counts in the order the site lists the episodes, newest last, and keeps that order.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable, Hashable, TypeVar

T = TypeVar("T")


def last_of_each(items: list[T], group: Callable[[T], Hashable], n: int | None) -> list[T]:
    """The last `n` items of each group, in the order they came; all of them when `n` is None."""
    if n is None:
        return items
    kept: dict[Hashable, list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        kept[group(item)].append(index)
    keep = {index for indexes in kept.values() for index in indexes[max(0, len(indexes) - n):]}
    return [item for index, item in enumerate(items) if index in keep]
