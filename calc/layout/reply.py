"""The answer of `composition.get`: the layout of a series, or the part of it asked, made of what is stored."""
from __future__ import annotations

from core.contracts import LAYOUT_PARTS, CompositionGetRequest, CompositionReply, availability

from . import compilations, levers, project, settings
from .stored import Stored, source_order


def layout_of(request: CompositionGetRequest, stored: Stored) -> CompositionReply:
    """The layout, or the part of it asked (`LAYOUT_PARTS`), as the model: every field is made one way, for the whole and
    for a part, and only the fields asked are made."""
    part = "sources" if request.sources_only else request.part
    known: dict[str, object] = {}

    def once(name: str, make):
        if name not in known:
            known[name] = make()
        return known[name]

    def shown(rows):
        return [row for row in rows if request.include_excluded or not row.excluded]

    def records():
        return once("records", lambda: settings.canonical(stored.records))

    def episodes():
        return once("episodes", lambda: project.layout_episodes(stored.rows))

    def outcome():
        return once("outcome", lambda: compilations.resolve(stored.rows, records(), source_order(stored)))

    def chosen():
        # The levers of the episodes named (a part `levers` after a change of some), of all in any other case.
        named = {(each.season, each.episode) for each in request.episodes} if part == "levers" else set()
        return {each for each in episodes() if not named or each in named}

    fields = {
        "rows": lambda: shown(stored.rows),
        "subtitles": lambda: shown(stored.subtitles),
        "sources": lambda: [each.summary for each in stored.sources],
        "levels": lambda: settings.levels(records(), {season for season, _ in episodes()},
                                          [(view.season, view.ref.key) for view in outcome().views]),
        "settings": lambda: settings.episode_settings(records(), chosen(), outcome().members),
        "compilations": lambda: outcome().views,
        "levers": levers.specs,
        "availability": availability.table,
        # A series counted before the orders were kept shows its default order until its next count writes it.
        "voice_orders": lambda: project.season_voices(project.season_orders(stored.orders, stored.rows, {}), stored.rows),
        "voice_titles": lambda: stored.titles,
    }
    wanted = fields.keys() if part == "whole" else LAYOUT_PARTS[part]
    return CompositionReply(series_id=request.series_id, rev=stored.rev, part=part,
                            **{name: fields[name]() for name in wanted})
