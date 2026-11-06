"""The choice of the reference of every episode of a series from its layout.

A pure function of the rows of the layout, the levers of every episode and the order of players: no bus, no database,
no clock, so equal inputs give equal answers. The reference is a single episode, never a compilation: a compilation
only tells why an episode has no reference (`unfit`), or, when the episode stands in it (stage 12), is carried with the
reference as the video the episode is cut out of, on the timeline of that single episode.

A reference whose row has an unsure answer (`not_obtained`, which is not `absent`) is held, so that a probe that failed
once does not swap the reference and the file for a worse one and back; an order of the user is stronger than the hold.
So is a reference whose file is on disk and whose row the layout does not have at all: a read that did not bring a row
proves nothing (a page read short, a video VK hid from the listing of its channel), and the file is not swapped on it; nor does a
row with no number. A row the layout numbers otherwise, or one the configuration took out (`CompositionRow.left_out`),
is its word: the reference follows it.

A slot is (season, episode, part): the pieces of one episode that are different videos. Of the playable single rows
of a slot that the user did not leave out (`reference.allow`), the head of the queue of `core.variant_queue` is the
reference: the variants of the user's order (`reference.order`) first, in his order, then the others by the rule. When
the first variant he named has no such row in the slot, the next one is taken, marked as another player.

A video whose download failed for good (`failed`: its source, row and link) is passed over: the next in the queue is the
reference, with the mark of another player and the reason (ЭТАП_10: «если скачивание не удаётся, то выбирается плеер по
следующему приоритету»). When every video of the episode failed there is no next one: the best stays, with the reason,
and is tried again. The failure is of that video: the same row showing another video is a candidate again.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Iterable, Sequence

from core.contracts import (REFERENCE_ALLOW, REFERENCE_ORDER, CompositionRow, EffectiveLever, EpisodeSettings, Level,
                            Reference, ReferenceMark, RowState, parse_variant_target)
from core.reasons import reason
from core.variant_queue import REFERENCE_RANKING, Candidate, Order, RankKey, ranked
from core.voice_names import NO_GROUPS, Groups

Slot = tuple[int, int, int | None]
# A video whose download failed: (source id, row key, link of the row).
Failed = tuple[int, str, str]
# A variant: (group, source id, player).
Variant = tuple[str, int, str]


def _variant(candidate: Candidate) -> Variant:
    return candidate.row.voice_label, candidate.row.source_id, candidate.row.player


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

    order: tuple[Variant, ...] = ()  # the variants the user put first, in his order
    own: bool = False
    denied: frozenset[Variant] = frozenset()  # the variants the user left out of the queue of the reference

    @property
    def wanted(self) -> Variant | None:
        """The first variant of the user's order that he did not leave out: the one he asked for."""
        return next((each for each in self.order if each not in self.denied), None)

    def allows(self, row: CompositionRow) -> bool:
        return (row.voice_label, row.source_id, row.player) not in self.denied

    def allows_reference(self, reference: Reference) -> bool:
        return (reference.voice_group or "", reference.source_id, reference.player) not in self.denied


def choose(rows: Sequence[CompositionRow], settings: Iterable[EpisodeSettings], players: Sequence[str],
           sources: Sequence[int], ranking: Sequence[RankKey] = REFERENCE_RANKING,
           previous: Iterable[Reference] = (), player_groups: Groups = NO_GROUPS,
           on_disk: Collection[tuple[int, str]] = (), failed: Collection[Failed] = ()) -> list[Chosen]:
    """One outcome per slot of every episode that has a row in the layout, in the order of season, episode, part.

    `players` is the order of players of the settings, best first; `sources` the ids of the sources of the series in
    their order; `previous` the references chosen last time, of which a slot may hold on to its own. `on_disk` are the
    rows (source id, row key) whose video is downloaded; a previous reference of one of them that the layout does not
    have is held, with no row (`Chosen.row` is None). An episode with no row in the layout and none held has no outcome.
    `rows` may hold the excluded rows: they are never candidates, but they tell a row taken out from a row not brought.

    An episode that stands in a compilation (stage 12, `EpisodeSettings.compilation`) keeps its single video as the
    reference (REF 2, the timeline the compilation is cut on) and carries the compilation with it; an episode in parts, or
    one without a single video, cannot be cut and carries none."""
    settings = list(settings)
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
            if gone is not None and not episode_levers.order and episode_levers.allows_reference(gone):
                chosen.append(_held(gone, episode_levers))
                continue
            chosen.append(_decide(season, episode, part, slot, compilations.get((season, episode), []),
                                  episode_levers, order, ranking, held.get((season, episode, part)), set(failed)))
    members = {(each.season, each.episode): each.compilation for each in settings if each.compilation is not None}
    return [_with_compilation(each, members) for each in chosen]


def _with_compilation(chosen: Chosen, members: dict) -> Chosen:
    member = members.get((chosen.reference.season, chosen.reference.episode))
    if member is None or chosen.reference.part is not None or chosen.reference.source_id is None:
        return chosen
    return Chosen(chosen.reference.model_copy(update={"compilation": member}), chosen.row)


def _held(previous: Reference, levers: _Levers) -> Chosen:
    """The previous reference kept with no row of the layout: its file stands for the slot as it stood before."""
    mark = ReferenceMark.OWN if levers.own else ReferenceMark.BY_LEVEL
    return Chosen(previous.model_copy(update={"mark": mark, "reason": None}))


def _decide(season: int, episode: int, part: int | None, singles: list[Candidate], compilations: list[Candidate],
            levers: _Levers, order: Order, ranking: Sequence[RankKey], held: Reference | None,
            failed: Collection[Failed] = frozenset()) -> Chosen:
    def lost(candidate: Candidate) -> bool:
        return (candidate.row.source_id, candidate.row.row_key, candidate.row.link) in failed

    mark = ReferenceMark.OWN if levers.own else ReferenceMark.BY_LEVEL
    if not levers.order and held is not None:
        unsure = next((candidate.row for candidate in singles
                       if (candidate.row.source_id, candidate.row.row_key) == (held.source_id, held.row_key)
                       and candidate.row.state == RowState.NOT_OBTAINED and levers.allows(candidate.row)
                       and not lost(candidate)), None)
        if unsure is not None:
            # The mark is the one the levers of the episode give now: the same as before unless they changed.
            return _taken(season, episode, part, unsure, mark, height=unsure.height or held.height)
    allowed = [candidate for candidate in singles if candidate.row.state == RowState.PLAYABLE and levers.allows(candidate.row)]
    queue = ranked(allowed, order, ranking, levers.order, _variant)
    pool = [candidate for candidate in queue if not lost(candidate)]
    if not queue:
        return _without(season, episode, part, compilations, levers)
    if not pool:
        # Every video of the episode failed: there is no next one, so the head stays and the next sync tries it again
        # (a failure that passes, as a host not resolved yet, is not a reason to leave the episode without a video).
        players = ", ".join(sorted({candidate.row.player for candidate in queue}))
        return _taken(season, episode, part, queue[0].row, mark, reason("reference_download_failed_all", players=players))
    taken, head = pool[0].row, queue[0].row
    if lost(queue[0]):
        # The video the queue puts first did not download: the next one takes its place, and says why.
        why = reason("reference_download_failed", taken=taken.player, failed=head.player, taken_source=taken.source_id,
                     failed_source=head.source_id)
        return _taken(season, episode, part, taken, ReferenceMark.OTHER_PLAYER, why)
    wanted = levers.wanted
    if wanted is not None and _variant(pool[0]) != wanted:
        # The variant the user put first has no playing single row of this episode: the next one of his queue is taken.
        why = reason("reference_other_player", taken=taken.player, missing=wanted[2], taken_source=taken.source_id,
                     missing_source=wanted[1])
        return _taken(season, episode, part, taken, ReferenceMark.OTHER_PLAYER, why)
    return _taken(season, episode, part, taken, mark)


def _without(season: int, episode: int, part: int | None, compilations: list[Candidate], levers: _Levers) -> Chosen:
    """No single row can be the reference: say whether a compilation holds the episode, or there is nothing."""
    covering = [candidate for candidate in compilations if candidate.row.state == RowState.PLAYABLE
                and levers.allows(candidate.row)]
    if covering:
        return Chosen(Reference(season=season, episode=episode, part=part, mark=ReferenceMark.UNFIT,
                                reason=reason("reference_unfit")))
    return Chosen(Reference(season=season, episode=episode, part=part, mark=ReferenceMark.NONE,
                            reason=reason("reference_none")))


def _taken(season: int, episode: int, part: int | None, row: CompositionRow, mark: ReferenceMark,
           why: str | None = None, height: int | None = None) -> Chosen:
    reference = Reference(season=season, episode=episode, part=part, mark=mark, source_id=row.source_id,
                          row_key=row.row_key, player=row.player, voice_group=row.voice_label,
                          height=height if height is not None else row.height, reason=why)
    return Chosen(reference, row)


def _levers_of(levers: Iterable[EffectiveLever]) -> _Levers:
    order: list[Variant] = []
    own = False
    denied: set[Variant] = set()
    for lever in levers:
        if lever.lever == REFERENCE_ORDER:
            order = _order_of(lever.value)
        elif lever.lever == REFERENCE_ALLOW:
            target = parse_variant_target(lever.target) if isinstance(lever.target, str) else None
            if target is not None and lever.value is False:
                denied.add(target)
        else:
            continue
        own = own or lever.level == Level.EPISODE
    return _Levers(tuple(order), own, frozenset(denied))


def _order_of(value) -> list[Variant]:
    """The user's order; an item that is not a variant (the registry refuses such, but a record may be older) is skipped."""
    if not isinstance(value, list):
        return []
    return [(each["group"], each["source_id"], each["player"]) for each in value
            if isinstance(each, dict) and isinstance(each.get("group"), str) and isinstance(each.get("source_id"), int)
            and isinstance(each.get("player"), str)]
