"""Репозиторий renaming: таблица renaming_tasks.

Запись существует на время работы (in_progress) — это носитель
ресьюмабельности и семантики is_busy; успех — удаление, ошибка —
status='error' с сообщением (Р-11). Тип single_vk не воспроизводится
(находка 33: механизм был мёртв).
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.db import Database


class RenamingRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, series_id: int, task_type: str) -> int:
        await self._db.execute(
            "INSERT INTO renaming_tasks (series_id, status, task_type, "
            "created_at) VALUES (?, 'in_progress', ?, ?)",
            (series_id, task_type,
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")))
        row = await self._db.fetch_one(
            "SELECT id FROM renaming_tasks WHERE series_id=? "
            "ORDER BY id DESC LIMIT 1", (series_id,))
        return row["id"]

    async def delete(self, task_id: int) -> None:
        await self._db.execute(
            "DELETE FROM renaming_tasks WHERE id=?", (task_id,))

    async def set_error(self, task_id: int, message: str) -> None:
        await self._db.execute(
            "UPDATE renaming_tasks SET status='error', error_message=? "
            "WHERE id=?", (message, task_id))

    async def delete_errors(self, series_id: int) -> None:
        """Новый запуск переобработки сбрасывает прошлую ошибку."""
        await self._db.execute(
            "DELETE FROM renaming_tasks WHERE series_id=? AND "
            "status='error'", (series_id,))

    async def unfinished(self) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM renaming_tasks WHERE status IN "
            "('pending', 'in_progress')")

    async def active_for_series(self, series_id: int) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM renaming_tasks WHERE series_id=?", (series_id,))
