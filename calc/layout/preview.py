"""The answer of `composition.preview`: what a window shows of an address that is no source of a series yet."""
from __future__ import annotations

from core.contracts import (CompositionPreviewReply, CompositionPreviewRequest, PreviewLine, RowState, RuleLine,
                            RuleResult, RulesApplyRequest, SourceType, SeriesSource, TableGetReply, TableState)

from . import project
from .project import SourceInput


def draft_source(request: CompositionPreviewRequest) -> SeriesSource:
    """The address as a source of no series: the choices of a source it would have, with the number 0."""
    return SeriesSource(id=0, series_id=0, url=request.url, source_type=SourceType.WEB_VIDEO,
                        parser_profile_id=request.profile_id, players_off=request.players_off,
                        vibix_season=request.vibix_season)


def page_preview(got: TableGetReply, item: SourceInput, groups: dict[str, str | None]) -> CompositionPreviewReply:
    """The preview of a page with its table: the summary, the lines with their answers and the rows the profile makes."""
    table = item.table
    projection = project.project([item], groups, {})
    results = item.reply.results if item.reply is not None else {}
    heights = {row.key: list(row.heights) for row in table.rows if row.state == RowState.PLAYABLE}
    lines = [PreviewLine(key=line.key, line=line.text, result=results.get(line.key), heights=heights.get(line.key, []))
             for line in project.lines_for(item.source, table, item.players_off)]
    return CompositionPreviewReply(url=got.url, state=got.state, reason=got.reason, names=list(table.names),
                                   summary=projection.sources[0], lines=lines,
                                   rows=[row for row in projection.rows if not row.excluded])


def tableless_preview(got: TableGetReply) -> CompositionPreviewReply:
    """The preview of a page that has no complete table: its state and the reason of its own, and nothing of the rest."""
    return CompositionPreviewReply(url=got.url, state=got.state, reason=got.reason)


def lines_request(request: CompositionPreviewRequest) -> dict | None:
    """The question put to the profile for the lines a window holds itself; None when there is no profile or no line."""
    lines = [RuleLine(key=str(place), text=text) for place, text in enumerate(request.lines)]
    if request.profile_id is None or not lines:
        return None
    return RulesApplyRequest(profile_id=request.profile_id, lines=lines,
                             series_season=request.series_season).model_dump(mode="json")


def lines_preview(request: CompositionPreviewRequest, results: dict[str, RuleResult]) -> CompositionPreviewReply:
    """The preview of lines the window holds itself: no page and no table, only the profile's answer to each line."""
    return CompositionPreviewReply(
        url=request.url, state=TableState.READY,
        lines=[PreviewLine(key=str(place), line=text, result=results.get(str(place)))
               for place, text in enumerate(request.lines)])
