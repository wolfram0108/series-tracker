"""Which voice-overs of a series are planned, and where each one comes from: a pure function, no bus and no database.

The input is what the other modules already decided: the rows of the layout (their numbers are the constructor's,
their voice-over group is `voice_label`), the reference of every episode (`scan`) and the output levers of every
episode (`composition`). The voice-overs of an episode are every voice of the layout but the one already in the
reference video, except the voices the levers turn off; the candidates of a voice are its rows that cover the episode
and play, the player of the lever first, then by the order of players in the settings, then by the order of sources.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, NamedTuple, Sequence

from core.contracts import (VOICE_PLAYER, VOICE_TAKE, CompositionRow, EffectiveLever, EpisodeSettings, Reference,
                            ReferenceMark, RowState, group_target)


# A reference with one of these marks is a video of the layout the voice-overs are aligned to.
_REFERENCED = frozenset({ReferenceMark.BY_LEVEL, ReferenceMark.OWN, ReferenceMark.OTHER_PLAYER})

Slot = tuple[int, int]  # (season, episode)


class Candidate(NamedTuple):
    """One place a voice-over of an episode can be downloaded from: the form of a link of `voice_dubs.chain`."""

    player: str
    studio: str
    url: str

    def as_json(self) -> dict[str, str]:
        return {"player": self.player, "studio": self.studio, "url": self.url}


@dataclass(frozen=True)
class PlannedDub:
    """A voice-over of an episode. An empty chain is a voice the episode has no playing row for."""

    season: int
    episode: int
    voice_key: str
    voice_label: str
    chain: tuple[Candidate, ...]


class Compilation(NamedTuple):
    """A compilation row that covers an episode of a voice-over; it is not taken until its file is shared."""

    season: int
    episode: int
    voice_label: str
    source_id: int
    row_key: str


@dataclass(frozen=True)
class Plan:
    """`with_reference`: episodes whose reference is a row of the layout. `undecided`: episodes whose reference row the
    layout does not have (the two were read at different moments): their voice-overs are neither planned nor dropped.
    Every other episode has no reference (none, unfit, or none given)."""

    dubs: tuple[PlannedDub, ...] = ()
    with_reference: frozenset[Slot] = frozenset()
    undecided: frozenset[Slot] = frozenset()
    compilations: tuple[Compilation, ...] = ()


def rank(player: str, priority: Sequence[str], players) -> int:
    """Place of a player in the order of the settings; players the user put into one group take one place and an
    unknown player comes last."""
    order = [players.canonical(kind) for kind in priority]
    wanted = players.canonical(player)
    return order.index(wanted) if wanted in order else len(order)


def plan(rows: Iterable[CompositionRow], references: Iterable[Reference], settings: Iterable[EpisodeSettings],
         priority: Sequence[str], players) -> Plan:
    """The voice-overs to plan; `players` is the user's groups of players (`identity.Groups`)."""
    by_key: dict[tuple[int, str], CompositionRow] = {}
    by_voice: dict[tuple[int, str], list[CompositionRow]] = {}
    voices: dict[str, None] = {}
    for row in rows:
        by_key[(row.source_id, row.row_key)] = row
        if row.excluded or row.season is None or row.episode_start is None or row.episode_end is None:
            continue
        voices.setdefault(row.voice_label)
        by_voice.setdefault((row.season, row.voice_label), []).append(row)
    levers = {(each.season, each.episode): each.levers for each in settings}
    dubs: list[PlannedDub] = []
    with_reference: set[Slot] = set()
    undecided: set[Slot] = set()
    compilations: list[Compilation] = []
    for ref in sorted(references, key=lambda each: (each.season, each.episode)):
        slot = (ref.season, ref.episode)
        if ref.mark not in _REFERENCED or ref.source_id is None or ref.row_key is None:
            continue
        ref_row = by_key.get((ref.source_id, ref.row_key))
        if ref_row is None:
            undecided.add(slot)
            continue
        with_reference.add(slot)
        take_off, picks = _levers(levers.get(slot, ()))
        for label in voices:
            if label == ref_row.voice_label or group_target(label) in take_off:
                continue
            covering = [row for row in by_voice.get((ref.season, label), ()) if _covers(row, ref.episode, ref_row.part)]
            singles = [row for row in covering if not row.is_compilation]
            compilations.extend(Compilation(ref.season, ref.episode, label, row.source_id, row.row_key)
                                for row in covering if row.is_compilation)
            if covering and not singles:
                continue
            dubs.append(PlannedDub(ref.season, ref.episode, label, label,
                                   _chain(singles, picks.get(group_target(label)), priority, players)))
    return Plan(tuple(dubs), frozenset(with_reference), frozenset(undecided), tuple(compilations))


def _levers(levers: Iterable[EffectiveLever]) -> tuple[set[str], dict[str, tuple[int, str]]]:
    """(targets taken off, {target: (source id, player) the user picked}) of the levers of one episode."""
    take_off: set[str] = set()
    picks: dict[str, tuple[int, str]] = {}
    for lever in levers:
        if lever.target is None:
            continue
        if lever.lever == VOICE_TAKE and lever.value is False:
            take_off.add(lever.target)
        elif lever.lever == VOICE_PLAYER and isinstance(lever.value, dict):
            picks[lever.target] = (lever.value.get("source_id"), lever.value.get("player"))
    return take_off, picks


def _covers(row: CompositionRow, episode: int, part: int | None) -> bool:
    """A compilation covers the episodes of its range; a single row covers its episode, in the part of the reference."""
    if row.is_compilation:
        return row.episode_start <= episode <= row.episode_end
    return row.episode_start == episode and row.part == part


def _chain(rows: Sequence[CompositionRow], pick: tuple[int, str] | None, priority: Sequence[str],
           players) -> tuple[Candidate, ...]:
    """The playing rows, the picked player first, then by the order of players, then as the sources are ordered."""
    playing = [row for row in rows if row.state == RowState.PLAYABLE and row.link]
    ordered = sorted(enumerate(playing), key=lambda item: (
        0 if pick == (item[1].source_id, item[1].player) else 1, rank(item[1].player, priority, players), item[0]))
    chain: dict[str, Candidate] = {}
    for _, row in ordered:
        chain.setdefault(row.link, Candidate(row.player, row.voice_label, row.link))
    return tuple(chain.values())
