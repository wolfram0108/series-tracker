"""The memory of a series of what the user switched off: seasons and episodes of them, for the rows that are there and for those a new
release brings. The plan of the series reads it (modules/planner)."""
from __future__ import annotations

import json

from core.contracts import OutputSwitch, SeriesCard
from core.ignored import parse_memory


def ignored_episodes(series: SeriesCard) -> dict[int, set[int]]:
    """Episodes the user switched off in advance, as for torrents: their rows are created switched off, so neither the download nor the
    voice-overs take them."""
    try:
        return parse_memory(json.loads(series.ignored_episodes or "{}"))
    except (json.JSONDecodeError, TypeError):
        return {}


def ignored_seasons(series: SeriesCard) -> set[int]:
    """The seasons the user switched off (VU11): their episodes are not in the plan."""
    try:
        return {int(each) for each in json.loads(series.ignored_seasons or "[]")}
    except (json.JSONDecodeError, TypeError, ValueError):
        return set()


def switched(series: SeriesCard, request: OutputSwitch) -> dict[str, str]:
    """The fields of the card a switch comes to: episodes (one or many at once), or a whole season, off or on. Switching a season on
    switches on all of it, the episodes switched off one by one included."""
    seasons, memory = ignored_seasons(series), ignored_episodes(series)
    if request.episodes is None:
        seasons = seasons | {request.season} if request.off else seasons - {request.season}
        if not request.off:
            memory.pop(request.season, None)
    else:
        numbers, asked = memory.get(request.season, set()), set(request.episodes)
        memory[request.season] = numbers | asked if request.off else numbers - asked
    return {"ignored_seasons": json.dumps(sorted(seasons)),
            "ignored_episodes": json.dumps({str(season): sorted(numbers) for season, numbers in memory.items() if numbers})}
