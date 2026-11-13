"""The registry of output levers: what a lever is called, what it may point at, what shape its value has and its default.

A new lever is one `register` call: storage, inheritance, the commands of settings and the window read this registry
and change nothing. The window draws a lever by its target and the shape of its value (`core.contracts.LeverSpec`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.contracts import (COMPILATION_MEMBER, COMPILATION_TAKE, LEVER_FORMS, MARKUP_ALLOW, MARKUP_ORDER, MARKUP_SAME,
                            REFERENCE_ALLOW, REFERENCE_ORDER, REFERENCE_SAME, VOICE_ALLOW,
                            VOICE_ORDER, VOICE_TAKE, LeverSpec, Queue, TargetKind, ValueShape, parse_compilation_key,
                            parse_group_target, parse_variant_target)


def _no_sources(value: Any) -> frozenset[int]:
    return frozenset()


def _is_flag(value: Any) -> bool:
    return isinstance(value, bool)


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_item(value: Any, shape: dict[str, Callable[[Any], bool]]) -> bool:
    """A dict that has exactly the fields of `shape`, each of its kind."""
    if not isinstance(value, dict) or set(value) != set(shape):
        return False
    # No builtin `all` here: this module's own `all` is the registry listing.
    return not [key for key, check in shape.items() if not check(value[key])]


# An item of an order names its variant by what the target of the lever does not: the group too when there is no target.
_ITEMS: dict[TargetKind, dict[str, Callable[[Any], bool]]] = {
    TargetKind.NONE: {"group": _is_name, "source_id": _is_id, "player": _is_name},
    TargetKind.GROUP: {"source_id": _is_id, "player": _is_name},
}


def _is_order(value: Any, target: TargetKind) -> bool:
    """A list of distinct variants in the form the target asks for; an empty list is the order of the rule."""
    if not isinstance(value, list) or target not in _ITEMS:
        return False
    items = [tuple(sorted(each.items())) for each in value if _is_item(each, _ITEMS[target])]
    return len(items) == len(value) == len(set(items))


def _is_sets(value: Any, target: TargetKind) -> bool:
    """Groups of variants declared equal: a list of lists of two variants or more, no variant in two places; an empty list is
    no group. A variant is named by its voice-over group too, as in an order of the reference."""
    if target != TargetKind.NONE or not isinstance(value, list):
        return False
    seen: set[tuple] = set()
    for group in value:
        if not isinstance(group, list) or len(group) < 2:
            return False
        for item in group:
            if not _is_item(item, _ITEMS[TargetKind.NONE]):
                return False
            key = tuple(sorted(item.items()))
            if key in seen:
                return False
            seen.add(key)
    return True


def _sets_sources(value: Any) -> frozenset[int]:
    return frozenset(each["source_id"] for group in value if isinstance(group, list) for each in group
                     if isinstance(each, dict) and _is_id(each.get("source_id"))) if isinstance(value, list) else frozenset()


def _order_sources(value: Any) -> frozenset[int]:
    return frozenset(each["source_id"] for each in value if isinstance(each, dict) and _is_id(each.get("source_id"))) \
        if isinstance(value, list) else frozenset()


def _compilation_sources(value: Any) -> frozenset[int]:
    found = parse_compilation_key(value)
    return frozenset({found[1]}) if found is not None else frozenset()


# What a value is, by its shape (and the target, for the form of an item): the one place that says which values a lever takes.
_ACCEPTS: dict[ValueShape, Callable[[Any, TargetKind], bool]] = {
    ValueShape.FLAG: lambda value, target: _is_flag(value),
    ValueShape.VARIANT_ORDER: _is_order,
    ValueShape.VARIANT_SETS: _is_sets,
    # The key of a compilation, or "" for none: the episode stands in no compilation by the user's word.
    ValueShape.COMPILATION: lambda value, target: value == "" or parse_compilation_key(value) is not None,
}


@dataclass(frozen=True)
class Lever:
    """`consumer` is the module that acts on the lever, `queue` the queue it acts on.

    `value_sources` names the sources of the series a value points at, so that a removed source leaves the records
    that mention it, whatever shape the value of a lever has."""

    name: str
    target: TargetKind
    shape: ValueShape
    default: Any
    consumer: str
    queue: Queue
    value_sources: Callable[[Any], frozenset[int]] = _no_sources

    def accepts(self, value: Any) -> bool:
        """Whether a value is one of the lever's values."""
        return _ACCEPTS[self.shape](value, self.target)

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
                         consumer=self.consumer, queue=self.queue)


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


register(Lever(REFERENCE_ORDER, TargetKind.NONE, ValueShape.VARIANT_ORDER, [], "scan", Queue.REFERENCE, _order_sources))
register(Lever(REFERENCE_ALLOW, TargetKind.VARIANT, ValueShape.FLAG, True, "scan", Queue.REFERENCE))
register(Lever(VOICE_TAKE, TargetKind.GROUP, ValueShape.FLAG, True, "voices", Queue.VOICE))
register(Lever(VOICE_ORDER, TargetKind.GROUP, ValueShape.VARIANT_ORDER, [], "voices", Queue.VOICE, _order_sources))
register(Lever(VOICE_ALLOW, TargetKind.VARIANT, ValueShape.FLAG, True, "voices", Queue.VOICE))
# Stage 12: no record of the membership of an episode is the rule (None), a record is the user's word.
register(Lever(COMPILATION_MEMBER, TargetKind.NONE, ValueShape.COMPILATION, None, "composition", Queue.COMPILATION,
               _compilation_sources))
register(Lever(COMPILATION_TAKE, TargetKind.NONE, ValueShape.FLAG, True, "composition", Queue.COMPILATION))
# Stage 14: the queue of the markup reference, of the form of the queue of the reference.
register(Lever(MARKUP_ORDER, TargetKind.NONE, ValueShape.VARIANT_ORDER, [], "planner", Queue.MARKUP, _order_sources))
register(Lever(MARKUP_ALLOW, TargetKind.VARIANT, ValueShape.FLAG, True, "planner", Queue.MARKUP))
# Р-99: the variants the user declared equal take one place in the queue; what the file holds stays while it is as good as
# the queue's first.
register(Lever(REFERENCE_SAME, TargetKind.NONE, ValueShape.VARIANT_SETS, [], "planner", Queue.REFERENCE, _sets_sources))
register(Lever(MARKUP_SAME, TargetKind.NONE, ValueShape.VARIANT_SETS, [], "planner", Queue.MARKUP, _sets_sources))
