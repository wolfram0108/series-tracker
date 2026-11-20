"""The count of the episodes of a series that holds several seasons.

The seasons that count are the real ones, those the files of the series are named by (not the specials, season 0, which TMDB
does not number the way a release does); the episodes of each season are TMDB's. The total is their sum over the real seasons.
What TMDB said of the seasons of the show is kept in the mapping, so that the network is asked again only for a season the cache
does not hold (a new season came out).
"""
from __future__ import annotations

import json
from typing import Iterable, Mapping

from core.contracts import TmdbSeason


def real_seasons(named: Iterable[object]) -> frozenset[int]:
    """The seasons the files are of, as numbers, without season 0 (the specials) and without what is no number."""
    out: set[int] = set()
    for season in named:
        try:
            number = int(season)
        except (TypeError, ValueError):
            continue
        if number != 0:
            out.add(number)
    return frozenset(out)


def cached_counts(stored: str | None) -> dict[int, int]:
    """The episodes of each season of the show the mapping holds, season → episodes; empty for a cache that is none or is damaged."""
    try:
        return {int(season): count for season, count in json.loads(stored or "{}").items()}
    except (ValueError, TypeError, AttributeError):
        return {}


def counts_of(seasons: Iterable[TmdbSeason]) -> dict[int, int]:
    """The episodes of each season TMDB numbers and counts; a season it gives no number or no count for is left out."""
    return {season.season_number: season.episode_count for season in seasons
            if season.season_number is not None and season.episode_count is not None}


def holds_every(counts: Mapping[int, int], real: frozenset[int]) -> bool:
    """Whether the counts cover every real season; if not, TMDB is asked (a new season has come)."""
    return real <= set(counts)


def total_of(counts: Mapping[int, int], real: frozenset[int]) -> int:
    """The episodes of the real seasons; a real season TMDB does not know counts for none."""
    return sum(counts.get(season, 0) for season in real)


def stored_counts(counts: Mapping[int, int]) -> str:
    """The cache of the counts as the mapping keeps it: a JSON text with the seasons as keys."""
    return json.dumps({str(season): count for season, count in counts.items()})
