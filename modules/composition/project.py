"""The layout of a series as a pure function of what its sources gave and what the constructor answered.

Nothing here touches a site, the clock, a database or chance: equal inputs give equal rows. Every number of a row
comes from the constructor's answer to the line of that row; this module never computes one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Mapping, Sequence

from core.contracts import (CompositionRow, Exclusion, PlayerSummary, ReleaseTable, RowState, RuleLine, RuleResult,
                            RulesApplyReply, SeriesSource, SourceStatus, SourceSummary, TableRow, line_of)
from core.voice_names import fold_name

# One template for the name of a voice-over that belongs to no user group.
UNKNOWN_VOICE_LABEL = "Неизвестная озвучка {number}"

# The only player whose list holds several seasons of a catalog, one of which the user may pick for the source.
CATALOG_PLAYER = "vibix"


@dataclass(frozen=True)
class SourceInput:
    """A source of the series with what the other modules told about it.

    `reply` is None while the source has no profile; `failure` is a reason when the page could not be read, and
    then `table` is the last complete table, which stays in force."""

    source: SeriesSource
    table: ReleaseTable | None = None
    reply: RulesApplyReply | None = None
    failure: str | None = None
    # The canonical address of the page as `sources` gave it, known before the first table arrives.
    table_url: str | None = None


@dataclass(frozen=True)
class Projection:
    """`unknown_voices` maps the folded name of a voice-over to its number; it only grows."""

    rows: list[CompositionRow]
    sources: list[SourceSummary]
    unknown_voices: dict[str, int]


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
    return [RuleLine(key=row.key, text=line_of(table, row), player=row.player,
                     voice=voice_name_of(row, None, source.voice_name)) for row in selected_rows(source, table)]


def voice_name_of(row: TableRow, result: RuleResult | None, fallback: str = "") -> str:
    """The name the constructor assigned beats the one the player or the site gave, which beats the one the user gave
    the source for rows that have no name."""
    assigned = result.voiceover if result is not None else None
    if assigned and assigned.strip():
        return assigned
    return fallback if not row.voice.name.strip() and fallback.strip() else row.voice.name


def voice_names(inputs: Sequence[SourceInput]) -> list[str]:
    """The distinct voice-over names of the rows the constructors answered: the names the groups are asked about."""
    names: dict[str, None] = {}
    for item in _ordered(inputs):
        names.update((voice_name_of(row, result, item.source.voice_name), None) for row, result in _answered_rows(item)
                     if voice_name_of(row, result, item.source.voice_name).strip())
    return list(names)


def project(inputs: Sequence[SourceInput], groups: Mapping[str, str | None],
            unknown: Mapping[str, int]) -> Projection:
    """Rows of all sources in source order, a summary of every source, and the numbering of unknown voice-overs.

    `groups` maps a voice-over name to the user group it belongs to, None for a name in no group."""
    numbering = dict(unknown)
    rows: list[CompositionRow] = []
    summaries: list[SourceSummary] = []
    for item in _ordered(inputs):
        summaries.append(_summary(item))
        for row, result in _answered_rows(item):
            rows.append(_row_of(item.source, row, result, voice_name_of(row, result, item.source.voice_name), groups,
                                numbering))
    return Projection(rows, summaries, numbering)


def group_renames(before: Sequence[CompositionRow], after: Sequence[CompositionRow]) -> dict[str, str]:
    """{label: label} of the voice-overs that kept their names and changed their group: one made from an unknown
    voice-over, one renamed, one dissolved. A label that still stands in the new layout is not renamed, and a label
    that turned into several is not told from itself, so neither is in the answer."""
    was = {(row.source_id, row.row_key): row for row in before}
    standing = {row.voice_label for row in after}
    became: dict[str, set[str]] = {}
    for row in after:
        old = was.get((row.source_id, row.row_key))
        if old is None or old.voice_label == row.voice_label or old.voice_name != row.voice_name:
            continue
        became.setdefault(old.voice_label, set()).add(row.voice_label)
    return {old: next(iter(new)) for old, new in became.items() if len(new) == 1 and old not in standing}


def layout_episodes(rows: Iterable[CompositionRow]) -> set[tuple[int, int]]:
    """The (season, episode) pairs the layout covers; a compilation covers every episode of its range.

    A row without a season names no episode that a setting could be about, so it covers none."""
    covered: set[tuple[int, int]] = set()
    for row in rows:
        covered |= _covered(row)
    return covered


def touched_episodes(before: Sequence[CompositionRow], after: Sequence[CompositionRow]) -> set[tuple[int, int]] | None:
    """The episodes whose rows are not the same in the two layouts; None when the difference cannot be told as
    episodes (a numbered row without a season changed). An excluded row covers no episode."""
    old = {(row.source_id, row.row_key): row for row in before}
    new = {(row.source_id, row.row_key): row for row in after}
    changed = [row for key, row in old.items() if new.get(key) != row] + [
        row for key, row in new.items() if old.get(key) != row]
    touched: set[tuple[int, int]] = set()
    for row in changed:
        covered = _covered(row)
        if not covered and not row.excluded:
            return None
        touched |= covered
    return touched


def _covered(row: CompositionRow) -> set[tuple[int, int]]:
    if row.excluded or row.season is None or row.episode_start is None:
        return set()
    end = row.episode_end if row.episode_end is not None else row.episode_start
    return {(row.season, number) for number in range(row.episode_start, max(end, row.episode_start) + 1)}


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
        voice_group=group, voice_label=label, voice_name=name, voice_unknown=number, player=row.player,
        player_label=row.player_label, group=row.group, text=row.text, heights=list(row.heights), state=row.state,
        published=row.published, link=row.link)


def _numbers_of(result: RuleResult) -> dict:
    """A single episode wins over a range, as it has always done; a range without an end ends where it starts."""
    if result.excluded:
        return {"excluded": True, "exclusion": Exclusion.RULE}
    numbers = {"season": result.season, "part": result.part}
    if result.episode is not None:
        return {**numbers, "episode_start": result.episode, "episode_end": result.episode}
    if result.start is not None:
        return {**numbers, "episode_start": result.start,
                "episode_end": result.end if result.end is not None else result.start}
    return {**numbers, "excluded": True, "exclusion": Exclusion.NO_NUMBER}


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


def _summary(item: SourceInput) -> SourceSummary:
    table = item.table
    return SourceSummary(
        source_id=item.source.id, url=item.source.url, canonical_url=table.url if table else item.table_url,
        status=_status_of(item),
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
    """What a window shows before any rule has read the rows: per player the rows and the seasons a catalog keeps."""
    by_player: dict[str, list[TableRow]] = {}
    for row in table.rows:
        by_player.setdefault(row.player, []).append(row)
    return [PlayerSummary(
        player=player, player_label=next((row.player_label for row in rows if row.player_label), ""),
        rows=len(rows), groups=len({row.group for row in rows if row.group}),
        group_names=list(dict.fromkeys(row.group for row in rows if row.group)))
        for player, rows in by_player.items()]
