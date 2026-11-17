"""The order of the subtitles of a container and how their tracks lie in it (stage 15, DESIGN.md 7.1.1): one rule for the
window and the planner.

A variant of subtitles is (name, source id, player). Every variant the level has is a track of the container unless the
user left it out; the user's order (`subtitles.order`) puts the variants he named first, in his order, and the rest follow
in the order of the sources of the series, then by where their first row stands in the layout.
"""
from __future__ import annotations

from typing import Iterable, Sequence

Variant = tuple[str, int, str]


def ordered(found: Iterable[tuple[Variant, int, int]], named: Sequence[Variant]) -> list[Variant]:
    """The variants in the order of the container. `found` are the variants of the level, each with the place of its source
    among the sources of the series and the place of its first row in the layout; `named` the user's order (a variant
    named there that the level does not have is passed over)."""
    places = {}
    for variant, source_place, position in found:
        known = places.get(variant)
        places[variant] = (source_place, position) if known is None else min(known, (source_place, position))
    first = [variant for variant in dict.fromkeys(named) if variant in places]
    rest = sorted((variant for variant in places if variant not in first), key=lambda variant: places[variant])
    return first + rest


def named_of(value: object) -> list[Variant]:
    """The variants of a value of `subtitles.order`; an item that is not one is passed over."""
    if not isinstance(value, list):
        return []
    found = []
    for each in value:
        if (isinstance(each, dict) and isinstance(each.get("group"), str) and isinstance(each.get("source_id"), int)
                and not isinstance(each.get("source_id"), bool) and isinstance(each.get("player"), str)):
            found.append((each["group"], each["source_id"], each["player"]))
    return found


def flags(types: Sequence[str]) -> list[tuple[bool, bool]]:
    """How the tracks of a container lie in it, (default, forced) for each, from their types in the order of the container:
    the first full track is the default one and the first track of signs the forced one. It is the standard way of a player,
    so the user sets nothing."""
    default = next((at for at, kind in enumerate(types) if kind == "full"), None)
    forced = next((at for at, kind in enumerate(types) if kind == "signs"), None)
    return [(at == default, at == forced) for at in range(len(types))]
