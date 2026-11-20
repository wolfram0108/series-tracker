"""The compilations of a series and the one each episode stands in (stage 12): a pure function of the layout and the levers.

A compilation is a row of the layout whose video holds several episodes; rows that share a key (the same voice-over
group, source, player, season and range) are one compilation, its video the best of them. An episode stands in at most
one compilation:

  - the user's word on the episode (`compilation.member`) — a key, or "" for none — holds while that compilation still
    covers the episode, is switched on and is better than the episode's own single video;
  - otherwise the rule: of the compilations that cover it, are switched on (`compilation.take`) and are better — higher —
    than the best single video of the episode that plays, the shortest; then the higher, then the earlier source, then
    the key. An episode without a single video that plays has nothing to be cut by and stands in none.

The best single video is the one of the layout, not the one the reference takes: the reference reads the levers of the
compilation the episode stands in, so it cannot decide that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from core import native
from core.contracts import (COMPILATION_MEMBER, COMPILATION_TAKE, Absence, CompilationEpisode, CompilationRef,
                            CompilationView, SettingRecord, parse_compilation_key)

from . import settings

Episode = tuple[int, int]


@dataclass
class Outcome:
    """The compilation of every episode that stands in one, and the compilations of every season as a window shows them."""

    members: dict[Episode, CompilationRef] = field(default_factory=dict)
    views: list[CompilationView] = field(default_factory=list)


def compilations(rows: native.Rows) -> list[CompilationRef]:
    """One reference per key over the rows that play, are grouped and are numbered as a range, its video the highest row
    (the earlier on a tie), in the order the keys come in the layout."""
    return [CompilationRef(key=key, source_id=source_id, row_key=row_key, player=player, voice_group=group, height=height,
                           start=start, end=end)
            for key, source_id, row_key, player, group, height, start, end in rows.compilations()]


def resolve(rows: native.Rows, records: Sequence[SettingRecord], source_order: Mapping[int, int]) -> Outcome:
    """The membership of every episode and the view of every compilation, by the rule of the module docstring."""
    refs = compilations(rows)
    if not refs:
        return Outcome()
    season_of = {ref.key: _season(ref) for ref in refs}
    # The best single video of every episode that plays (at least 1: a video of no known height still plays).
    singles: dict[Episode, int] = rows.singles()
    takes = {ref.key: _value(records, COMPILATION_TAKE, season_of[ref.key], None, ref.key) is not False for ref in refs}
    covered: dict[Episode, list[CompilationRef]] = {}
    for ref in refs:
        for episode in range(ref.start, ref.end + 1):
            covered.setdefault((season_of[ref.key], episode), []).append(ref)

    def better(ref: CompilationRef, slot: Episode) -> bool:
        single = singles.get(slot)
        return single is not None and (ref.height or 0) > single

    def rank(ref: CompilationRef) -> tuple:
        return ref.end - ref.start, -(ref.height or 0), source_order.get(ref.source_id, len(source_order)), ref.key

    outcome = Outcome()
    own: dict[Episode, str | None] = {}
    for slot, refs_here in covered.items():
        word = _own(records, slot)
        fit = [ref for ref in refs_here if takes[ref.key] and better(ref, slot)]
        chosen = None
        if word is not None:
            own[slot] = word
            chosen = next((ref for ref in fit if ref.key == word), None)
            if chosen is None and word == "":
                continue
        if chosen is None:
            if word:
                own.pop(slot)  # the word names a compilation that does not take the episode any more: the rule decides
            chosen = min(fit, key=rank, default=None)
        if chosen is not None:
            outcome.members[slot] = chosen
    for season in sorted(set(season_of.values())):
        ordered = sorted((ref for ref in refs if season_of[ref.key] == season), key=lambda ref: (ref.start, ref.end, ref.key))
        for index, ref in enumerate(ordered):
            episodes = []
            for episode in range(ref.start, ref.end + 1):
                slot = (season, episode)
                member = outcome.members.get(slot)
                if member is not None and member.key == ref.key:
                    episodes.append(CompilationEpisode(episode=episode, member=True, own=slot in own))
                    continue
                absence = (Absence.OFF if not takes[ref.key] else Absence.NO_SINGLE if slot not in singles
                           else Absence.NOT_BETTER if not better(ref, slot) else Absence.ELSEWHERE if member is not None
                           else Absence.OWN)
                episodes.append(CompilationEpisode(episode=episode, absence=absence, own=slot in own,
                                                   elsewhere=member.key if absence == Absence.ELSEWHERE else None))
            outcome.views.append(CompilationView(season=season, index=index, ref=ref, take=takes[ref.key], episodes=episodes))
    return outcome


def fits(rows: native.Rows, records: Sequence[SettingRecord], season: int, episode: int, key: str,
         source_order: Mapping[int, int]) -> bool:
    """Whether the episode may be put into the compilation by the user's word: it covers it, is switched on and is better."""
    view = next((each for each in resolve(rows, records, source_order).views
                 if each.season == season and each.ref.key == key), None)
    if view is None or not view.take:
        return False
    found = next((each for each in view.episodes if each.episode == episode), None)
    return found is not None and (found.member or found.absence in (Absence.ELSEWHERE, Absence.OWN))


def _season(ref: CompilationRef) -> int:
    return parse_compilation_key(ref.key)[3]


def _own(records: Sequence[SettingRecord], slot: Episode) -> str | None:
    """The user's word on the membership of the episode: a key, "" for none, None for no word."""
    return next((each.value for each in records if each.lever == COMPILATION_MEMBER and each.season == slot[0]
                 and each.episode == slot[1] and settings.validate(each) is None), None)


def _value(records: Sequence[SettingRecord], lever: str, season: int, episode: int | None, compilation: str | None):
    found = next((each for each in settings.effective(records, season, episode, compilation) if each.lever == lever), None)
    return found.value if found is not None else None
