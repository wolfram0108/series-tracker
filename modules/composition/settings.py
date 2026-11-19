"""Output settings of one series: the value of every lever for an episode, and the edits of the records behind it.

Pure functions over the records of one series. A record exists only where the user set a value of its own; the
value of an episode is its own, else that of the compilation it stands in (stage 12), else its season's, else the series',
else the lever's default. "As the level above"
is the absence of a record, so it is asked for by a reset of the lever, never by a value.
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable, Collection, Iterable, Mapping, Sequence

from core.contracts import (COMPILATION_MEMBER, COMPILATION_TAKE, SUBTITLES_ALLOW,
                            SUBTITLES_ORDER, CompilationRef, EffectiveLever, EpisodeSettings, Level,
                            LevelSettings, Queue, SettingRecord, SettingsReset, ValueShape, compilation_key, group_target,
                            parse_compilation_key, parse_group_target, parse_variant_target, variant_target)
from core import _native
from core.reasons import reason

from . import levers

# The scope of a record: (season, episode, compilation key).
_Scope = tuple[int | None, int | None, str | None]
_Index = dict[str, dict[str | None, dict[_Scope, Any]]]
def validate(record: SettingRecord) -> str | None:
    """None when the lever, its target, its value and its level are in the registry's terms; else the reason to refuse.
    The core tells it (native/composition levers), as it tells the records it reads."""
    try:
        written = json.dumps(record.value, ensure_ascii=False)
    except (TypeError, ValueError):
        written = None
    found = "bad_value" if written is None else _native.lever_problem(
        record.lever, record.target, written, record.season, record.episode, record.compilation)
    if found == "unknown_lever" or (found == "bad_value" and levers.get(record.lever) is None):
        return reason("unknown_lever", lever=record.lever)
    return reason(found, lever=record.lever) if found is not None else None


def validate_reset(scope: SettingsReset) -> str | None:
    """None when a reset names no lever, a queue, or a lever of the registry with the target it points at."""
    if scope.queue is not None:
        return None if scope.target is None else reason("bad_target", lever="")
    if scope.lever is None:
        return reason("bad_target", lever="") if scope.target is not None else None
    lever = levers.get(scope.lever)
    if lever is None:
        return reason("unknown_lever", lever=scope.lever)
    return None if lever.accepts_target(scope.target) else reason("bad_target", lever=scope.lever)


def effective(records: Sequence[SettingRecord], season: int | None, episode: int | None,
              compilation: str | None = None) -> list[EffectiveLever]:
    """Every lever's value for an episode (or, without `episode`, for a compilation or a season; without `season`, for
    the series). `compilation` is the key of the compilation the episode stands in, or of the level itself.

    A lever without a target gives one entry; a lever with targets gives one per target that has a record on the
    episode's own chain of levels, so a record of another season never adds an entry here."""
    return _resolve(_index(records), season, episode, compilation)


def episode_settings(records: Sequence[SettingRecord], episodes: Iterable[tuple[int, int]],
                     members: Mapping[tuple[int, int], CompilationRef | None]) -> list[EpisodeSettings]:
    """The levers of each episode with the compilation it stands in, in the order of season and episode."""
    index = _index(records)
    result = []
    for season, episode in sorted(set(episodes)):
        member = members.get((season, episode))
        result.append(EpisodeSettings(season=season, episode=episode, compilation=member,
                                      levers=_resolve(index, season, episode, member.key if member else None)))
    return result


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
    level = (scope.series_id, scope.season, scope.episode, scope.compilation)

    def gone(each: SettingRecord) -> bool:
        if (each.series_id, each.season, each.episode, each.compilation) != level:
            return False
        if scope.queue is not None:
            lever = levers.get(each.lever)
            return lever is not None and lever.queue == scope.queue
        return scope.lever is None or (each.lever, each.target) == (scope.lever, scope.target)

    return canonical([each for each in records if not gone(each)])


def without_source(records: Sequence[SettingRecord], source_id: int) -> list[SettingRecord]:
    """The records after a source has left the series: a record that points at it by its target is gone; an order loses
    the variants of that source and keeps the others (an order left empty is gone: it was the order of the rule)."""
    kept: list[SettingRecord] = []
    for each in records:
        lever = levers.get(each.lever)
        scope = parse_compilation_key(each.compilation)
        if scope is not None and scope[1] == source_id:
            continue  # the level of a compilation of that source is gone with it
        if lever is None or source_id not in lever.sources_of(each.target, each.value):
            kept.append(each)
        elif lever.shape == ValueShape.VARIANT_ORDER and source_id not in lever.sources_of(each.target, None):
            rest = [item for item in each.value if item.get("source_id") != source_id]
            if rest:
                kept.append(each.model_copy(update={"value": rest}))
        elif lever.shape == ValueShape.VARIANT_SETS:
            rest = _regrouped([[item for item in group if item.get("source_id") != source_id] for group in each.value])
            if rest:
                kept.append(each.model_copy(update={"value": rest}))
    return canonical(kept)


def retarget(records: Sequence[SettingRecord], renames: Mapping[str, str],
             on_conflict: Callable[[list[SettingRecord]], None] | None = None) -> list[SettingRecord]:
    """The records after voice-over groups changed their names: what was set for a group (or for a variant of it, or as
    the group of a variant in an order) is set for the group it became. A record that would meet one the new name has already
    set at the same level meets it with the rest. Records of several groups that became one meet each other: a flag that is off
    stands over one that is on (what the user switched off is not switched on by a merge); orders that are the same are
    one, and orders that differ go back to the level above, which `on_conflict` is told."""
    if not renames:
        return list(records)

    def renamed_key(key: Any) -> Any:
        found = parse_compilation_key(key)
        if found is None or found[0] not in renames:
            return key
        return compilation_key(renames[found[0]], *found[1:])

    def moved(record: SettingRecord) -> SettingRecord | None:
        lever = levers.get(record.lever)
        if lever is not None and lever.queue == Queue.SUBTITLES:
            return record  # the group of a variant of subtitles is the name of the subtitles, no voice-over group
        target, value = record.target, record.value
        compilation = renamed_key(record.compilation)
        if lever is not None and lever.shape == ValueShape.COMPILATION:
            value = renamed_key(value)
        if isinstance(target, str) and (found := parse_group_target(target)) is not None and found in renames:
            target = group_target(renames[found])
        elif isinstance(target, str) and (variant := parse_variant_target(target)) is not None and variant[0] in renames:
            target = variant_target(renames[variant[0]], variant[1], variant[2])
        if (lever is not None and lever.shape == ValueShape.VARIANT_ORDER and isinstance(value, list)
                and any(isinstance(each, dict) and each.get("group") in renames for each in value)):
            value = _deduplicated([{**each, "group": renames.get(each.get("group"), each.get("group"))}
                                   if isinstance(each, dict) and "group" in each else each for each in value])
        if lever is not None and lever.shape == ValueShape.VARIANT_SETS and isinstance(value, list) and any(
                isinstance(item, dict) and item.get("group") in renames for each in value for item in each):
            value = _regrouped([[{**item, "group": renames.get(item.get("group"), item.get("group"))} for item in each]
                                for each in value])
            if not value:
                return None  # no group is left: the record was a word about those groups, the level above speaks again
        return record if (target, value, compilation) == (record.target, record.value, record.compilation) else \
            record.model_copy(update={"target": target, "value": value, "compilation": compilation})

    kept: list[SettingRecord] = []
    arrived: dict[tuple, list[SettingRecord]] = {}
    for record in records:
        shifted = moved(record)
        if shifted is None:
            continue
        if shifted is record:
            kept.append(shifted)
        else:
            arrived.setdefault(_slot(shifted), []).append(shifted)
    # A record the new name has already is one of the records that meet at its place, as a group that was there is one of
    # the groups that became the new one: the same rule for a name that is new and a name that is taken.
    settled = []
    for record in kept:
        if _slot(record) in arrived:
            arrived[_slot(record)].append(record)
        else:
            settled.append(record)
    for group in arrived.values():
        merged = _merged(group)
        if merged is not None:
            settled.append(merged)
        elif on_conflict is not None:
            on_conflict(group)
    return canonical(settled)


def _deduplicated(items: list) -> list:
    """An order with each variant once, at its first place: two groups that became one may name one variant twice."""
    seen: list = []
    for each in items:
        if each not in seen:
            seen.append(each)
    return seen


def _regrouped(groups: list[list[dict]]) -> list[list[dict]]:
    """Groups of equal variants after variants left or were renamed: a variant once, groups that now share a variant one
    group, a group of fewer than two variants gone."""
    found: list[list[dict]] = []
    for group in groups:
        keys = {tuple(sorted(item.items())) for item in group}
        meeting = [each for each in found if keys & {tuple(sorted(item.items())) for item in each}]
        merged = [item for each in [*meeting, group] for item in each]
        unique = list({tuple(sorted(item.items())): item for item in merged}.values())
        found = [each for each in found if each not in meeting]
        found.append(unique)
    return [group for group in found if len(group) > 1]


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


def levels(records: Sequence[SettingRecord], seasons: Iterable[int],
           compilations: Iterable[tuple[int, str]] = ()) -> list[LevelSettings]:
    """The levers of the series, then of each of the seasons given, then of each compilation (season, key) given: what
    the headers of the levels show.

    A header is the value of the level itself, so an episode with a value of its own leaves it as it is."""
    index = _index(records)
    return [LevelSettings(season=None, levers=_resolve(index, None, None)),
            *(LevelSettings(season=season, levers=_resolve(index, season, None)) for season in sorted(set(seasons))),
            *(LevelSettings(season=season, compilation=key, levers=_resolve(index, season, None, key))
              for season, key in sorted(set(compilations)))]


def affected(before: Sequence[EpisodeSettings], after: Sequence[EpisodeSettings]) -> set[tuple[int, int]]:
    """The (season, episode) pairs whose levers or compilation come out differently; there is no rule per kind of change.

    An episode that keeps its own value of the lever that moved compares equal, so it is not touched."""
    old = {(each.season, each.episode): each for each in before}
    new = {(each.season, each.episode): each for each in after}
    return {slot for slot in set(old) | set(new) if old.get(slot) != new.get(slot)}


def _slot(record: SettingRecord) -> tuple:
    return record.series_id, record.season, record.episode, record.compilation, record.lever, record.target


def canonical(records: Sequence[SettingRecord]) -> list[SettingRecord]:
    """One order for equal sets of records, so that independent edits commute."""
    return sorted(records, key=lambda each: (each.series_id, -1 if each.season is None else each.season,
                                              -1 if each.episode is None else each.episode, each.compilation or "",
                                              each.lever, each.target or ""))


def _index(records: Sequence[SettingRecord]) -> _Index:
    """lever -> target -> (season, episode, compilation) -> value, over the records the registry still accepts. A value
    is copied here, once: the levers every episode inherits share it (an order of the series stands in all its episodes),
    and nobody changes a value read."""
    index: _Index = {}
    for record in records:
        if validate(record) is None:
            index.setdefault(record.lever, {}).setdefault(record.target, {})[
                (record.season, record.episode, record.compilation)] = deepcopy(record.value)
    return index


def _resolve(index: _Index, season: int | None, episode: int | None, compilation: str | None = None) -> list[EffectiveLever]:
    if episode is not None and season is None:
        raise ValueError("an episode is set within a season")
    chain: list[tuple[Level, _Scope]] = [(Level.SERIES, (None, None, None))]
    if season is not None:
        chain.insert(0, (Level.SEASON, (season, None, None)))
    if compilation is not None:
        chain.insert(0, (Level.COMPILATION, (season, None, compilation)))
    if episode is not None:
        chain.insert(0, (Level.EPISODE, (season, episode, None)))
    scopes = {scope for _, scope in chain}
    result = []
    for lever in levers.all():
        by_target = index.get(lever.name, {})
        targets = [None] if lever.target == levers.TargetKind.NONE else sorted(
            target for target, values in by_target.items() if scopes & set(values))
        for target in targets:
            values = by_target.get(target, {})
            level, value = next(((level, values[scope]) for level, scope in chain if scope in values), (Level.DEFAULT, None))
            # The default is the registry's own: each lever gets a copy of it.
            value = deepcopy(lever.default) if level == Level.DEFAULT else value
            result.append(EffectiveLever(lever=lever.name, target=target, value=value, level=level))
    return result
