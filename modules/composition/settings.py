"""Output settings of one series: the value of every lever for an episode, and the edits of the records behind it.

Pure functions over the records of one series. A record exists only where the user set a value of its own; the
value of an episode is its own, else its season's, else the series', else the lever's default.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Sequence

from core.contracts import EffectiveLever, Level, SettingRecord, SettingsReset
from core.reasons import reason

from . import levers

# The value that takes a record away: the level goes back to what the level above says.
AS_ABOVE = "as_above"

_Index = dict[str, dict[str | None, dict[tuple[int | None, int | None], Any]]]


def validate(record: SettingRecord) -> str | None:
    """None when the lever, its target and its value are in the registry's terms; else the reason to refuse."""
    lever = levers.get(record.lever)
    if lever is None:
        return reason("unknown_lever", lever=record.lever)
    if not lever.accepts_target(record.target):
        return reason("bad_target", lever=record.lever)
    if record.value != AS_ABOVE and not lever.accepts(record.value):
        return reason("bad_value", lever=record.lever)
    return None


def effective(records: Sequence[SettingRecord], season: int | None, episode: int | None) -> list[EffectiveLever]:
    """Every lever's value for an episode (or, without `episode`, for a season; without `season`, for the series).

    A lever without a target gives one entry; a lever with targets gives one per target that has a record on the
    episode's own chain of levels, so a record of another season never adds an entry here."""
    return _resolve(_index(records), season, episode)


def apply_set(records: Sequence[SettingRecord], record: SettingRecord) -> list[SettingRecord]:
    """The records after setting a lever's value at a level; `AS_ABOVE` takes the record away. Raises on a refused one."""
    problem = validate(record)
    if problem is not None:
        raise ValueError(problem)
    kept = [each for each in records if _slot(each) != _slot(record)]
    if record.value != AS_ABOVE:
        kept.append(SettingRecord(series_id=record.series_id, season=record.season, episode=record.episode,
                                  lever=record.lever, target=record.target, value=deepcopy(record.value)))
    return _canonical(kept)


def apply_reset(records: Sequence[SettingRecord], scope: SettingsReset | SettingRecord) -> list[SettingRecord]:
    """The records after one level goes back to inheritance; the levels below it keep their own values."""
    return _canonical([each for each in records
                       if (each.series_id, each.season, each.episode) != (scope.series_id, scope.season, scope.episode)])


def affected(before: Sequence[SettingRecord], after: Sequence[SettingRecord],
             episodes: Iterable[tuple[int, int]]) -> set[tuple[int, int]]:
    """The (season, episode) pairs whose levers come out differently; there is no rule per kind of change.

    An episode that keeps its own value of the lever that moved compares equal, so it is not touched."""
    old, new = _index(before), _index(after)
    return {(season, episode) for season, episode in set(episodes)
            if _resolve(old, season, episode) != _resolve(new, season, episode)}


def _slot(record: SettingRecord) -> tuple:
    return record.series_id, record.season, record.episode, record.lever, record.target


def _canonical(records: list[SettingRecord]) -> list[SettingRecord]:
    """One order for equal sets of records, so that independent edits commute."""
    return sorted(records, key=lambda each: (each.series_id, -1 if each.season is None else each.season,
                                              -1 if each.episode is None else each.episode, each.lever,
                                              each.target or ""))


def _index(records: Sequence[SettingRecord]) -> _Index:
    """lever -> target -> (season, episode) -> value, over the records the registry still accepts."""
    index: _Index = {}
    for record in records:
        if record.value != AS_ABOVE and validate(record) is None:
            index.setdefault(record.lever, {}).setdefault(record.target, {})[(record.season, record.episode)] = record.value
    return index


def _resolve(index: _Index, season: int | None, episode: int | None) -> list[EffectiveLever]:
    if episode is not None and season is None:
        raise ValueError("an episode is set within a season")
    chain = [(Level.SERIES, (None, None))]
    if season is not None:
        chain.insert(0, (Level.SEASON, (season, None)))
    if episode is not None:
        chain.insert(0, (Level.EPISODE, (season, episode)))
    scopes = {scope for _, scope in chain}
    result = []
    for lever in levers.all():
        by_target = index.get(lever.name, {})
        targets = [None] if lever.target == levers.TargetKind.NONE else sorted(
            target for target, values in by_target.items() if scopes & set(values))
        for target in targets:
            values = by_target.get(target, {})
            level, value = next(((level, values[scope]) for level, scope in chain if scope in values),
                                (Level.DEFAULT, lever.default))
            result.append(EffectiveLever(lever=lever.name, target=target, value=deepcopy(value), level=level))
    return result
