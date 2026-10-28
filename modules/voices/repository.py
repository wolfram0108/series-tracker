"""SQL of the voices module: the table voice_dubs (additional voice-overs of an episode)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


class VoicesRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _row(row: dict | None) -> dict | None:
        if row is not None:
            row["chain"] = json.loads(row["chain"])
        return row

    async def for_series(self, series_id: int) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM voice_dubs WHERE series_id=? ORDER BY season, episode, voice_key", (series_id,))
        return [self._row(row) for row in rows]

    async def get(self, dub_id: int) -> dict[str, Any] | None:
        return self._row(await self._db.fetch_one("SELECT * FROM voice_dubs WHERE id=?", (dub_id,)))

    async def upsert(self, series_id: int, season: int, episode: int, site_episode: int,
                     voice_key: str, label: str, chain: list[dict[str, str]]) -> None:
        """Offer the voice-over again: an untouched row takes the fresh chain and starts from its
        main source; a row that is downloading or ready is left alone. An empty chain is a failure."""
        status = "pending" if chain else "failed"
        error = None if chain else "серии нет ни в одном источнике"
        await self._db.execute(
            "INSERT INTO voice_dubs (series_id, season, episode, site_episode, voice_key, "
            "voice_label, chain, status, error, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (series_id, season, episode, voice_key) DO UPDATE SET "
            "site_episode=excluded.site_episode, voice_label=excluded.voice_label, "
            "chain=excluded.chain, attempt=0, status=excluded.status, error=excluded.error, "
            "updated_at=excluded.updated_at WHERE voice_dubs.status IN ('pending', 'failed')",
            (series_id, season, episode, site_episode, voice_key, label,
             json.dumps(chain, ensure_ascii=False), status, error, _now()))

    async def pending(self, series_id: int | None = None) -> list[dict[str, Any]]:
        where, args = ("WHERE status='pending'", ())
        if series_id is not None:
            where, args = ("WHERE status='pending' AND series_id=?", (series_id,))
        rows = await self._db.fetch_all(f"SELECT * FROM voice_dubs {where} ORDER BY id", args)
        return [self._row(row) for row in rows]

    async def mark_downloading(self, dub_id: int, temp_path: str) -> None:
        await self._db.execute(
            "UPDATE voice_dubs SET status='downloading', temp_path=?, error=NULL, updated_at=? WHERE id=?",
            (temp_path, _now(), dub_id))

    async def mark_ready(self, dub_id: int) -> None:
        await self._db.execute(
            "UPDATE voice_dubs SET status='ready', error=NULL, updated_at=? WHERE id=?", (_now(), dub_id))

    async def advance(self, dub_id: int, error: str) -> None:
        """The current source failed: the next one of the chain is tried."""
        await self._db.execute(
            "UPDATE voice_dubs SET attempt=attempt+1, status='pending', error=?, updated_at=? WHERE id=?",
            (error, _now(), dub_id))

    async def mark_failed(self, dub_id: int, error: str) -> None:
        await self._db.execute(
            "UPDATE voice_dubs SET status='failed', error=?, updated_at=? WHERE id=?",
            (error, _now(), dub_id))

    async def requeue_interrupted(self) -> int:
        return await self._db.execute(
            "UPDATE voice_dubs SET status='pending' WHERE status='downloading'")

    async def drop_unplanned(self, series_id: int,
                             planned: set[tuple[int, int, str]]) -> list[dict[str, Any]]:
        """Rows of voice-overs that left the plan and have no file; returns what was removed."""
        gone = [row for row in await self.for_series(series_id)
                if (row["season"], row["episode"], row["voice_key"]) not in planned
                and row["status"] in ("pending", "failed")]
        for row in gone:
            await self._db.execute("DELETE FROM voice_dubs WHERE id=?", (row["id"],))
        return gone

    async def mark_pending(self, dub_id: int, error: str) -> None:
        await self._db.execute(
            "UPDATE voice_dubs SET status='pending', attempt=0, error=?, updated_at=? WHERE id=?",
            (error, _now(), dub_id))

    async def delete_for_series(self, series_id: int) -> None:
        await self._db.execute("DELETE FROM voice_dubs WHERE series_id=?", (series_id,))
