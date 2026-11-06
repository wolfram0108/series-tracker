"""The order in which the variants of an episode are tried: for its reference and for the voice-over of each group.

One rule for every reader — the choice of the reference (scan), the plan of voice-overs (voices) and the window that shows
the queues — so that the queue a window shows is the one the core takes. Pure functions over rows of the layout.

The reference: the best resolution, then the place of the player in the order of players of the settings, then the place
of the source in the series, then the place of the row in the layout. A voice-over: the place of the player, then the
place of the row in the layout (the layout lists the rows source by source, so the place of a row holds the order of the
sources). The user's own queue goes first: the variants it names, in its order, then the others by the rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Hashable, Iterable, Sequence

from core.contracts import CompositionRow
from core.voice_names import NO_GROUPS, Groups, rank


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
        return rank(player, self.players, self.player_groups)

    def source_place(self, source_id: int) -> int:
        return self.sources.get(source_id, len(self.sources))


RankKey = Callable[[Candidate, Order], int]


def by_resolution(candidate: Candidate, order: Order) -> int:
    return -(candidate.row.height or 0)


def by_player(candidate: Candidate, order: Order) -> int:
    return order.player_place(candidate.row.player)


def by_source(candidate: Candidate, order: Order) -> int:
    return order.source_place(candidate.row.source_id)


# The criteria, the strongest first; a new criterion is one more entry of a list.
REFERENCE_RANKING: tuple[RankKey, ...] = (by_resolution, by_player, by_source)
VOICE_RANKING: tuple[RankKey, ...] = (by_player,)


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


def best(candidates: Iterable[Candidate], order: Order, ranking: Sequence[RankKey]) -> Candidate:
    """The first candidate by the rule alone; the pool must not be empty."""
    return min(candidates, key=lambda candidate: (*(each(candidate, order) for each in ranking), candidate.position))
