"""The pure steps of the count of a series: the question put to the rules for a table, and what the answers of the
neighbours make of the layout, compared with what is stored."""
from __future__ import annotations

from dataclasses import dataclass

from core.contracts import (EpisodeSettings, ReleaseTable, RulesApplyRequest, SeriesSource, SettingRecord,
                            SourceStatus)

from . import compilations, project, settings
from .project import SourceInput
from .stored import Stored


def rules_request(source: SeriesSource, table: ReleaseTable, season, rules,
                  everywhere: frozenset[str] = frozenset()) -> dict | None:
    """The request of the rules for the lines of a table; None when the table has none for the source."""
    lines = project.lines_for(source, table, everywhere)
    if not lines:
        return None
    return RulesApplyRequest(profile_id=source.parser_profile_id, lines=lines, series_season=season,
                             rules=rules).model_dump(mode="json")


@dataclass
class Derived:
    """What a count makes of its inputs and of the stored layout: the rows, the settings and what changed. `conflicts` are
    the groups of records of voice-over groups that became one and set different values: each went back to the level above."""
    result: "project.Projection"
    gone: list[int]
    records: list[SettingRecord]
    records_changed: bool
    orders: dict[int, list[str]]
    moved: set[tuple[int, int]]
    headers_changed: bool
    rows_changed: bool
    summaries_changed: bool
    touched: set[tuple[int, int]] | None
    conflicts: list[list[SettingRecord]]


def derive(inputs: list[SourceInput], groups: dict[str, str | None], stored: Stored) -> Derived:
    """The projection of a count and its comparison with what is stored."""
    result = project.project(inputs, groups, stored.numbering)
    gone = [each.source_id for each in stored.sources if each.source_id not in {i.source.id for i in inputs}]
    before = settings.canonical(stored.records)
    renames = project.group_renames(stored.rows, result.rows)
    orders = project.season_orders(stored.orders, result.rows, renames)
    # What was set for a voice-over group follows the voice-over when the user names it, renames it or dissolves its group.
    records, conflicts = settings.retarget(before, renames)
    for source_id in gone:
        records = settings.without_source(records, source_id)
    layout = project.layout_episodes(result.rows)
    if any(summary.status == SourceStatus.READY for summary in result.sources):
        # Without an answered source the layout says nothing of which episodes exist, so nothing is taken away.
        records = settings.within_episodes(records, layout)
    order = {item.source.id: item.source.position for item in inputs}
    # The membership of an episode follows the rows as well as the levers: what it changes is a change of the episode.
    was, now = shape(stored.rows, before, layout, order), shape(result.rows, records, layout, order)
    moved = settings.affected(was[0], now[0])
    headers_changed = was[1] != now[1]
    rows_changed = result.rows != stored.rows or result.subtitles != stored.subtitles
    # The time of a reading is stored with the summary but is no change of it: a page read again is no news.
    shown = [summary.model_copy(update={"read_at": None}) for summary in result.sources]
    summaries_changed = shown != [each.summary.model_copy(update={"read_at": None}) for each in stored.sources]
    touched = project.touched_episodes(stored.rows, result.rows) if rows_changed else set()
    return Derived(result, gone, records, records != before, orders, moved, headers_changed, rows_changed,
                   summaries_changed, touched, conflicts)


def shape(rows, records, episodes, order) -> tuple[list[EpisodeSettings], tuple]:
    """The levers and the compilation of every episode of the layout, and what the headers of the levels and the blocks
    of compilations show (told to windows when it changes): of one resolution of the compilations."""
    outcome = compilations.resolve(rows, records, order)
    return (settings.episode_settings(records, episodes, outcome.members),
            (settings.levels(records, {season for season, _ in episodes},
                             [(view.season, view.ref.key) for view in outcome.views]), outcome.views))
