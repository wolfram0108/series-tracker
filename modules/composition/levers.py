"""The registry of output levers: what a lever is called, what it may point at, what shape its value has and its default.

A new lever is one `register` call: storage, inheritance, the commands of settings and the window read this registry
and change nothing. The window draws a lever by its target and the shape of its value (`core.contracts.LeverSpec`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from core import _native
from core.contracts import (COMPILATION_MEMBER, COMPILATION_TAKE, LEVER_FORMS, MARKUP_ALLOW, MARKUP_ORDER, MARKUP_SAME,
                            REFERENCE_ALLOW, REFERENCE_ORDER, REFERENCE_SAME, SUBTITLES_ALLOW,
                            SUBTITLES_ORDER, VOICE_ALLOW,
                            VOICE_ORDER, VOICE_TAKE, Level, LeverSpec, Queue, TargetKind, ValueShape,
                            parse_compilation_key, parse_variant_target)


def _no_sources(value: Any) -> frozenset[int]:
    return frozenset()


def _is_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _sets_sources(value: Any) -> frozenset[int]:
    return frozenset(each["source_id"] for group in value if isinstance(group, list) for each in group
                     if isinstance(each, dict) and _is_id(each.get("source_id"))) if isinstance(value, list) else frozenset()


def _order_sources(value: Any) -> frozenset[int]:
    return frozenset(each["source_id"] for each in value if isinstance(each, dict) and _is_id(each.get("source_id"))) \
        if isinstance(value, list) else frozenset()


def _compilation_sources(value: Any) -> frozenset[int]:
    found = parse_compilation_key(value)
    return frozenset({found[1]}) if found is not None else frozenset()


@dataclass(frozen=True)
class Lever:
    """`consumer` is the module that acts on the lever, `queue` the queue it acts on, `levels` the levels it may be set on
    (none: every level).

    `value_sources` names the sources of the series a value points at, so that a removed source leaves the records
    that mention it, whatever shape the value of a lever has. What a value of its shape is and what a target of its kind is
    are told by the core (native/composition levers): the one place of it, for what is written and for what is read."""

    name: str
    target: TargetKind
    shape: ValueShape
    default: Any
    consumer: str
    queue: Queue
    value_sources: Callable[[Any], frozenset[int]] = _no_sources
    levels: frozenset[Level] | None = None

    def accepts(self, value: Any) -> bool:
        """Whether a value is one of the lever's values."""
        try:
            written = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return False  # what JSON cannot hold is no value of any lever
        return _native.lever_accepts(str(self.target), str(self.shape), written)

    def accepts_target(self, target: str | None) -> bool:
        """Whether `target` is the form this lever points at; a lever without a target takes none."""
        if target is not None and not isinstance(target, str):
            return False
        return _native.lever_accepts_target(str(self.target), target)

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
    _native.lever_register(lever.name, str(lever.target), str(lever.shape), json.dumps(lever.default, ensure_ascii=False),
                           sorted(str(each) for each in lever.levels) if lever.levels is not None else None)


def unregister(name: str) -> None:
    """Takes a lever out of the registry; its stored records stay and are ignored until it comes back."""
    _levers.pop(name, None)
    _native.lever_unregister(name)


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
               _compilation_sources, levels=frozenset({Level.EPISODE})))
register(Lever(COMPILATION_TAKE, TargetKind.NONE, ValueShape.FLAG, True, "composition", Queue.COMPILATION,
               levels=frozenset({Level.COMPILATION})))
# Stage 14: the queue of the markup reference, of the form of the queue of the reference.
register(Lever(MARKUP_ORDER, TargetKind.NONE, ValueShape.VARIANT_ORDER, [], "planner", Queue.MARKUP, _order_sources))
register(Lever(MARKUP_ALLOW, TargetKind.VARIANT, ValueShape.FLAG, True, "planner", Queue.MARKUP))
# Р-99: the variants the user declared equal take one place in the queue; what the file holds stays while it is as good as
# the queue's first.
register(Lever(REFERENCE_SAME, TargetKind.NONE, ValueShape.VARIANT_SETS, [], "planner", Queue.REFERENCE, _sets_sources))
register(Lever(MARKUP_SAME, TargetKind.NONE, ValueShape.VARIANT_SETS, [], "planner", Queue.MARKUP, _sets_sources))
# Stage 15 (DESIGN.md 7.1.1): the subtitles of the container — every allowed variant in this order; a variant is named by
# the name of its subtitles in place of a voice-over group.
# The subtitles have a table on the series, on a season and on an episode, none of a compilation of their own.
_SUBTITLE_LEVELS = frozenset({Level.SERIES, Level.SEASON, Level.EPISODE})
register(Lever(SUBTITLES_ORDER, TargetKind.NONE, ValueShape.VARIANT_ORDER, [], "planner", Queue.SUBTITLES, _order_sources,
               levels=_SUBTITLE_LEVELS))
register(Lever(SUBTITLES_ALLOW, TargetKind.VARIANT, ValueShape.FLAG, True, "planner", Queue.SUBTITLES,
               levels=_SUBTITLE_LEVELS))
# How the track of a variant lies in the output container: the one a player shows by itself, the forced one.
