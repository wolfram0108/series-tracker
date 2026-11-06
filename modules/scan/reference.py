"""The choice of the reference of every episode of a series from its layout.

A pure function of the rows of the layout, the levers of every episode and the order of players: no bus, no database,
no clock, so equal inputs give equal answers. The reference is a single episode, never a compilation: a compilation
only tells why an episode has no reference (`unfit`).

A reference whose row has an unsure answer (`not_obtained`, which is not `absent`) is held, so that a probe that failed
once does not swap the reference and the file for a worse one and back; a pick of the user is stronger than the hold.
So is a reference whose file is on disk and whose row the layout does not have at all: a read that did not bring a row
proves nothing (a page read short, a video VK hid from the listing of its channel), and the file is not swapped on it; nor does a
row with no number. A row the layout numbers otherwise, or one the configuration took out (`CompositionRow.left_out`),
is its word: the reference follows it.

A slot is (season, episode, part): the pieces of one episode that are different videos. Of the playable single rows
of a slot, whose player takes part in the choice, the best by `RANKING` is the reference. A voice-over picked by the
user narrows the candidates to that voice-over and is never replaced by the best of another one.

A video whose download failed for good (`failed`: its source, row and link) is passed over: the next by `RANKING` is the
reference, with the mark of another player and the reason (ЭТАП_10: «если скачивание не удаётся, то выбирается плеер по
следующему приоритету»). When every video of the episode failed there is no next one: the best stays, with the reason,
and is tried again. The failure is of that video: the same row showing another video is a candidate again.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Collection, Iterable, Sequence

from pydantic import ValidationError

from core.contracts import (REFERENCE_CHOICE, VARIANT_ALLOW, CompositionRow, EffectiveLever, EpisodeSettings, Level,
                            Reference, ReferenceChoice, ReferenceMark, RowState, parse_variant_target)
from core.reasons import reason
from core.voice_names import NO_GROUPS, Groups, rank

Slot = tuple[int, int, int | None]
# A video whose download failed: (source id, row key, link of the row).
Failed = tuple[int, str, str]


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
    """The outcome for one slot. `row` is the layout row of the reference; None for the marks `none` and `unfit`, and for
    a reference held while the layout does not have its row (its file stands for the slot)."""

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
    denied: frozenset[tuple[str, int, str]] = frozenset()  # (group, source id, player) the user left out

    def allows(self, row: CompositionRow) -> bool:
        return (row.voice_label, row.source_id, row.player) not in self.denied

    def allows_reference(self, reference: Reference) -> bool:
        return (reference.voice_group or "", reference.source_id, reference.player) not in self.denied


def choose(rows: Sequence[CompositionRow], settings: Iterable[EpisodeSettings], players: Sequence[str],
           sources: Sequence[int], ranking: Sequence[RankKey] = RANKING,
           previous: Iterable[Reference] = (), player_groups: Groups = NO_GROUPS,
           on_disk: Collection[tuple[int, str]] = (), failed: Collection[Failed] = ()) -> list[Chosen]:
    """One outcome per slot of every episode that has a row in the layout, in the order of season, episode, part.

    `players` is the order of players of the settings, best first; `sources` the ids of the sources of the series in
    their order; `previous` the references chosen last time, of which a slot may hold on to its own. `on_disk` are the
    rows (source id, row key) whose video is downloaded; a previous reference of one of them that the layout does not
    have is held, with no row (`Chosen.row` is None). An episode with no row in the layout and none held has no outcome.
    `rows` may hold the excluded rows: they are never candidates, but they tell a row taken out from a row not brought."""
    order = Order(tuple(players), {source_id: place for place, source_id in enumerate(sources)}, player_groups)
    singles: dict[tuple[int, int], list[Candidate]] = {}
    compilations: dict[tuple[int, int], list[Candidate]] = {}
    seen = {(row.source_id, row.row_key) for row in rows if row.decided}
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
    kept = {slot: each for slot, each in held.items()
            if (each.source_id, each.row_key) not in seen and (each.source_id, each.row_key) in on_disk
            and each.source_id in order.sources}
    chosen: list[Chosen] = []
    for season, episode in sorted(set(singles) | set(compilations) | {(slot[0], slot[1]) for slot in kept}):
        here = singles.get((season, episode), [])
        parts = {candidate.row.part for candidate in here} | {slot[2] for slot in kept if slot[:2] == (season, episode)}
        for part in sorted(parts, key=lambda part: (part is not None, part or 0)) or [None]:
            slot = [candidate for candidate in here if candidate.row.part == part]
            episode_levers = levers.get((season, episode), _Levers())
            gone = kept.get((season, episode, part))
            if gone is not None and episode_levers.choice is None and episode_levers.allows_reference(gone):
                chosen.append(_held(gone, episode_levers))
                continue
            chosen.append(_decide(season, episode, part, slot, compilations.get((season, episode), []),
                                  episode_levers, order, ranking, held.get((season, episode, part)), set(failed)))
    return chosen


def _held(previous: Reference, levers: _Levers) -> Chosen:
    """The previous reference kept with no row of the layout: its file stands for the slot as it stood before."""
    mark = ReferenceMark.OWN if levers.own else ReferenceMark.BY_LEVEL
    return Chosen(previous.model_copy(update={"mark": mark, "reason": None}))


def _decide(season: int, episode: int, part: int | None, singles: list[Candidate], compilations: list[Candidate],
            levers: _Levers, order: Order, ranking: Sequence[RankKey], held: Reference | None,
            failed: Collection[Failed] = frozenset()) -> Chosen:
    def lost(candidate: Candidate) -> bool:
        return (candidate.row.source_id, candidate.row.row_key, candidate.row.link) in failed

    if levers.choice is None and held is not None:
        unsure = next((candidate.row for candidate in singles
                       if (candidate.row.source_id, candidate.row.row_key) == (held.source_id, held.row_key)
                       and candidate.row.state == RowState.NOT_OBTAINED and levers.allows(candidate.row)
                       and not lost(candidate)), None)
        if unsure is not None:
            # The mark is the one the levers of the episode give now: the same as before unless they changed.
            return _taken(season, episode, part, unsure, ReferenceMark.OWN if levers.own else ReferenceMark.BY_LEVEL,
                          height=unsure.height or held.height)
    voice = levers.choice.group if levers.choice else None
    allowed = [candidate for candidate in singles if candidate.row.state == RowState.PLAYABLE
               and levers.allows(candidate.row) and (voice is None or candidate.row.voice_label == voice)]
    pool = [candidate for candidate in allowed if not lost(candidate)]
    failures = [candidate for candidate in allowed if lost(candidate)]
    if not pool and failures:
        # Every video of the episode failed: there is no next one, so the best stays and the next sync tries it again
        # (a failure that passes, as a host not resolved yet, is not a reason to leave the episode without a video).
        players = ", ".join(sorted({candidate.row.player for candidate in failures}))
        best = _best(failures, order, ranking).row
        return _taken(season, episode, part, best, ReferenceMark.OWN if levers.own else ReferenceMark.BY_LEVEL,
                      reason("reference_download_failed_all", players=players))
    if not pool:
        return _without(season, episode, part, compilations, levers, voice)
    if levers.choice is not None:
        picked = [candidate for candidate in pool if candidate.row.source_id == levers.choice.source_id
                  and candidate.row.player == levers.choice.player]
        if not picked:
            best = _best(pool, order, ranking).row
            gave_up = next((candidate.row for candidate in failures if candidate.row.source_id == levers.choice.source_id
                            and candidate.row.player == levers.choice.player), None)
            why = (reason("reference_download_failed", taken=best.player, failed=gave_up.player,
                          taken_source=best.source_id, failed_source=gave_up.source_id) if gave_up is not None else
                   reason("reference_other_player", taken=best.player, missing=levers.choice.player,
                          taken_source=best.source_id, missing_source=levers.choice.source_id))
            return _taken(season, episode, part, best, ReferenceMark.OTHER_PLAYER, why)
        pool = picked
    best = _best(pool, order, ranking).row
    first = _best(pool + failures, order, ranking).row
    if lost(Candidate(first, 0)) and levers.choice is None:
        # The video the order puts first did not download: the next one by the order takes its place, and says why.
        why = reason("reference_download_failed", taken=best.player, failed=first.player, taken_source=best.source_id,
                     failed_source=first.source_id)
        return _taken(season, episode, part, best, ReferenceMark.OTHER_PLAYER, why)
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
    denied: set[tuple[str, int, str]] = set()
    for lever in levers:
        if lever.lever == REFERENCE_CHOICE:
            choice = _choice_of(lever.value)
            own = own or lever.level == Level.EPISODE
        elif lever.lever == VARIANT_ALLOW:
            target = parse_variant_target(lever.target) if isinstance(lever.target, str) else None
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
