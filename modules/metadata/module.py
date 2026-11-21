"""Module metadata: the integration with TMDB; owner of series_tmdb_mappings.

Differences from the old utils/tmdb_client.py (by its analysis):
- the errors of TMDB are not swallowed (the old one returned []/None and the problem drowned): they go into the reply and the
  consumer sees the cause;
- the token is no direct read of the table settings of another module but the query `settings.value.get` over the bus (Р-7);
- an async httpx with a timeout instead of requests in a thread.

The forms of the answers search/details repeat today's contract of the front end; their fate is decided at stage 5 of the
revision, here they are "as now". The answers are the models of core/contracts/metadata.py, handed over as they are.

Queries (the answer is the model):
  metadata.search {query} -> TmdbSearchReply: the titles TMDB finds, asked in Russian and in English beside it (a title with no
      Russian name is told by TMDB in its own letters); the English is an enrichment, its failure does not fail the search.
  metadata.details {tmdb_id} -> TmdbDetails: the names, the poster, the status and the seasons of a show.
  metadata.map.get {series_id} -> MapReply: the mapping of the series to TMDB, none when it has none.
  metadata.map.list {} -> MapListReply: the mappings of every series.
Commands:
  metadata.map.set {series_id, tmdb_data} -> MapSaved: the mapping is written, and the count of the episodes of a series of several
      seasons is made again at once, from the seasons the naming already knows (a command `metadata.seasons.recompute` to itself).
  metadata.seasons.recompute {series_id} -> SeasonsRecounted: the total of a series of several seasons over its real seasons (the
      seasons of the files of the plan, planner.seasons; season 0, the specials, is left out), the episodes of each season from TMDB
      (the cache is in the mapping; the network only for a season that is new), the sum in the mapping; tells `series.updated` with
      the mapping, so that the card shows it without a reload. Driven by events (a scan, a rename, an adoption, a layout), no timers
      (decision #3); a series with its season set is not touched, its total is right already (decision #2: no specials).
      The sum and the choice of what to count are pure and live in calc/metadata/seasons.py.
Events in: series.deleted (the cascade of Р-19: the module clears its own). Events out: series.updated, series.purged.
"""
from __future__ import annotations

import asyncio

import httpx

from calc.metadata.seasons import cached_counts, counts_of, holds_every, real_seasons, total_of
from core.contracts import (MapGet, MapListReply, MapReply, MapSaved, MapSet, SeasonsRecompute, SeasonsRecounted, SeriesCard,
                            SeriesDeleted, SeriesPurged, SeriesUpdated, TmdbDetails, TmdbDetailsRequest, TmdbFound, TmdbMapping,
                            TmdbSearchReply, TmdbSearchRequest, TmdbSeason)
from core.module import BaseModule, BusRequestError
from core.db import Database
from core.envelope import Envelope
from core.reasons import reason
from core.topics import (CATALOG_SERIES_GET, METADATA_DETAILS, METADATA_MAP_GET, METADATA_MAP_LIST, METADATA_MAP_SET,
                         METADATA_SEARCH, METADATA_SEASONS_RECOMPUTE, PLANNER_SEASONS, SERIES_DELETED, SERIES_PURGED,
                         SERIES_UPDATED, SETTINGS_VALUE_GET)

from .repository import MetadataRepository

BASE_URL = "https://api.themoviedb.org/3"


class TmdbError(RuntimeError):
    pass


class MetadataModule(BaseModule):
    name = "metadata"

    def __init__(self, bus, db: Database) -> None:
        self.repo = MetadataRepository(db)
        super().__init__(bus)

    def register(self) -> None:
        self.handle(METADATA_SEARCH, self.on_search)
        self.handle(METADATA_DETAILS, self.on_details)
        self.handle(METADATA_MAP_GET, self.on_map_get)
        self.handle(METADATA_MAP_LIST, self.on_map_list)
        self.handle(METADATA_MAP_SET, self.on_map_set)
        self.handle(METADATA_SEASONS_RECOMPUTE, self.on_seasons_recompute)
        self.handle(SERIES_DELETED, self.on_series_deleted)

    async def on_start(self) -> None:
        self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=10.0)

    async def on_stop(self) -> None:
        await self._http.aclose()

    async def _headers(self) -> dict:
        token = (await self.reply_of(SETTINGS_VALUE_GET, {"key": "tmdb_token"})).value
        if not token:
            raise TmdbError(reason("tmdb_token_missing"))
        return {"accept": "application/json",
                "Authorization": f"Bearer {token}"}

    async def _get(self, path: str, params: dict) -> dict:
        resp = await self._http.get(path, params=params,
                                    headers=await self._headers())
        resp.raise_for_status()
        return resp.json()

    async def on_search(self, env: Envelope) -> TmdbSearchReply:
        query = TmdbSearchRequest.model_validate(env.payload).query
        params = {"query": query, "include_adult": "false", "page": "1"}
        # Two requests in parallel: ru-RU (as before) and en-US, so that a title with no Russian translation (Asian releases:
        # TMDB gives the original hieroglyphs in `name`) has a readable English name. The English is an optional enrichment:
        # its failure does not fail the search.
        data, en_data = await asyncio.gather(
            self._get("/search/tv", {**params, "language": "ru-RU"}),
            self._get("/search/tv", {**params, "language": "en-US"}),
            return_exceptions=True,
        )
        if isinstance(data, BaseException):
            raise data
        en_names = ({} if isinstance(en_data, BaseException)
                    else {r.get("id"): r.get("name")
                          for r in en_data.get("results", [])})
        return TmdbSearchReply(results=tuple(TmdbFound(
            id=r.get("id"), name=r.get("name"), name_en=en_names.get(r.get("id")), original_name=r.get("original_name"),
            year=(r.get("first_air_date") or "")[:4], poster_path=r.get("poster_path"), overview=r.get("overview") or "",
        ) for r in data.get("results", [])))

    # --- series_tmdb_mappings (our table, Р-19) -------------------------------------

    async def on_map_get(self, env: Envelope) -> MapReply:
        return MapReply(mapping=await self.repo.get_mapping(MapGet.model_validate(env.payload).series_id))

    async def on_map_list(self, env: Envelope) -> MapListReply:
        return MapListReply(mappings=tuple(await self.repo.all_mappings()))

    async def on_map_set(self, env: Envelope) -> MapSaved:
        command = MapSet.model_validate(env.payload)
        await self.repo.upsert_mapping(command.series_id, command.tmdb_data)
        # An assignment or a change of TMDB makes the aggregate of a series of several seasons again AT ONCE, from the seasons
        # the naming already knows, without waiting for the next scan (or the count of the episodes of such a series does not
        # appear after the assignment).
        self.send_command(METADATA_SEASONS_RECOMPUTE, SeasonsRecompute(series_id=command.series_id).model_dump())
        return MapSaved(ok=True)

    async def on_series_deleted(self, env: Envelope) -> None:
        """The cascade of Р-19: the owner clears its own on the event."""
        series_id = SeriesDeleted.model_validate(env.payload).series_id
        await self.repo.delete_for_series(series_id)
        self.publish_event(SERIES_PURGED, SeriesPurged(series_id=series_id, owner=self.name).model_dump(mode="json"))

    async def on_details(self, env: Envelope) -> TmdbDetails:
        return await self._details(TmdbDetailsRequest.model_validate(env.payload).tmdb_id)

    async def _details(self, tmdb_id: int) -> TmdbDetails:
        data = await self._get(f"/tv/{tmdb_id}", {"language": "ru-RU"})
        return TmdbDetails(
            name=data.get("name"),
            # The name in the language of the title (Chinese for a donghua): the search of the provider of external sources asks by it.
            original_name=data.get("original_name"),
            poster_path=data.get("poster_path"),
            status=data.get("status"),
            seasons=tuple(TmdbSeason(
                season_number=s.get("season_number"), episode_count=s.get("episode_count"),
                air_date=s.get("air_date"), name=s.get("name")) for s in data.get("seasons", [])))

    # --- the aggregate of a series of several seasons (the episodes by the real seasons) ---

    async def on_seasons_recompute(self, env: Envelope) -> SeasonsRecounted:
        series_id = SeasonsRecompute.model_validate(env.payload).series_id
        mapping = await self.repo.get_mapping(series_id)
        if not mapping or not mapping.tmdb_id:
            return SeasonsRecounted(skipped="no_tmdb")
        series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
        if (series.season or "").strip():
            return SeasonsRecounted(skipped="single_season")
        real = await self._real_seasons(series)
        if not real:
            return SeasonsRecounted(skipped="no_seasons")
        counts = await self._season_counts(mapping, real)
        total = total_of(counts, real)
        await self.repo.set_aggregate(series_id, total, counts)
        counted = await self.repo.get_mapping(series_id)
        self.publish_event(SERIES_UPDATED, SeriesUpdated(
            series_id=series_id, tmdb_info=counted.model_dump() if counted else None).model_dump(mode="json", exclude_unset=True))
        self.log.info("агрегат TMDB серии %d: сезоны %s → %d серий",
                      series_id, sorted(real), total)
        return SeasonsRecounted(total=total, seasons=tuple(sorted(real)))

    async def _real_seasons(self, series: SeriesCard) -> frozenset[int]:
        """The real seasons from the naming (not 0): the seasons of the files of the plan of the series (planner.seasons; Р-7,
        what is another's is a query)."""
        try:
            reply = await self.reply_of(PLANNER_SEASONS, {"series_id": series.id})
        except BusRequestError as exc:
            self.log.warning("сезоны серии %d недоступны (%s): %s",
                             series.id, PLANNER_SEASONS.name, exc)
            return frozenset()
        return real_seasons(reply.seasons)

    async def _season_counts(self, mapping: TmdbMapping, real: frozenset[int]) -> dict[int, int]:
        """The episodes of each season of the show. The cache is in the mapping; TMDB is asked only when the real seasons
        include one that is not in the cache (a new season came out)."""
        cache = cached_counts(mapping.season_episode_counts)
        if not holds_every(cache, real):
            cache = counts_of((await self._details(mapping.tmdb_id)).seasons)
        return cache
