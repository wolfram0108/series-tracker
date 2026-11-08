"""The SQL of downloads: the columns media_items.status and final_filename the torrent pipeline writes through it (the
rows are scan's, slicing_* are slicing's). The downloads of a video series keep no record here: their owner (the planner)
keeps its own table."""
from __future__ import annotations

from core.db import Database


class DownloadsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def set_item_filename(self, unique_id: str, filename: str) -> None:
        """Slicing reports the name of a file it made or took over."""
        await self._db.execute("UPDATE media_items SET final_filename=?, rename_to=NULL WHERE unique_id=?",
                               (filename, unique_id))

    async def set_item_status(self, unique_id: str, status: str) -> None:
        await self._db.execute("UPDATE media_items SET status=? WHERE unique_id=?", (status, unique_id))
