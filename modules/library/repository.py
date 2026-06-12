"""Репозиторий library: таблица relocation_tasks."""
from __future__ import annotations

from datetime import datetime, timezone

from core.db import Database


class LibraryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, series_id: int, new_path: str) -> int | None:
        """None — активная задача уже есть (отказ, как в оригинале)."""
        existing = await self._db.fetch_one(
            "SELECT id FROM relocation_tasks WHERE series_id=? AND "
            "status IN ('pending', 'in_progress')", (series_id,))
        if existing:
            return None
        await self._db.execute(
            "INSERT INTO relocation_tasks (series_id, new_path, status, "
            "created_at) VALUES (?, ?, 'pending', ?)",
            (series_id, new_path,
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")))
        row = await self._db.fetch_one(
            "SELECT id FROM relocation_tasks WHERE series_id=? "
            "ORDER BY id DESC LIMIT 1", (series_id,))
        return row["id"]

    async def unfinished(self) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM relocation_tasks WHERE status IN "
            "('pending', 'in_progress') ORDER BY created_at")

    async def set_status(self, task_id: int, status: str,
                         error_message: str | None = None) -> None:
        await self._db.execute(
            "UPDATE relocation_tasks SET status=?, error_message=? "
            "WHERE id=?", (status, error_message, task_id))

    async def delete(self, task_id: int) -> None:
        await self._db.execute(
            "DELETE FROM relocation_tasks WHERE id=?", (task_id,))

    async def active_for_series(self, series_id: int) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM relocation_tasks WHERE series_id=?", (series_id,))

    async def delete_for_series(self, series_id: int) -> None:
        """Каскад Р-19: серия удалена — задачи перемещения."""
        await self._db.execute(
            "DELETE FROM relocation_tasks WHERE series_id=?", (series_id,))
