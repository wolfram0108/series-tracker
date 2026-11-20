"""The inputs of the planner of a series out of the service's own contracts (stage 12): the layout of the composition (its
rows, the levers of every episode and of every compilation, the order of the voice-overs), the settings of the voice-overs
and the order of the sources. Pure: the module of the planner and the checks on real series read the same way."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Iterable, Mapping, Sequence

from core.contracts import (COMPILATION_MEMBER, COMPILATION_TAKE, MARKUP_ALLOW, MARKUP_ORDER, MARKUP_SAME, REFERENCE_ALLOW, REFERENCE_ORDER, REFERENCE_SAME,
                            SUBTITLES_ALLOW, SUBTITLES_ORDER, VOICE_ALLOW,
                            VOICE_ORDER, VOICE_TAKE, CompositionReply, CompositionRow, EffectiveLever, Level, RowState,
                            SeriesSource, compilation_key,
                            parse_group_target, parse_variant_target)
from core.ignored import parse_memory
from core.subtitle_queue import named_of
from core.variant_queue import Same
from core.voice_names import Groups

from .tables import Choices, Container, OnlineRow, RowId


def online_of(rows: Sequence[CompositionRow], places: Mapping[int, str] | None = None) -> tuple[OnlineRow, ...]:
    """The rows of the output (`CompositionRow.in_output`: the rules gave them an episode or a range, the voice-over groups
    took them; a row of subtitles needs no group). The place of a row is its place in the layout, every row counted, as the
    service's rule breaks a tie."""
    places = places or {}
    online = []
    for position, row in enumerate(rows):
        if not row.in_output:
            continue
        online.append(OnlineRow(RowId(row.source_id, row.row_key, row.video or ""), row.season, row.episode_start,
                                row.episode_end or row.episode_start, row.player, row.voice_group or "",
                                max(row.heights or [0]), places.get(row.source_id, ""), row.link, row.voice_label, position,
                                row.part, row.state == RowState.PLAYABLE, not row.stale, row.player_label, row.timeline,
                                row.track.value, row.language, row.subtitle_type))
    return tuple(online)


def assets_against(containers: Iterable[Container], online: Iterable[OnlineRow]) -> tuple[Container, ...]:
    """The asset read against the rows of the sources: the video behind a row is told apart only where both sides know
    its number (В-6). A file whose number is unknown, or a row that names none, is the same video as the row of its key."""
    by_key = {(each.id.source_id, each.id.row_key): each.id for each in online}

    def same(row: RowId | None) -> RowId | None:
        found = by_key.get((row.source_id, row.row_key)) if row is not None else None
        if found is not None and found.video != row.video and not (found.video and row.video):
            return found
        return row

    return tuple(replace(each, video=replace(each.video, id=same(each.video.id), timeline=same(each.video.timeline),
                                             cut_of=same(each.video.cut_of)),
                         tracks=tuple(replace(track, dub=same(track.dub), aligned_to=same(track.aligned_to))
                                      for track in each.tracks),
                         subtitles=tuple(replace(item, row=same(item.row), timeline=same(item.timeline))
                                         for item in each.subtitles)) for each in containers)


def compilations_of(rows: Iterable[CompositionRow], online: Iterable[OnlineRow]) -> dict[str, RowId]:
    """The row of every compilation by its key (core/contracts/compilation.py)."""
    ids = {(each.id.source_id, each.id.row_key): each.id for each in online}
    found = {}
    for row in rows:
        if row.is_compilation and not row.excluded and row.season is not None:
            key = compilation_key(row.voice_label, row.source_id, row.player, row.season, row.episode_start, row.episode_end)
            if (row.source_id, row.row_key) in ids:
                found[key] = ids[(row.source_id, row.row_key)]
    return found


def choices_of(rows: Sequence[CompositionRow], online: Sequence[OnlineRow],
               episodes: Iterable[tuple[int, int, Iterable[EffectiveLever]]],
               compilation_levers: Iterable[tuple[str, Iterable[EffectiveLever]]], *,
               players: Sequence[str], player_groups: Groups, sources: Sequence[int],
               voice_order: Mapping[int, Sequence[str]],
               off_episodes: Iterable[tuple[int, int]], titles: Mapping[str, str] | None = None,
               languages: Mapping[str, str] | None = None) -> Choices:
    """What the user chose. `episodes` are the levers of every episode as its levels give them (season, episode, levers),
    `compilation_levers` those of every compilation (its key, levers); `titles` the user's names of the tracks of the
    voice-over groups, `languages` the language the user gave each group."""
    by_key = compilations_of(rows, online)
    reference, reference_deny, voice_orders, voice_deny, voice_off, member = {}, {}, {}, {}, {}, {}
    markup, markup_deny = {}, {}
    same, markup_same = {}, {}
    subtitle_order, subtitle_deny = {}, {}
    own_reference: set = set()
    for season, episode, levers in episodes:
        slot = (season, episode)
        for lever in levers:
            if lever.lever in (REFERENCE_ORDER, REFERENCE_ALLOW, REFERENCE_SAME) and lever.level == Level.EPISODE:
                own_reference.add(slot)
            if lever.lever == REFERENCE_ORDER and isinstance(lever.value, list):
                reference[slot] = tuple((each["group"], each["source_id"], each["player"]) for each in lever.value
                                        if isinstance(each, dict) and {"group", "source_id", "player"} <= set(each))
            elif lever.lever == REFERENCE_SAME and isinstance(lever.value, list):
                same[slot] = Same.of(lever.value)
            elif lever.lever == MARKUP_SAME and isinstance(lever.value, list):
                markup_same[slot] = Same.of(lever.value)
            elif lever.lever == REFERENCE_ALLOW and lever.value is False and lever.target:
                variant = parse_variant_target(lever.target)
                if variant is not None:
                    reference_deny[slot] = reference_deny.get(slot, frozenset()) | {variant}
            elif lever.lever == MARKUP_ORDER and isinstance(lever.value, list) and lever.level != Level.DEFAULT:
                # A level of the chain has a queue of the markup reference of its own; with none it follows the reference.
                markup[slot] = tuple((each["group"], each["source_id"], each["player"]) for each in lever.value
                                     if isinstance(each, dict) and {"group", "source_id", "player"} <= set(each))
            elif lever.lever == MARKUP_ALLOW and lever.value is False and lever.target:
                variant = parse_variant_target(lever.target)
                if variant is not None:
                    markup_deny[slot] = markup_deny.get(slot, frozenset()) | {variant}
            elif lever.lever == VOICE_ORDER and isinstance(lever.value, list) and lever.target:
                group = parse_group_target(lever.target)
                if group is not None:
                    voice_orders[(slot, group)] = tuple((each["source_id"], each["player"]) for each in lever.value
                                                        if isinstance(each, dict) and {"source_id", "player"} <= set(each))
            elif lever.lever == VOICE_ALLOW and lever.value is False and lever.target:
                variant = parse_variant_target(lever.target)
                if variant is not None:
                    voice_deny[slot] = voice_deny.get(slot, frozenset()) | {variant}
            elif lever.lever == VOICE_TAKE and lever.value is False and lever.target:
                group = parse_group_target(lever.target)
                if group is not None:
                    voice_off[slot] = voice_off.get(slot, frozenset()) | {group}
            elif lever.lever == SUBTITLES_ORDER and isinstance(lever.value, list):
                subtitle_order[slot] = tuple(named_of(lever.value))
            elif lever.lever == SUBTITLES_ALLOW and lever.value is False and lever.target:
                variant = parse_variant_target(lever.target)
                if variant is not None:
                    subtitle_deny[slot] = subtitle_deny.get(slot, frozenset()) | {variant}
            elif lever.lever == COMPILATION_MEMBER and lever.value is not None:
                member[slot] = by_key.get(lever.value) if lever.value else None
    off_compilations = frozenset(by_key[key] for key, levers in compilation_levers if key in by_key and any(
        lever.lever == COMPILATION_TAKE and lever.value is False for lever in levers))
    return Choices(players=tuple(players), sources=tuple(sources), groups={season: tuple(order) for season, order in voice_order.items()}, player_groups=player_groups,
                   off_episodes=frozenset(off_episodes), off_compilations=off_compilations, reference=reference,
                   reference_deny=reference_deny, voice_order=voice_orders, voice_deny=voice_deny, voice_off=voice_off,
                   member=member, own_reference=frozenset(own_reference), titles=dict(titles or {}), languages=dict(languages or {}),
                   markup=markup, markup_deny=markup_deny, same=same, markup_same=markup_same,
                   subtitle_order=subtitle_order, subtitle_deny=subtitle_deny)


def planner_inputs(layout: CompositionReply, series: Mapping[str, Any],
                   voices: Mapping[str, Any]) -> tuple[tuple[OnlineRow, ...], Choices]:
    """The rows and the choices of a series as the planner reads them: `layout` is the answer of `composition.get`,
    `series` the card of the catalog, `voices` the view of the settings of the voice-overs (`voices.settings.get`). The
    module of the planner and the tools on a real series read the series by this one function."""
    places = {each.source_id: " / ".join(each.place) for each in layout.sources}
    # The rows of subtitles ride among the rows (`Inputs` sets them apart): every reader of the pair takes them as they are.
    online = online_of([*layout.rows, *layout.subtitles], places)
    try:
        ignored_seasons = {int(each) for each in json.loads(series.get("ignored_seasons") or "[]")}
    except (json.JSONDecodeError, TypeError, ValueError):
        ignored_seasons = set()
    try:
        ignored = parse_memory(json.loads(series.get("ignored_episodes") or "{}"))
    except (json.JSONDecodeError, TypeError):
        ignored = {}
    slots = {row.slot[:2] for row in online if not row.compilation}
    off = {slot for slot in slots if slot[0] in ignored_seasons or slot[1] in ignored.get(slot[0], set())}
    order = {each.season: [span.label for span in each.voices] for each in layout.voice_orders}
    sources = [SeriesSource.model_validate(each).id for each in series.get("sources", [])]
    choices = choices_of(layout.rows, online, [(each.season, each.episode, each.levers) for each in layout.settings],
                         [(each.compilation, each.levers) for each in layout.levels if each.compilation],
                         players=voices.get("priority") or [], player_groups=Groups.of(voices.get("player_groups") or {}),
                         sources=sources, voice_order=order, off_episodes=off, titles=layout.voice_titles,
                         languages=voices.get("voice_languages") or {})
    return online, choices
