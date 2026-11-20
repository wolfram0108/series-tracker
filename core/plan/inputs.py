"""The inputs of the planner of a series out of the service's own contracts (stage 12): the layout of the composition (its
rows, the levers of every episode and of every compilation, the order of the voice-overs), the settings of the voice-overs
and the order of the sources. Pure: the module of the planner and the checks on real series read the same way."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Iterable, Mapping, Sequence

from core import native
from core.contracts import CompositionReply, EffectiveLever, SeriesSource
from core.ignored import parse_memory
from core.voice_names import Groups

from .tables import Choices, Container, RowId


def online_of(rows, places: Mapping[int, str] | None = None, first: int = 0) -> native.OnlineRows:
    """The rows of the output (`CompositionRow.in_output`: the rules gave them an episode or a range, the voice-over groups
    took them; a row of subtitles needs no group), of the rows of a layout as it keeps them (`core.native.Rows`). The place
    of a row is its place in the layout, every row counted (from `first`: the rows of subtitles come after the rows of
    sound), as the service's rule breaks a tie. Kept by the core: a row becomes an object here only when it is read."""
    return rows.online(dict(places or {}), first)


def assets_against(containers: Iterable[Container], online: native.OnlineRows) -> tuple[Container, ...]:
    """The asset read against the rows of the sources: the video behind a row is told apart only where both sides know
    its number (В-6). A file whose number is unknown, or a row that names none, is the same video as the row of its key.
    A file none of whose rows is told apart is the file itself."""
    online = native.OnlineRows(online)

    def same(row: RowId | None) -> RowId | None:
        found = online.video_of(row.source_id, row.row_key) if row is not None else None
        if found is not None and found != row.video and not (found and row.video):
            return replace(row, video=found)
        return row

    def against(each: Container) -> Container:
        video = each.video
        ids = (video.id, video.timeline, video.cut_of, *(id for track in each.tracks for id in (track.dub, track.aligned_to)),
               *(id for item in each.subtitles for id in (item.row, item.timeline)))
        if all(same(id) is id for id in ids):
            return each
        return replace(each, video=replace(video, id=same(video.id), timeline=same(video.timeline), cut_of=same(video.cut_of)),
                       tracks=tuple(replace(track, dub=same(track.dub), aligned_to=same(track.aligned_to))
                                    for track in each.tracks),
                       subtitles=tuple(replace(item, row=same(item.row), timeline=same(item.timeline))
                                       for item in each.subtitles))

    return tuple(against(each) for each in containers)


def choices_of(rows, online, episodes: Iterable[tuple[int, int, Iterable[EffectiveLever]]],
               compilation_levers: Iterable[tuple[str, Iterable[EffectiveLever]]], *,
               players: Sequence[str], player_groups: Groups, sources: Sequence[int],
               voice_order: Mapping[int, Sequence[str]],
               off_episodes: Iterable[tuple[int, int]], titles: Mapping[str, str] | None = None,
               languages: Mapping[str, str] | None = None) -> Choices:
    """What the user chose, made by the core (native/src/plan_inputs.rs) of the rows of the layout (`core.native.Rows`) and
    of the output (`online_of`): `episodes` are the levers of every episode as its levels give them (season, episode,
    levers), `compilation_levers` those of every compilation (its key, levers); `titles` the user's names of the tracks of
    the voice-over groups, `languages` the language the user gave each group. A lever is read once however many episodes
    share it (the value of their level)."""
    return native.plan_choices(rows, online, list(episodes), list(compilation_levers), players=list(players),
                               player_groups=player_groups, sources=list(sources),
                               voice_order={season: list(order) for season, order in voice_order.items()},
                               off_episodes=set(off_episodes), titles=dict(titles or {}), languages=dict(languages or {}))


def planner_inputs(layout: CompositionReply, series: Mapping[str, Any],
                   voices: Mapping[str, Any]) -> tuple[native.OnlineRows, Choices]:
    """The rows and the choices of a series as the planner reads them: `layout` is the answer of `composition.get`,
    `series` the card of the catalog, `voices` the view of the settings of the voice-overs (`voices.settings.get`). The
    module of the planner and the tools on a real series read the series by this one function."""
    places = {each.source_id: " / ".join(each.place) for each in layout.sources}
    # The rows as the layout keeps them, read in place; the rows of subtitles ride among the rows (`Inputs` sets them apart).
    rows, subtitles = native.Rows(layout.rows_field), native.Rows(layout.subtitles_field)
    online = online_of(rows, places) + online_of(subtitles, places, len(rows))
    try:
        ignored_seasons = {int(each) for each in json.loads(series.get("ignored_seasons") or "[]")}
    except (json.JSONDecodeError, TypeError, ValueError):
        ignored_seasons = set()
    try:
        ignored = parse_memory(json.loads(series.get("ignored_episodes") or "{}"))
    except (json.JSONDecodeError, TypeError):
        ignored = {}
    off = {slot for slot in online.episodes() if slot[0] in ignored_seasons or slot[1] in ignored.get(slot[0], set())}
    order = {each.season: [span.label for span in each.voices] for each in layout.voice_orders}
    sources = [SeriesSource.model_validate(each).id for each in series.get("sources", [])]
    choices = choices_of(rows, online, [(each.season, each.episode, each.levers) for each in layout.settings],
                         [(each.compilation, each.levers) for each in layout.levels if each.compilation],
                         players=voices.get("priority") or [], player_groups=Groups.of(voices.get("player_groups") or {}),
                         sources=sources, voice_order=order, off_episodes=off, titles=layout.voice_titles,
                         languages=voices.get("voice_languages") or {})
    return online, choices
