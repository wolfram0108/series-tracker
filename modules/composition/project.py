"""The layout of a series as a pure function of what its sources gave and what the constructor answered.

Nothing here touches a site, the clock, a database or chance: equal inputs give equal rows. Every number of a row
comes from the constructor's answer to the line of that row; this module never computes one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Mapping, Sequence

from core.contracts import (CompositionRow, PlayerSummary, ReleaseTable, RowState, RuleLine, RuleResult,
                            RulesApplyReply, SeriesSource, SourceStatus, SourceSummary, TableRow, line_of)

# One template for the name of a voice-over that belongs to no user group.
UNKNOWN_VOICE_LABEL = "Неизвестная озвучка {number}"

EXCLUDED_BY_RULE = "rule"
EXCLUDED_NO_NUMBER = "no_number"

# The only player whose list holds several seasons of a catalog, one of which the user may pick for the source.
CATALOG_PLAYER = "vibix"

_NOISE = re.compile(r"[\W_]+", re.UNICODE)


@dataclass(frozen=True)
class SourceInput:
    """A source of the series with what the other modules told about it.

    `reply` is None while the source has no profile; `failure` is a reason when the page could not be read, and
    then `table` is the last complete table, which stays in force."""

    source: SeriesSource
    table: ReleaseTable | None = None
    reply: RulesApplyReply | None = None
    failure: str | None = None


@dataclass(frozen=True)
class Projection:
    """`unknown_voices` maps the folded name of a voice-over to its number; it only grows."""

    rows: list[CompositionRow]
    sources: list[SourceSummary]
    unknown_voices: dict[str, int]


def fold_name(name: str) -> str:
    """The form in which two spellings of a voice-over name are the same name: no case, no spaces, no punctuation."""
    return _NOISE.sub("", name.casefold())


def selected_rows(source: SeriesSource, table: ReleaseTable) -> list[TableRow]:
    """The rows the source's own choice leaves in play; the single place that applies that choice.

    A switched-off player drops out; of a catalog player only the season at the chosen 1-based position stays."""
    off = set(source.players_off)
    groups = list(dict.fromkeys(row.group for row in table.rows if row.player == CATALOG_PLAYER))
    position = source.vibix_season - 1 if source.vibix_season and source.vibix_season > 0 else None
    chosen = groups[position] if position is not None and 0 <= position < len(groups) else None
    kept = []
    for row in table.rows:
        if row.player in off:
            continue
        if row.player == CATALOG_PLAYER and position is not None and (chosen is None or row.group != chosen):
            continue
        kept.append(row)
    return kept


def lines_for(source: SeriesSource, table: ReleaseTable) -> list[RuleLine]:
    """What the constructor is asked about the source: one line with its key for every row in play."""
    return [RuleLine(key=row.key, text=line_of(table, row)) for row in selected_rows(source, table)]


def voice_name_of(row: TableRow, result: RuleResult | None) -> str:
    """The name the constructor assigned beats the one the player or the site gave."""
    assigned = result.voiceover if result is not None else None
    return assigned if assigned and assigned.strip() else row.voice.name


def voice_names(inputs: Sequence[SourceInput]) -> list[str]:
    """The distinct voice-over names of the rows in the layout, in order of appearance: what to ask the groups about."""
    names: dict[str, None] = {}
    for item in _ordered(inputs):
        for row, result in _answered_rows(item):
            name = voice_name_of(row, result)
            if name.strip():
                names.setdefault(name)
    return list(names)


def project(inputs: Sequence[SourceInput], groups: Mapping[str, str | None],
            unknown: Mapping[str, int]) -> Projection:
    """Rows of all sources in source order, a summary of every source, and the numbering of unknown voice-overs.

    `groups` maps a voice-over name to the user group it belongs to, None for a name in no group."""
    numbering = dict(unknown)
    built: list[tuple[CompositionRow, str]] = []
    summaries: list[SourceSummary] = []
    for item in _ordered(inputs):
        summaries.append(_summary(item))
        for row, result in _answered_rows(item):
            name = voice_name_of(row, result)
            built.append((_row_of(item.source, row, result, name, groups, numbering), fold_name(name)))
    return Projection(_with_part_one(built), summaries, numbering)


def _ordered(inputs: Sequence[SourceInput]) -> list[SourceInput]:
    return sorted(inputs, key=lambda item: (item.source.position, item.source.id))


def _answered_rows(item: SourceInput) -> Iterator[tuple[TableRow, RuleResult]]:
    """The rows of a source with a table and a profile; a row the constructor gave no answer for has no numbers."""
    if item.table is None or item.reply is None or item.source.parser_profile_id is None:
        return
    for row in selected_rows(item.source, item.table):
        result = item.reply.results.get(row.key)
        yield row, result if result is not None else RuleResult()


def _row_of(source: SeriesSource, row: TableRow, result: RuleResult, name: str, groups: Mapping[str, str | None],
            numbering: dict[str, int]) -> CompositionRow:
    group, label, number = _voice_of(name, groups, numbering)
    return CompositionRow(
        series_id=source.series_id, source_id=source.id, row_key=row.key, **_numbers_of(result),
        voice_group=group, voice_label=label, voice_unknown=number, player=row.player,
        player_label=row.player_label, group=row.group, text=row.text, heights=list(row.heights), state=row.state,
        published=row.published, link=row.link)


def _numbers_of(result: RuleResult) -> dict:
    """A single episode wins over a range, as it has always done; a range without an end ends where it starts."""
    if result.excluded:
        return {"excluded": True, "exclusion": EXCLUDED_BY_RULE}
    numbers = {"season": result.season, "part": result.part}
    if result.episode is not None:
        return {**numbers, "episode_start": result.episode, "episode_end": result.episode}
    if result.start is not None:
        return {**numbers, "episode_start": result.start,
                "episode_end": result.end if result.end is not None else result.start}
    return {**numbers, "excluded": True, "exclusion": EXCLUDED_NO_NUMBER}


def _voice_of(name: str, groups: Mapping[str, str | None], numbering: dict[str, int]
              ) -> tuple[str | None, str, int | None]:
    """(group, label, unknown number); a new unknown name takes the next number and keeps it for good."""
    group = groups.get(name) if name.strip() else None
    if group:
        return group, group, None
    key = fold_name(name)
    if key not in numbering:
        numbering[key] = max(numbering.values(), default=0) + 1
    return None, UNKNOWN_VOICE_LABEL.format(number=numbering[key]), numbering[key]


def _with_part_one(built: list[tuple[CompositionRow, str]]) -> list[CompositionRow]:
    """A single episode without a part gets part 1 where its slot has single episodes with parts."""
    def slot(row: CompositionRow, voice: str) -> tuple:
        return row.source_id, row.player, voice, row.season, row.episode_start

    def single(row: CompositionRow) -> bool:
        return not row.excluded and row.episode_start is not None and not row.is_compilation

    parted = {slot(row, voice) for row, voice in built if single(row) and row.part is not None}
    return [row.model_copy(update={"part": 1}) if single(row) and row.part is None and slot(row, voice) in parted
            else row for row, voice in built]


def _summary(item: SourceInput) -> SourceSummary:
    table = item.table
    return SourceSummary(
        source_id=item.source.id, url=item.source.url, status=_status_of(item),
        reason=item.failure or None, read_at=table.read_at if table else None,
        players=_players_of(table) if table else [])


def _status_of(item: SourceInput) -> SourceStatus:
    if item.failure:
        return SourceStatus.FAILED
    if item.table is None:
        return SourceStatus.WAITING
    if item.source.parser_profile_id is None:
        return SourceStatus.NO_PROFILE
    return SourceStatus.READY if item.reply is not None else SourceStatus.WAITING


def _players_of(table: ReleaseTable) -> list[PlayerSummary]:
    """What a window shows before any number exists: per player the rows, the seasons it keeps and its resolutions."""
    by_player: dict[str, list[TableRow]] = {}
    for row in table.rows:
        by_player.setdefault(row.player, []).append(row)
    return [PlayerSummary(
        player=player, player_label=next((row.player_label for row in rows if row.player_label), ""),
        rows=len(rows), groups=len({row.group for row in rows if row.group}),
        heights=sorted({height for row in rows if row.state == RowState.PLAYABLE for height in row.heights}))
        for player, rows in by_player.items()]
