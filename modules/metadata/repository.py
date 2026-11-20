"""Repository of metadata: the table series_tmdb_mappings (Р-19)."""
from __future__ import annotations

from datetime import datetime, timezone

from calc.metadata.seasons import stored_counts
from core.contracts import TmdbMapping
from core.db import Database

_FIELDS = ("tmdb_id", "tmdb_season_number", "total_episodes",
           "poster_path", "series_name", "year")
# The columns of the record in the order of the table, so that the model is the same record whatever a migration adds later.
_COLUMNS = ("series_id, tmdb_id, tmdb_season_number, total_episodes, last_updated, poster_path, series_name, year, "
            "season_episode_counts")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


class MetadataRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_mapping(self, series_id: int) -> TmdbMapping | None:
        row = await self._db.fetch_one(
            f"SELECT {_COLUMNS} FROM series_tmdb_mappings WHERE series_id=?",
            (series_id,))
        return TmdbMapping(**row) if row else None

    async def all_mappings(self) -> list[TmdbMapping]:
        return [TmdbMapping(**row) for row in await self._db.fetch_all(f"SELECT {_COLUMNS} FROM series_tmdb_mappings")]

    async def upsert_mapping(self, series_id: int, data: dict) -> None:
        fields = {k: data[k] for k in _FIELDS if k in data}
        existing = await self.get_mapping(series_id)
        if existing:
            sets = ", ".join(f"{k}=?" for k in fields)
            await self._db.execute(
                f"UPDATE series_tmdb_mappings SET {sets}, last_updated=? "
                "WHERE series_id=?",
                (*fields.values(), _now(), series_id))
        else:
            fields.setdefault("total_episodes", 0)
            cols = ", ".join(fields)
            marks = ", ".join("?" for _ in fields)
            await self._db.execute(
                f"INSERT INTO series_tmdb_mappings (series_id, {cols}, "
                f"last_updated) VALUES (?, {marks}, ?)",
                (series_id, *fields.values(), _now()))

    async def set_aggregate(self, series_id: int, total_episodes: int,
                            season_counts: dict) -> None:
        """The aggregate of a series of several seasons: total_episodes is the sum over the real seasons,
        season_episode_counts the cache of the episodes of every season of the show (TMDB)."""
        await self._db.execute(
            "UPDATE series_tmdb_mappings SET total_episodes=?, "
            "season_episode_counts=?, last_updated=? WHERE series_id=?",
            (total_episodes, stored_counts(season_counts), _now(), series_id))

    async def delete_for_series(self, series_id: int) -> None:
        await self._db.execute(
            "DELETE FROM series_tmdb_mappings WHERE series_id=?",
            (series_id,))
