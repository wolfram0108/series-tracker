"""The registry of output levers: what a lever is called, what it may point at, what values it takes and its default.

A new lever is one `register` call: storage, inheritance and the commands of settings read this registry and
change nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from core.contracts import group_target, player_target

REFERENCE_CHOICE = "reference.choice"
REFERENCE_ALLOW = "reference.allow"
VOICE_TAKE = "voice.take"
VOICE_PLAYER = "voice.player"

_PLAYER_TARGET = re.compile(r"source:(\d+)/player:(.+)", re.DOTALL)
_GROUP_TARGET = re.compile(r"group:(.+)", re.DOTALL)


class TargetKind(StrEnum):
    """What a lever's value is about: nothing in particular, a player of a source, or a voice-over group."""

    NONE = "none"
    PLAYER = "player"
    GROUP = "group"


@dataclass(frozen=True)
class Lever:
    """`accepts` tells whether a value is one of the lever's values; `consumer` is the module that acts on it."""

    name: str
    target: TargetKind
    accepts: Callable[[Any], bool]
    default: Any
    consumer: str

    def accepts_target(self, target: str | None) -> bool:
        """Whether `target` is the form this lever points at; a lever without a target takes none."""
        if self.target == TargetKind.NONE:
            return target is None
        if not isinstance(target, str):
            return False
        if self.target == TargetKind.PLAYER:
            return parse_player_target(target) is not None
        return parse_group_target(target) is not None


def parse_player_target(target: str) -> tuple[int, str] | None:
    """(source id, player) of a target made by `player_target`; None for anything else."""
    found = _PLAYER_TARGET.fullmatch(target)
    if found is None:
        return None
    source_id, player = int(found.group(1)), found.group(2)
    return (source_id, player) if player_target(source_id, player) == target else None


def parse_group_target(target: str) -> str | None:
    """The group name of a target made by `group_target`; None for anything else."""
    found = _GROUP_TARGET.fullmatch(target)
    return found.group(1) if found is not None and group_target(found.group(1)) == target else None


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


register(Lever(REFERENCE_CHOICE, TargetKind.NONE, _is_reference_choice, None, "scan"))
register(Lever(REFERENCE_ALLOW, TargetKind.PLAYER, _is_flag, True, "scan"))
register(Lever(VOICE_TAKE, TargetKind.GROUP, _is_flag, True, "voices"))
register(Lever(VOICE_PLAYER, TargetKind.GROUP, _is_player_pick, None, "voices"))
