"""The choice of the reference of every episode of a series from its layout.

A pure function of the rows of the layout, the levers of every episode and the order of players: no bus, no database,
no clock, so equal inputs give equal answers. The reference is a single episode, never a compilation: a compilation
only tells why an episode has no reference (`unfit`).

A reference whose row has an unsure answer (`not_obtained`, which is not `absent`) is held, so that a probe that failed
once does not swap the reference and the file for a worse one and back; a pick of the user is stronger than the hold.

A slot is (season, episode, part): the pieces of one episode that are different videos. Of the playable single rows
of a slot, whose player takes part in the choice, the best by `RANKING` is the reference. A voice-over picked by the
user narrows the candidates to that voice-over and is never replaced by the best of another one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from pydantic import ValidationError

from core.contracts import (REFERENCE_ALLOW, REFERENCE_CHOICE, CompositionRow, EffectiveLever, EpisodeSettings, Level,
                            Reference, ReferenceChoice, ReferenceMark, RowState, parse_player_target)
from core.reasons import reason
from core.voice_names import NO_GROUPS, Groups, rank

Slot = tuple[int, int, int | None]


@dataclass(frozen=True)
class Candidate:
    """A row of the layout that may become the reference. `position` is its place in the layout, the last tie-break."""

    row: CompositionRow
    position: int


@dataclass(frozen=True)
class Order:
    """What the keys of the ranking compare against: the order of players and the order of the sources of the series."""

    players: tuple[str, ...] = ()
    sources: dict[int, int] = field(default_factory=dict)
    player_groups: Groups = NO_GROUPS

    def player_place(self, player: str) -> int:
        """Place in the order of the settings (the one order the voice-overs follow too, `core.voice_names.rank`)."""
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


# The one list of the criteria of the choice, the strongest first; a new criterion is one more entry here.
RANKING: tuple[RankKey, ...] = (by_resolution, by_player, by_source)


@dataclass(frozen=True)
class Chosen:
    """The outcome for one slot. `row` is the layout row of the reference; None for the marks `none` and `unfit`."""

    reference: Reference
    row: CompositionRow | None = None

    @property
    def slot(self) -> Slot:
        return self.reference.season, self.reference.episode, self.reference.part


@dataclass(frozen=True)
class _Levers:
    """What the levers of an episode say to the choice."""

    choice: ReferenceChoice | None = None
    own: bool = False
    denied: frozenset[tuple[int, str]] = frozenset()

    def allows(self, row: CompositionRow) -> bool:
        return (row.source_id, row.player) not in self.denied


def choose(rows: Sequence[CompositionRow], settings: Iterable[EpisodeSettings], players: Sequence[str],
           sources: Sequence[int], ranking: Sequence[RankKey] = RANKING,
           previous: Iterable[Reference] = (), player_groups: Groups = NO_GROUPS) -> list[Chosen]:
    """One outcome per slot of every episode that has a row in the layout, in the order of season, episode, part.

    `players` is the order of players of the settings, best first; `sources` the ids of the sources of the series in
    their order; `previous` the references chosen last time, of which a slot may hold on to its own. An episode with no
    row in the layout has no outcome."""
    order = Order(tuple(players), {source_id: place for place, source_id in enumerate(sources)}, player_groups)
    singles: dict[tuple[int, int], list[Candidate]] = {}
    compilations: dict[tuple[int, int], list[Candidate]] = {}
    for position, row in enumerate(rows):
        if row.excluded:
            continue  # a row that is not excluded has its numbers: the layout gives it a season and an episode
        end = row.episode_start if row.episode_end is None else row.episode_end
        if end < row.episode_start:
            continue  # a reversed range is no episode and no compilation: the title was read wrongly
        candidate = Candidate(row, position)
        if end > row.episode_start:
            for episode in range(row.episode_start, end + 1):
                compilations.setdefault((row.season, episode), []).append(candidate)
        else:
            singles.setdefault((row.season, row.episode_start), []).append(candidate)
    levers = {(each.season, each.episode): _levers_of(each.levers) for each in settings}
    held = {(each.season, each.episode, each.part): each for each in previous
            if each.source_id is not None and each.row_key is not None}
    chosen: list[Chosen] = []
    for season, episode in sorted(set(singles) | set(compilations)):
        here = singles.get((season, episode), [])
        parts = sorted({candidate.row.part for candidate in here}, key=lambda part: (part is not None, part or 0))
        for part in parts or [None]:
            slot = [candidate for candidate in here if candidate.row.part == part]
            chosen.append(_decide(season, episode, part, slot, compilations.get((season, episode), []),
                                  levers.get((season, episode), _Levers()), order, ranking,
                                  held.get((season, episode, part))))
    return chosen


def _decide(season: int, episode: int, part: int | None, singles: list[Candidate], compilations: list[Candidate],
            levers: _Levers, order: Order, ranking: Sequence[RankKey], held: Reference | None) -> Chosen:
    if levers.choice is None and held is not None:
        unsure = next((candidate.row for candidate in singles
                       if (candidate.row.source_id, candidate.row.row_key) == (held.source_id, held.row_key)
                       and candidate.row.state == RowState.NOT_OBTAINED and levers.allows(candidate.row)), None)
        if unsure is not None:
            # The mark is the one the levers of the episode give now: the same as before unless they changed.
            return _taken(season, episode, part, unsure, ReferenceMark.OWN if levers.own else ReferenceMark.BY_LEVEL,
                          height=unsure.height or held.height)
    voice = levers.choice.group if levers.choice else None
    playable = [candidate for candidate in singles if candidate.row.state == RowState.PLAYABLE
                and levers.allows(candidate.row)]
    pool = [candidate for candidate in playable if voice is None or candidate.row.voice_label == voice]
    if not pool:
        return _without(season, episode, part, compilations, levers, voice)
    if levers.choice is not None:
        picked = [candidate for candidate in pool if candidate.row.source_id == levers.choice.source_id
                  and candidate.row.player == levers.choice.player]
        if not picked:
            best = _best(pool, order, ranking).row
            why = reason("reference_other_player", taken=best.player, missing=levers.choice.player,
                         taken_source=best.source_id, missing_source=levers.choice.source_id)
            return _taken(season, episode, part, best, ReferenceMark.OTHER_PLAYER, why)
        pool = picked
    best = _best(pool, order, ranking).row
    return _taken(season, episode, part, best, ReferenceMark.OWN if levers.own else ReferenceMark.BY_LEVEL)


def _without(season: int, episode: int, part: int | None, compilations: list[Candidate], levers: _Levers,
             voice: str | None) -> Chosen:
    """No single row can be the reference: say whether a compilation holds the episode, or the picked voice-over has
    nothing, or there is nothing. The picked voice-over is never swapped for another one."""
    covering = [candidate for candidate in compilations if candidate.row.state == RowState.PLAYABLE
                and levers.allows(candidate.row) and (voice is None or candidate.row.voice_label == voice)]
    if covering:
        mark, why = ReferenceMark.UNFIT, reason("reference_unfit")
    elif voice is not None:
        mark, why = ReferenceMark.NONE, reason("reference_voice_missing", voice=voice, season=season, episode=episode)
    else:
        mark, why = ReferenceMark.NONE, reason("reference_none")
    return Chosen(Reference(season=season, episode=episode, part=part, mark=mark, reason=why))


def _taken(season: int, episode: int, part: int | None, row: CompositionRow, mark: ReferenceMark,
           why: str | None = None, height: int | None = None) -> Chosen:
    reference = Reference(season=season, episode=episode, part=part, mark=mark, source_id=row.source_id,
                          row_key=row.row_key, player=row.player, voice_group=row.voice_label,
                          height=height if height is not None else row.height, reason=why)
    return Chosen(reference, row)


def _best(pool: list[Candidate], order: Order, ranking: Sequence[RankKey]) -> Candidate:
    return min(pool, key=lambda candidate: (*(key(candidate, order) for key in ranking), candidate.position))


def _levers_of(levers: Iterable[EffectiveLever]) -> _Levers:
    choice, own = None, False
    denied: set[tuple[int, str]] = set()
    for lever in levers:
        if lever.lever == REFERENCE_CHOICE:
            choice = _choice_of(lever.value)
            own = own or lever.level == Level.EPISODE
        elif lever.lever == REFERENCE_ALLOW:
            target = parse_player_target(lever.target) if isinstance(lever.target, str) else None
            if target is not None and lever.value is False:
                denied.add(target)
            own = own or lever.level == Level.EPISODE
    return _Levers(choice, own, frozenset(denied))


def _choice_of(value) -> ReferenceChoice | None:
    """The pick of the user; a value that is not one (the registry refuses such, but a record may be older) is auto."""
    if value is None:
        return None
    try:
        return ReferenceChoice.model_validate(value)
    except ValidationError:
        return None
