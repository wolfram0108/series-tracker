"""The registry of output levers: what a lever is called, what it may point at, what values it takes and its default.

A new lever is one `register` call: storage, inheritance and the commands of settings read this registry and
change nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from core.contracts import (REFERENCE_ALLOW, REFERENCE_CHOICE, VOICE_PLAYER, VOICE_TAKE, parse_group_target,
                            parse_player_target)

class TargetKind(StrEnum):
    """What a lever's value is about: nothing in particular, a player of a source, or a voice-over group."""

    NONE = "none"
    PLAYER = "player"
    GROUP = "group"


def _no_sources(value: Any) -> frozenset[int]:
    return frozenset()


@dataclass(frozen=True)
class Lever:
    """`accepts` tells whether a value is one of the lever's values; `consumer` is the module that acts on it.

    `value_sources` names the sources of the series a value points at, so that a removed source takes the records
    that mention it, whatever shape the value of a lever has."""

    name: str
    target: TargetKind
    accepts: Callable[[Any], bool]
    default: Any
    consumer: str
    value_sources: Callable[[Any], frozenset[int]] = _no_sources

    def accepts_target(self, target: str | None) -> bool:
        """Whether `target` is the form this lever points at; a lever without a target takes none."""
        if self.target == TargetKind.NONE:
            return target is None
        if not isinstance(target, str):
            return False
        if self.target == TargetKind.PLAYER:
            return parse_player_target(target) is not None
        return parse_group_target(target) is not None

    def sources_of(self, target: str | None, value: Any) -> frozenset[int]:
        """The sources of the series a record of this lever refers to, by its target and by its value."""
        found = self.value_sources(value)
        player = parse_player_target(target) if self.target == TargetKind.PLAYER and isinstance(target, str) else None
        return found | {player[0]} if player is not None else found


_levers: dict[str, Lever] = {}


def register(lever: Lever) -> None:
    """Adds a lever; a name is taken once, so two owners cannot give one name two meanings."""
    if lever.name in _levers:
        raise ValueError(f"lever {lever.name!r} is already registered")
    _levers[lever.name] = lever


def unregister(name: str) -> None:
    """Takes a lever out of the registry; its stored records stay and are ignored until it comes back."""
    _levers.pop(name, None)


def get(name: str) -> Lever | None:
    return _levers.get(name)


def all() -> list[Lever]:
    """Every lever in the order of registration."""
    return list(_levers.values())


def _is_flag(value: Any) -> bool:
    return isinstance(value, bool)


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_pick(value: Any, shape: dict[str, Callable[[Any], bool]]) -> bool:
    """Auto (None) or a dict that has exactly the fields of `shape`, each of its kind."""
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) != set(shape):
        return False
    # No builtin `all` here: this module's own `all` is the registry listing.
    return not [key for key, check in shape.items() if not check(value[key])]


def _is_reference_choice(value: Any) -> bool:
    return _is_pick(value, {"group": _is_name, "source_id": _is_id, "player": _is_name})


def _is_player_pick(value: Any) -> bool:
    return _is_pick(value, {"source_id": _is_id, "player": _is_name})


def _picked_source(value: Any) -> frozenset[int]:
    return frozenset({value["source_id"]}) if isinstance(value, dict) and _is_id(value.get("source_id")) else frozenset()


register(Lever(REFERENCE_CHOICE, TargetKind.NONE, _is_reference_choice, None, "scan", _picked_source))
register(Lever(REFERENCE_ALLOW, TargetKind.PLAYER, _is_flag, True, "scan"))
register(Lever(VOICE_TAKE, TargetKind.GROUP, _is_flag, True, "voices"))
register(Lever(VOICE_PLAYER, TargetKind.GROUP, _is_player_pick, None, "voices", _picked_source))
