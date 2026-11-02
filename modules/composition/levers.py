"""The registry of output levers: what a lever is called, what it may point at, what shape its value has and its default.

A new lever is one `register` call: storage, inheritance, the commands of settings and the window read this registry
and change nothing. The window draws a lever by its target and the shape of its value (`core.contracts.LeverSpec`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.contracts import (LEVER_FORMS, REFERENCE_CHOICE, VARIANT_ALLOW, VOICE_PLAYER, VOICE_TAKE, LeverSpec, TargetKind, ValueShape,
                            parse_group_target, parse_variant_target)


def _no_sources(value: Any) -> frozenset[int]:
    return frozenset()


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


def _is_variant_pick(value: Any) -> bool:
    return _is_pick(value, {"source_id": _is_id, "player": _is_name})


def _picked_source(value: Any) -> frozenset[int]:
    return frozenset({value["source_id"]}) if isinstance(value, dict) and _is_id(value.get("source_id")) else frozenset()


# What a value is, by its shape: the one place that says which values a lever of a shape takes.
_ACCEPTS: dict[ValueShape, Callable[[Any], bool]] = {
    ValueShape.FLAG: _is_flag,
    ValueShape.VARIANT_PICK: _is_variant_pick,
    ValueShape.REFERENCE_PICK: _is_reference_choice,
}


@dataclass(frozen=True)
class Lever:
    """`consumer` is the module that acts on the lever.

    `value_sources` names the sources of the series a value points at, so that a removed source takes the records
    that mention it, whatever shape the value of a lever has."""

    name: str
    target: TargetKind
    shape: ValueShape
    default: Any
    consumer: str
    value_sources: Callable[[Any], frozenset[int]] = _no_sources

    def accepts(self, value: Any) -> bool:
        """Whether a value is one of the lever's values."""
        return _ACCEPTS[self.shape](value)

    def accepts_target(self, target: str | None) -> bool:
        """Whether `target` is the form this lever points at; a lever without a target takes none."""
        if self.target == TargetKind.NONE:
            return target is None
        if not isinstance(target, str):
            return False
        if self.target == TargetKind.VARIANT:
            return parse_variant_target(target) is not None
        return parse_group_target(target) is not None

    def sources_of(self, target: str | None, value: Any) -> frozenset[int]:
        """The sources of the series a record of this lever refers to, by its target and by its value."""
        found = self.value_sources(value)
        variant = parse_variant_target(target) if self.target == TargetKind.VARIANT and isinstance(target, str) else None
        return found | {variant[1]} if variant is not None else found

    def spec(self) -> LeverSpec:
        return LeverSpec(name=self.name, target=self.target, shape=self.shape, default=self.default,
                         consumer=self.consumer)


_levers: dict[str, Lever] = {}


def register(lever: Lever) -> None:
    """Adds a lever; a name is taken once, so two owners cannot give one name two meanings."""
    if lever.name in _levers:
        raise ValueError(f"lever {lever.name!r} is already registered")
    if (lever.target, lever.shape) not in LEVER_FORMS:
        raise ValueError(f"lever {lever.name!r}: no window draws a lever with the target {lever.target} and the value {lever.shape}")
    _levers[lever.name] = lever


def unregister(name: str) -> None:
    """Takes a lever out of the registry; its stored records stay and are ignored until it comes back."""
    _levers.pop(name, None)


def get(name: str) -> Lever | None:
    return _levers.get(name)


def all() -> list[Lever]:
    """Every lever in the order of registration."""
    return list(_levers.values())


def specs() -> list[LeverSpec]:
    """The descriptions of the levers, in the order of registration: what a window is given to draw them."""
    return [lever.spec() for lever in _levers.values()]


register(Lever(REFERENCE_CHOICE, TargetKind.NONE, ValueShape.REFERENCE_PICK, None, "scan", _picked_source))
register(Lever(VARIANT_ALLOW, TargetKind.VARIANT, ValueShape.FLAG, True, "scan"))
register(Lever(VOICE_TAKE, TargetKind.GROUP, ValueShape.FLAG, True, "voices"))
register(Lever(VOICE_PLAYER, TargetKind.GROUP, ValueShape.VARIANT_PICK, None, "voices", _picked_source))
