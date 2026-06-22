"""Репозиторий metadata: таблица series_tmdb_mappings (Р-19)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.db import Database

_FIELDS = ("tmdb_id", "tmdb_season_number", "total_episodes",
           "poster_path", "series_name", "year")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


class MetadataRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_mapping(self, series_id: int) -> dict | None:
        return await self._db.fetch_one(
            "SELECT * FROM series_tmdb_mappings WHERE series_id=?",
            (series_id,))

    async def all_mappings(self) -> list[dict]:
        return await self._db.fetch_all("SELECT * FROM series_tmdb_mappings")

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
        """Многосезонный агрегат: total_episodes = сумма по реальным сезонам,
        season_episode_counts = кэш числа серий по всем сезонам шоу (TMDB)."""
        await self._db.execute(
            "UPDATE series_tmdb_mappings SET total_episodes=?, "
            "season_episode_counts=?, last_updated=? WHERE series_id=?",
            (total_episodes,
             json.dumps({str(k): v for k, v in season_counts.items()}),
             _now(), series_id))

    async def delete_for_series(self, series_id: int) -> None:
        await self._db.execute(
            "DELETE FROM series_tmdb_mappings WHERE series_id=?",
            (series_id,))
