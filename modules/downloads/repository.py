"""The SQL of downloads: the cards of the downloads that stay after their sync (download_cards), and the files fetches
ended well with (download_done). What a video series downloads is its owner's (the planner keeps its own table); a file
kept here only spares the same fetch, asked again, a second ask of its source."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.contracts import DownloadCard
from core.db import Database


class DownloadsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def cards(self) -> list[DownloadCard]:
        return [DownloadCard.model_validate(json.loads(row["data"]))
                for row in await self._db.fetch_all("SELECT data FROM download_cards ORDER BY rowid")]

    async def keep_cards(self, cards: list[DownloadCard]) -> None:
        if cards:
            await self._db.execute_many([(
                "INSERT INTO download_cards (id, series_id, data) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET series_id = excluded.series_id, data = excluded.data",
                (card.id, card.need.series_id, card.model_dump_json())) for card in cards])

    async def done_of(self, path: str) -> dict | None:
        """The fetch that ended well with the file at the path: its link, its cap, and the size and time of change of the
        file then."""
        return await self._db.fetch_one("SELECT url, max_height, size, mtime_ns FROM download_done WHERE path = ?", (path,))

    async def keep_done(self, path: str, url: str, max_height: int | None, size: int, mtime_ns: int) -> None:
        await self._db.execute(
            "INSERT INTO download_done (path, url, max_height, size, mtime_ns, done_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (path) DO UPDATE SET url = excluded.url, max_height = excluded.max_height, size = excluded.size,"
            " mtime_ns = excluded.mtime_ns, done_at = excluded.done_at",
            (path, url, max_height, size, mtime_ns, datetime.now(timezone.utc).isoformat()))

    async def done_paths(self) -> list[str]:
        return [row["path"] for row in await self._db.fetch_all("SELECT path FROM download_done")]

    async def forget_done(self, path: str) -> None:
        await self._db.execute("DELETE FROM download_done WHERE path = ?", (path,))

    async def drop_cards(self, ids: list[str]) -> None:
        if ids:
            await self._db.execute_many([("DELETE FROM download_cards WHERE id = ?", (each,)) for each in ids])
