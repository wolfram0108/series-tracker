"""Репозиторий library: таблица relocation_tasks."""
from __future__ import annotations

from datetime import datetime, timezone

from core.db import Database


class LibraryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # planned — written by a save, made by the next sync (И-59); in_progress — being made; pending — a move written
    # before the plans, made when found.
    async def plan(self, series_id: int, new_path: str) -> None:
        """One plan per series: a later save replaces the path of the earlier one."""
        if not await self._db.execute(
                "UPDATE relocation_tasks SET new_path=?, error_message=NULL WHERE series_id=? AND status='planned'",
                (new_path, series_id)):
            await self._db.insert(
                "INSERT INTO relocation_tasks (series_id, new_path, status, created_at) VALUES (?, ?, 'planned', ?)",
                (series_id, new_path, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")))

    async def drop_plan(self, series_id: int) -> bool:
        return bool(await self._db.execute(
            "DELETE FROM relocation_tasks WHERE series_id=? AND status='planned'", (series_id,)))

    async def moving(self, series_id: int) -> bool:
        return await self._db.fetch_one(
            "SELECT id FROM relocation_tasks WHERE series_id=? AND status IN ('pending', 'in_progress')",
            (series_id,)) is not None

    async def of_series(self, series_id: int) -> dict | None:
        return await self._db.fetch_one(
            "SELECT * FROM relocation_tasks WHERE series_id=? AND status IN ('planned', 'pending', 'in_progress') "
            "ORDER BY created_at DESC", (series_id,))

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

    async def delete_for_series(self, series_id: int) -> None:
        """Каскад Р-19: серия удалена — задачи перемещения."""
        await self._db.execute(
            "DELETE FROM relocation_tasks WHERE series_id=?", (series_id,))
