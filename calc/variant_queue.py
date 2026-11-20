"""The order in which the variants of an episode are tried: for its reference and for the voice-over of each group.

One rule for every reader — the choice of the reference (scan), the plan of voice-overs (voices) and the window that shows
the queues — so that the queue a window shows is the one the core takes. Pure functions over rows of the layout.

The reference: the best resolution, then the place of the player in the order of players of the settings, then the place
of the source in the series, then the place of the row in the layout. A voice-over: the best resolution, then the place of
the player, then the place of the row in the layout (the layout lists the rows source by source, so the place of a row
holds the order of the sources) — the owner's rule (stage 10): the resolution decides, the player after it. The user's own
queue goes first: the variants it names, in its order, then the others by the rule.

Variants the user declared equal (`Same`, Р-99) take one place: the best that any of them would have. They differ only in
their resolution, which still decides first, and in what the asset holds: of equals what it holds stays (a video goes
only for a better one), and with nothing held the first of them by the rule is taken.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Callable, Hashable, Iterable, Sequence

from core.contracts import CompositionRow
from calc.voice_names import NO_GROUPS, Groups, PlayerOrder


@dataclass(frozen=True)
class Candidate:
    """A row of the layout that may be taken. `position` is its place in the layout, the last tie-break."""

    row: CompositionRow
    position: int


@dataclass(frozen=True)
class Order:
    """What the keys of the rule compare against: the order of players of the settings and of the sources of the series."""

    players: tuple[str, ...] = ()
    sources: dict[int, int] = field(default_factory=dict)
    player_groups: Groups = NO_GROUPS

    def player_place(self, player: str) -> int:
        """Place in the order of the settings; players of one group of the user take one place, an unknown one is last."""
        return self._players.place(player)

    @cached_property
    def _players(self) -> PlayerOrder:
        return PlayerOrder.of(self.players, self.player_groups)

    def source_place(self, source_id: int) -> int:
        return self.sources.get(source_id, len(self.sources))


RankKey = Callable[[Candidate, Order], int]


def by_resolution(candidate: Candidate, order: Order) -> int:
    return -(candidate.row.height or 0)


def by_player(candidate: Candidate, order: Order) -> int:
    return order.player_place(candidate.row.player)


def by_source(candidate: Candidate, order: Order) -> int:
    return order.source_place(candidate.row.source_id)


# The criteria, the strongest first; a new criterion is one more entry of a list. Those that belong to a variant (its player
# and its source), not to the line it plays (its resolution), are what a group of equal variants makes one.
VARIANT_RANKING: tuple[RankKey, ...] = (by_player, by_source)
REFERENCE_RANKING: tuple[RankKey, ...] = (by_resolution, *VARIANT_RANKING)
VOICE_RANKING: tuple[RankKey, ...] = (by_resolution, by_player)

Variant = tuple[str, int, str]  # (voice-over group, source id, player)


@dataclass(frozen=True)
class Line:
    """What the criteria read of a variant when there is no row of the layout to ask: a variant the user named that a
    level does not list, a variant a file holds."""

    height: int
    player: str
    source_id: int


@dataclass(frozen=True)
class Same:
    """The groups of variants the user declared equal on a level; a variant is in one group at most."""

    groups: tuple[frozenset[Variant], ...] = ()

    @classmethod
    def of(cls, value: object) -> "Same":
        """The groups of the value of a lever: a list of lists of `{group, source_id, player}`; what is not one is skipped."""
        found: list[frozenset[Variant]] = []
        for each in value if isinstance(value, list) else ():
            members = frozenset(
                (item["group"], item["source_id"], item["player"]) for item in each if isinstance(item, dict)
                and isinstance(item.get("group"), str) and isinstance(item.get("source_id"), int)
                and isinstance(item.get("player"), str)) if isinstance(each, list) else frozenset()
            if len(members) > 1 and not any(members & other for other in found):
                found.append(members)
        return cls(tuple(found))

    def members(self, variant: Variant) -> frozenset[Variant]:
        """The variants that stand where `variant` stands: its group, or itself."""
        return next((group for group in self.groups if variant in group), frozenset((variant,)))

    def number_of(self, variant: Variant) -> int | None:
        return next((place for place, group in enumerate(self.groups, 1) if variant in group), None)


NO_SAME = Same()


def standing(variant: Variant, same: Same, named: Callable[[Variant], int], rule: Callable[[Variant], tuple]) -> tuple:
    """The place of a variant in a queue before its resolution: (the place the user's order gives it, what the rule says of
    its variant). A member of a group of equals stands where the best of its group does, so the group is one place."""
    return min((named(each), *rule(each)) for each in same.members(variant))


def variant_rule(order: Order) -> Callable[[Variant], tuple]:
    """What the rule says of a variant by itself: its player and its source, as `VARIANT_RANKING` reads them."""
    return lambda variant: tuple(each(Candidate(Line(0, variant[2], variant[1]), 0), order) for each in VARIANT_RANKING)


def ranked(candidates: Iterable[Candidate], order: Order, ranking: Sequence[RankKey],
           preferred: Sequence[Hashable] = (), identity: Callable[[Candidate], Hashable] | None = None) -> list[Candidate]:
    """The candidates in the order they are tried. Those whose `identity` is in `preferred` come first, in its order;
    the rest follow by `ranking`, then by their place in the layout."""
    places = {each: place for place, each in reversed(list(enumerate(preferred)))}
    unnamed = len(places)

    def key(candidate: Candidate) -> tuple:
        named = places.get(identity(candidate), unnamed) if identity is not None and places else unnamed
        return (named, *(each(candidate, order) for each in ranking), candidate.position)

    return sorted(candidates, key=key)


def ranked_singles(candidates: Iterable[Candidate], order: Order, preferred: Sequence[Variant] = (),
                   same: Same = NO_SAME) -> list[Candidate]:
    """The variants of the queue of the reference in the order they are tried: the place of the variant (the user's order,
    its player, its source — a group of equals as one place), then its resolution, then its place in the layout."""
    places = {each: place for place, each in reversed(list(enumerate(preferred)))}
    unnamed = len(places)
    rule = variant_rule(order)

    def key(candidate: Candidate) -> tuple:
        variant = (candidate.row.voice_label, candidate.row.source_id, candidate.row.player)
        named, *by_variant = standing(variant, same, lambda each: places.get(each, unnamed), rule)
        return (named, by_resolution(candidate, order), *by_variant, candidate.position)

    return sorted(candidates, key=key)


def best(candidates: Iterable[Candidate], order: Order, ranking: Sequence[RankKey]) -> Candidate:
    """The first candidate by the rule alone; the pool must not be empty."""
    return min(candidates, key=lambda candidate: (*(each(candidate, order) for each in ranking), candidate.position))
