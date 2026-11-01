"""The one place where tests create a series together with its sources (stage 10, Ис-3).

A series has at least one source (Д-38): its address, kind and profile live in `series_sources`, so a test never
writes the columns of `series` and the row of its source by hand. It calls the factory, and the day the shape of
those tables moves, only this file follows.

Two forms, the same arguments:

- `insert_series(conn, ...)` writes with plain SQL into an open `sqlite3` connection, for a database that is filled
  before any module starts. The caller commits, as with any other insert on its connection.
- `await create_series(where, ...)` goes through the catalog: `where` is a module that sends requests
  (`catalog.series.create` over the bus) or a `Database` (the repository of the catalog, no bus needed).

The series takes the defaults of `SERIES_DEFAULTS` for the columns not given; the season stays unset, as it was in
the tests that left it out. The sources are given as a list of dicts (`source(...)` makes one), or, for the usual
series with a single source, as the fields of that source next to the fields of the series:

    insert_series(conn, id=1, url="https://vkvideo.ru/@c|q", source_type="vk_video", parser_profile_id=1)

The result names the series and its sources, in the order they were given.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from core.db import Database
from modules.catalog.repository import CatalogRepository
from modules.catalog.series_sources import checked_sources

SERIES_DEFAULTS = {
    "name": "Тайтл",
    "name_en": "Title",
    "site": "kinozal",
    "save_path": "/media/t",
    "season": None,
    "auto_scan_enabled": 0,
}

SOURCE_DEFAULTS = {
    "url": "https://kinozal.tv/details.php?id=1",
    "source_type": "torrent",
    "parser_profile_id": None,
    "vk_search_mode": "search",
    "vibix_season": None,
    "players_off": [],
}


@dataclass(frozen=True)
class Created:
    series_id: int
    source_ids: tuple[int, ...]

    @property
    def source_id(self) -> int:
        """The only source of a series made with one; the first of several."""
        return self.source_ids[0]


def source(**fields) -> dict:
    """One source with the defaults of `SOURCE_DEFAULTS` for the fields not given."""
    unknown = set(fields) - set(SOURCE_DEFAULTS) - {"position"}
    if unknown:
        raise TypeError(f"not a field of a source: {sorted(unknown)}")
    return {**SOURCE_DEFAULTS, **fields}


def _split(fields: dict, sources: list[dict] | None) -> tuple[dict, list[dict]]:
    """Fields of the series, and its sources: the given list or the one made of the source fields among `fields`."""
    own = {name: fields.pop(name) for name in list(fields) if name in SOURCE_DEFAULTS or name == "position"}
    if sources is not None and own:
        raise TypeError(f"give either sources or the fields of a single source, not both: {sorted(own)}")
    return {**SERIES_DEFAULTS, **fields}, [source(**field) for field in sources] if sources is not None \
        else [source(**own)]


def insert_series(conn: sqlite3.Connection, *, id: int | None = None, sources: list[dict] | None = None,
                  **fields) -> Created:
    series, drafts = _split(fields, sources)
    if id is not None:
        series["id"] = id
    cursor = conn.execute(
        f"INSERT INTO series ({', '.join(series)}) VALUES ({', '.join('?' for _ in series)})",
        tuple(series.values()))
    series_id = cursor.lastrowid
    source_ids = []
    for position, draft in enumerate(drafts):
        draft = {"position": position, **draft}
        players_off = json.dumps(draft["players_off"], ensure_ascii=False)
        source_ids.append(conn.execute(
            "INSERT INTO series_sources (series_id, position, url, source_type, parser_profile_id, "
            "vk_search_mode, vibix_season, players_off) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (series_id, draft["position"], draft["url"], draft["source_type"], draft["parser_profile_id"],
             draft["vk_search_mode"], draft["vibix_season"], players_off)).lastrowid)
    return Created(series_id, tuple(source_ids))


async def create_series(where, *, sources: list[dict] | None = None, **fields) -> Created:
    series, drafts = _split(fields, sources)
    if isinstance(where, Database):
        series_id = await CatalogRepository(where).create_series(series, checked_sources(drafts))
        stored = await CatalogRepository(where).list_sources(series_id)
    else:
        created = await where.request("catalog.series.create", {"data": {**series, "sources": drafts}}, timeout=10)
        series_id, stored = created["id"], created["sources"]
    return Created(series_id, tuple(item["id"] for item in stored))
