"""Output settings of one series: the value of every lever for an episode, and the edits of the records behind it.

Pure functions over the records of one series. A record exists only where the user set a value of its own; the
value of an episode is its own, else its season's, else the series', else the lever's default. "As the level above"
is the absence of a record, so it is asked for by a reset of the lever, never by a value.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Collection, Iterable, Mapping, Sequence

from core.contracts import (EffectiveLever, Level, LevelSettings, SettingRecord, SettingsReset, ValueShape, group_target,
                            parse_group_target, parse_variant_target, variant_target)
from core.reasons import reason

from . import levers

_Index = dict[str, dict[str | None, dict[tuple[int | None, int | None], Any]]]


def validate(record: SettingRecord) -> str | None:
    """None when the lever, its target and its value are in the registry's terms; else the reason to refuse."""
    lever = levers.get(record.lever)
    if lever is None:
        return reason("unknown_lever", lever=record.lever)
    if not lever.accepts_target(record.target):
        return reason("bad_target", lever=record.lever)
    if not lever.accepts(record.value):
        return reason("bad_value", lever=record.lever)
    return None


def validate_reset(scope: SettingsReset) -> str | None:
    """None when a reset names no lever, or a lever of the registry with the target it points at."""
    if scope.lever is None:
        return reason("bad_target", lever="") if scope.target is not None else None
    lever = levers.get(scope.lever)
    if lever is None:
        return reason("unknown_lever", lever=scope.lever)
    return None if lever.accepts_target(scope.target) else reason("bad_target", lever=scope.lever)


def effective(records: Sequence[SettingRecord], season: int | None, episode: int | None) -> list[EffectiveLever]:
    """Every lever's value for an episode (or, without `episode`, for a season; without `season`, for the series).

    A lever without a target gives one entry; a lever with targets gives one per target that has a record on the
    episode's own chain of levels, so a record of another season never adds an entry here."""
    return _resolve(_index(records), season, episode)


def apply_set(records: Sequence[SettingRecord], record: SettingRecord) -> list[SettingRecord]:
    """The records after setting a lever's value at a level. Raises ValueError with the reason on a refused one."""
    problem = validate(record)
    if problem is not None:
        raise ValueError(problem)
    kept = [each for each in records if _slot(each) != _slot(record)]
    kept.append(SettingRecord(series_id=record.series_id, season=record.season, episode=record.episode,
                              lever=record.lever, target=record.target, value=deepcopy(record.value)))
    return canonical(kept)


def apply_reset(records: Sequence[SettingRecord], scope: SettingsReset) -> list[SettingRecord]:
    """The records after a level, or one lever of it, goes back to inheritance; the levels below keep their values.

    Raises ValueError with the reason when the reset names a lever or a target the registry does not know."""
    problem = validate_reset(scope)
    if problem is not None:
        raise ValueError(problem)
    level = (scope.series_id, scope.season, scope.episode)

    def gone(each: SettingRecord) -> bool:
        return (each.series_id, each.season, each.episode) == level and (
            scope.lever is None or (each.lever, each.target) == (scope.lever, scope.target))

    return canonical([each for each in records if not gone(each)])


def without_source(records: Sequence[SettingRecord], source_id: int) -> list[SettingRecord]:
    """The records after a source has left the series: those that point at it, by target or by value, are gone."""
    def points_at(each: SettingRecord) -> bool:
        lever = levers.get(each.lever)
        return lever is not None and source_id in lever.sources_of(each.target, each.value)

    return canonical([each for each in records if not points_at(each)])


def retarget(records: Sequence[SettingRecord], renames: Mapping[str, str],
             on_conflict: Callable[[list[SettingRecord]], None] | None = None) -> list[SettingRecord]:
    """The records after voice-over groups changed their names: what was set for a group (or for a variant of it, or as
    the group of the reference) is set for the group it became. A record that would meet one the new name has already
    set at the same level gives way to it. Records of several groups that became one meet each other: a flag that is off
    stands over one that is on (what the user switched off is not switched on by a merge); picks that are the same are
    one, and picks that differ go back to the level above, which `on_conflict` is told."""
    if not renames:
        return list(records)

    def moved(record: SettingRecord) -> SettingRecord:
        lever = levers.get(record.lever)
        target, value = record.target, record.value
        if isinstance(target, str) and (found := parse_group_target(target)) is not None and found in renames:
            target = group_target(renames[found])
        elif isinstance(target, str) and (variant := parse_variant_target(target)) is not None and variant[0] in renames:
            target = variant_target(renames[variant[0]], variant[1], variant[2])
        if (lever is not None and lever.shape == ValueShape.REFERENCE_PICK and isinstance(value, dict)
                and value.get("group") in renames):
            value = {**value, "group": renames[value["group"]]}
        return record if (target, value) == (record.target, record.value) else record.model_copy(
            update={"target": target, "value": value})

    own = {_slot(each) for each in records}
    kept: list[SettingRecord] = []
    arrived: dict[tuple, list[SettingRecord]] = {}
    for record in records:
        shifted = moved(record)
        if shifted is record:
            kept.append(shifted)
        elif _slot(shifted) in own and shifted.target != record.target:
            continue  # the new name has this record already: its own stands
        else:
            arrived.setdefault(_slot(shifted), []).append(shifted)
    for group in arrived.values():
        merged = _merged(group)
        if merged is not None:
            kept.append(merged)
        elif on_conflict is not None:
            on_conflict(group)
    return canonical(kept)


def _merged(group: list[SettingRecord]) -> SettingRecord | None:
    """One record for records that came to one place; None when they cannot be one and the place is left to the level above."""
    if len(group) == 1:
        return group[0]
    lever = levers.get(group[0].lever)
    if lever is not None and lever.shape == ValueShape.FLAG:
        return next((each for each in group if each.value is False), group[0])
    return group[0] if all(each.value == group[0].value for each in group) else None


def within_episodes(records: Sequence[SettingRecord], episodes: Collection[tuple[int, int]]) -> list[SettingRecord]:
    """The records after episodes have left the layout: an episode's own records go with it, the levels above stay."""
    return canonical([each for each in records
                       if each.episode is None or (each.season, each.episode) in episodes])


def levels(records: Sequence[SettingRecord], seasons: Iterable[int]) -> list[LevelSettings]:
    """The levers of the series, then of each of the seasons given: what the headers of the levels show.

    A header is the value of the level itself, so an episode with a value of its own leaves it as it is."""
    index = _index(records)
    return [LevelSettings(season=None, levers=_resolve(index, None, None)),
            *(LevelSettings(season=season, levers=_resolve(index, season, None)) for season in sorted(set(seasons)))]


def affected(before: Sequence[SettingRecord], after: Sequence[SettingRecord],
             episodes: Iterable[tuple[int, int]]) -> set[tuple[int, int]]:
    """The (season, episode) pairs whose levers come out differently; there is no rule per kind of change.

    An episode that keeps its own value of the lever that moved compares equal, so it is not touched."""
    old, new = _index(before), _index(after)
    return {(season, episode) for season, episode in set(episodes)
            if _resolve(old, season, episode) != _resolve(new, season, episode)}


def _slot(record: SettingRecord) -> tuple:
    return record.series_id, record.season, record.episode, record.lever, record.target


def canonical(records: Sequence[SettingRecord]) -> list[SettingRecord]:
    """One order for equal sets of records, so that independent edits commute."""
    return sorted(records, key=lambda each: (each.series_id, -1 if each.season is None else each.season,
                                              -1 if each.episode is None else each.episode, each.lever,
                                              each.target or ""))


def _index(records: Sequence[SettingRecord]) -> _Index:
    """lever -> target -> (season, episode) -> value, over the records the registry still accepts."""
    index: _Index = {}
    for record in records:
        if validate(record) is None:
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
