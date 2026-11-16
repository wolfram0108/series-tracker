"""The SQL of downloads: the cards of the downloads that stay after their sync (download_cards). The downloads of a video
series keep no record of their work here: their owner (the planner) keeps its own table."""
from __future__ import annotations

import json

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

    async def drop_cards(self, ids: list[str]) -> None:
        if ids:
            await self._db.execute_many([("DELETE FROM download_cards WHERE id = ?", (each,)) for each in ids])
