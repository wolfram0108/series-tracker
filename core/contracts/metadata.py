"""The mapping of a series to its record in TMDB, and what TMDB answers of a title, as the module `metadata` takes and answers them."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .frozen import Frozen


class TmdbMapping(Frozen):
    """The record `series_tmdb_mappings` of a series, as the table holds it (the columns in the order of the table).

    `season_episode_counts` is the cache of the episodes of every season of the show, a JSON text `{"1": 26, ...}` that the module keeps
    of what TMDB said, so that the network is asked again only for a season it does not hold."""

    series_id: int
    tmdb_id: int
    tmdb_season_number: int
    total_episodes: int | None = None
    last_updated: str | None = None
    poster_path: str | None = None
    series_name: str | None = None
    year: str | None = None
    season_episode_counts: str | None = None


class MapGet(BaseModel):
    """`metadata.map.get`: the series whose mapping is asked."""

    series_id: int


class MapReply(Frozen):
    """`metadata.map.get`: the mapping of the series, none when it has none."""

    mapping: TmdbMapping | None = None


class MapListReply(Frozen):
    """`metadata.map.list`: the mappings of every series that has one."""

    mappings: tuple[TmdbMapping, ...]


class MapSet(BaseModel):
    """`metadata.map.set`: the mapping to write. `tmdb_data` holds the columns of the record the caller names (the others are left
    as they are; a field that is no column is ignored); a series with none gets a record with no episodes counted."""

    series_id: int
    tmdb_data: dict[str, Any]


class MapSaved(Frozen):
    """`metadata.map.set`: the mapping was written (the count of the episodes follows from the event of the series)."""

    ok: bool


class TmdbSearchRequest(BaseModel):
    query: str


class TmdbFound(Frozen):
    """A title the search of TMDB found: its name in Russian and in English (a title with no Russian name is told in its own
    letters by TMDB, so the English one is asked beside it), the year of its first air date."""

    id: int | None = None
    name: str | None = None
    name_en: str | None = None
    original_name: str | None = None
    year: str = ""
    poster_path: str | None = None
    overview: str = ""


class TmdbSearchReply(Frozen):
    results: tuple[TmdbFound, ...]


class TmdbDetailsRequest(BaseModel):
    tmdb_id: int


class TmdbSeason(Frozen):
    season_number: int | None = None
    episode_count: int | None = None
    air_date: str | None = None
    name: str | None = None


class TmdbDetails(Frozen):
    """What TMDB says of a show: its names (`original_name` is the name in the language of the title, Chinese for a donghua), its
    poster, its status and its seasons."""

    name: str | None = None
    original_name: str | None = None
    poster_path: str | None = None
    status: str | None = None
    seasons: tuple[TmdbSeason, ...]


class SeasonsRecompute(BaseModel):
    """`metadata.seasons.recompute`: the series whose count of episodes is made again from its real seasons."""

    series_id: int


class SeasonsRecounted(Frozen):
    """`metadata.seasons.recompute`: `skipped` says why nothing was counted (`no_tmdb`, `single_season`, `no_seasons`); otherwise the
    total of the episodes and the real seasons it was counted over."""

    skipped: str | None = None
    total: int | None = None
    seasons: tuple[int, ...] | None = None
