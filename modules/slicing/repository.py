"""Репозиторий slicing: таблицы slicing_tasks, sliced_files и колонки
media_items.slicing_status / chapters / chapters_filtered (остальные
колонки media_items читаются, пишутся владельцами).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.db import Database


class SlicingRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- задачи -------------------------------------------------------------------

    async def create_task(self, unique_id: str, series_id: int) -> int:
        await self._db.execute(
            "INSERT INTO slicing_tasks (media_item_unique_id, series_id, "
            "status, progress_chapters, created_at) VALUES "
            "(?, ?, 'pending', '{}', ?)",
            (unique_id, series_id,
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")))
        row = await self._db.fetch_one(
            "SELECT id FROM slicing_tasks WHERE media_item_unique_id=? "
            "ORDER BY id DESC LIMIT 1", (unique_id,))
        return row["id"]

    async def delete_task_by_uid(self, unique_id: str) -> None:
        await self._db.execute(
            "DELETE FROM slicing_tasks WHERE media_item_unique_id=?",
            (unique_id,))

    async def delete_task(self, task_id: int) -> None:
        await self._db.execute(
            "DELETE FROM slicing_tasks WHERE id=?", (task_id,))

    async def next_pending(self) -> dict | None:
        return await self._db.fetch_one(
            "SELECT * FROM slicing_tasks WHERE status='pending' "
            "ORDER BY created_at LIMIT 1")

    async def all_tasks(self) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM slicing_tasks ORDER BY created_at")

    async def set_task_status(self, task_id: int, status: str,
                              error_message: str | None = None) -> None:
        await self._db.execute(
            "UPDATE slicing_tasks SET status=?, error_message=? WHERE id=?",
            (status, error_message, task_id))

    async def set_task_progress(self, task_id: int, progress: dict) -> None:
        await self._db.execute(
            "UPDATE slicing_tasks SET progress_chapters=? WHERE id=?",
            (json.dumps(progress, ensure_ascii=False), task_id))

    async def requeue_interrupted(self) -> int:
        return await self._db.execute(
            "UPDATE slicing_tasks SET status='pending' WHERE "
            "status='slicing'")

    # --- sliced_files ----------------------------------------------------------------

    async def add_sliced_file(self, series_id: int, source_uid: str,
                              episode: int, path: str) -> None:
        existing = await self._db.fetch_one(
            "SELECT id FROM sliced_files WHERE "
            "source_media_item_unique_id=? AND episode_number=?",
            (source_uid, episode))
        if existing:
            return
        await self._db.execute(
            "INSERT INTO sliced_files (series_id, "
            "source_media_item_unique_id, episode_number, file_path, "
            "status) VALUES (?, ?, ?, ?, 'completed')",
            (series_id, source_uid, episode, path))

    async def sliced_for_source(self, source_uid: str) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM sliced_files WHERE "
            "source_media_item_unique_id=?", (source_uid,))

    async def sliced_for_series(self, series_id: int) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM sliced_files WHERE series_id=?", (series_id,))

    async def delete_sliced_for_source(self, source_uid: str) -> int:
        return await self._db.execute(
            "DELETE FROM sliced_files WHERE source_media_item_unique_id=?",
            (source_uid,))

    async def set_sliced_status(self, file_id: int, status: str) -> None:
        await self._db.execute(
            "UPDATE sliced_files SET status=? WHERE id=?", (status, file_id))

    async def set_sliced_path(self, file_id: int, path: str) -> None:
        await self._db.execute(
            "UPDATE sliced_files SET file_path=? WHERE id=?",
            (path, file_id))

    # --- media_items: наши колонки ----------------------------------------------------

    async def get_item(self, unique_id: str) -> dict | None:
        return await self._db.fetch_one(
            "SELECT * FROM media_items WHERE unique_id=?", (unique_id,))

    async def set_slicing_status(self, unique_id: str, status: str) -> None:
        await self._db.execute(
            "UPDATE media_items SET slicing_status=? WHERE unique_id=?",
            (status, unique_id))

    async def set_chapters(self, unique_id: str,
                           chapters: list[dict] | None = None,
                           filtered: list[dict] | None = None) -> None:
        if chapters is not None:
            await self._db.execute(
                "UPDATE media_items SET chapters=? WHERE unique_id=?",
                (json.dumps(chapters, ensure_ascii=False), unique_id))
        if filtered is not None:
            await self._db.execute(
                "UPDATE media_items SET chapters_filtered=? WHERE "
                "unique_id=?",
                (json.dumps(filtered, ensure_ascii=False), unique_id))

    # --- свёртка (Р-11): slicing/error по плановым элементам ---------------------------

    async def series_flags(self, series_id: int) -> dict:
        rows = await self._db.fetch_all(
            "SELECT slicing_status FROM media_items WHERE series_id=? AND "
            "plan_status IN ('in_plan_single', 'in_plan_compilation') AND "
            "is_ignored_by_user=0", (series_id,))
        statuses = [r["slicing_status"] for r in rows]
        return {"slicing": "slicing" in statuses,
                "error": "error" in statuses}

    async def series_with_activity(self) -> list[int]:
        rows = await self._db.fetch_all(
            "SELECT DISTINCT series_id FROM media_items WHERE "
            "slicing_status IN ('slicing', 'error')")
        return [r["series_id"] for r in rows]
