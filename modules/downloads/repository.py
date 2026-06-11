"""Репозиторий downloads: таблица download_tasks (VK-часть) и колонки
media_items.status / final_filename (колоночное владение по ТЗ;
plan_status и строки — у scan, slicing_* — у slicing: только чтение).
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.db import Database

_IN_PLAN = "('in_plan_single', 'in_plan_compilation')"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


class DownloadsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- задачи -------------------------------------------------------------------

    async def active_tasks(self) -> list[dict]:
        """pending+downloading — контракт старого download_queue_update."""
        return await self._db.fetch_all(
            "SELECT * FROM download_tasks WHERE task_type='vk_video' AND "
            "status IN ('pending', 'downloading') ORDER BY created_at")

    async def next_pending(self, limit: int) -> list[dict]:
        """Добор: только задачи, чей элемент в плане (семантика
        оригинала; выпавшие из плана чистятся в sync_plan_tasks)."""
        return await self._db.fetch_all(
            "SELECT dt.* FROM download_tasks dt "
            "JOIN media_items mi ON mi.unique_id = dt.task_key "
            f"WHERE dt.task_type='vk_video' AND dt.status='pending' "
            f"AND mi.plan_status IN {_IN_PLAN} "
            "ORDER BY dt.created_at LIMIT ?", (limit,))

    async def task_exists_active(self, unique_id: str) -> bool:
        row = await self._db.fetch_one(
            "SELECT id FROM download_tasks WHERE task_key=? AND "
            "task_type='vk_video' AND status IN ('pending', 'downloading')",
            (unique_id,))
        return row is not None

    async def replace_or_create_task(self, unique_id: str, series_id: int,
                                     video_url: str, save_path: str) -> bool:
        """Создание задачи; error-задача того же элемента заменяется
        (ретрай сканом — Р-13, вместо дублей из находки 31)."""
        if await self.task_exists_active(unique_id):
            return False
        await self._db.execute(
            "DELETE FROM download_tasks WHERE task_key=? AND "
            "task_type='vk_video' AND status='error'", (unique_id,))
        await self._db.execute(
            "INSERT INTO download_tasks (task_key, series_id, video_url, "
            "save_path, status, attempts, created_at, updated_at, "
            "task_type, progress, dlspeed, eta) VALUES "
            "(?, ?, ?, ?, 'pending', 0, ?, ?, 'vk_video', 0, 0, 0)",
            (unique_id, series_id, video_url, save_path, _now(), _now()))
        return True

    async def drop_pending_outside_plan(self, series_id: int) -> int:
        """Чистка pending-задач, выпавших из плана (находка 32)."""
        return await self._db.execute(
            "DELETE FROM download_tasks WHERE series_id=? AND "
            "task_type='vk_video' AND status='pending' AND task_key IN "
            "(SELECT unique_id FROM media_items WHERE series_id=? AND "
            f"plan_status NOT IN {_IN_PLAN})", (series_id, series_id))

    async def mark_downloading(self, task_id: int) -> None:
        await self._db.execute(
            "UPDATE download_tasks SET status='downloading', "
            "attempts=attempts+1, updated_at=? WHERE id=?",
            (_now(), task_id))

    async def mark_error(self, task_id: int, message: str) -> None:
        await self._db.execute(
            "UPDATE download_tasks SET status='error', error_message=?, "
            "updated_at=? WHERE id=?", (message, _now(), task_id))

    async def update_progress(self, task_id: int, data: dict) -> None:
        await self._db.execute(
            "UPDATE download_tasks SET progress=?, dlspeed=?, eta=?, "
            "total_size_mb=COALESCE(?, total_size_mb), updated_at=? "
            "WHERE id=?",
            (data.get("progress", 0), data.get("dlspeed", 0),
             data.get("eta", 0), data.get("total_size_mb"), _now(), task_id))

    async def delete_task(self, task_id: int) -> None:
        await self._db.execute(
            "DELETE FROM download_tasks WHERE id=?", (task_id,))

    async def clear_queue(self) -> int:
        """Удаляет все задачи, кроме идущих загрузок (контракт
        /downloads/queue/clear)."""
        return await self._db.execute(
            "DELETE FROM download_tasks WHERE task_type='vk_video' AND "
            "status != 'downloading'")

    async def requeue_interrupted(self) -> int:
        """Reconcile при старте: оборванные загрузки — снова в очередь.
        error НЕ трогаем (Р-13: ошибка не лечится рестартом)."""
        return await self._db.execute(
            "UPDATE download_tasks SET status='pending' WHERE "
            "task_type='vk_video' AND status='downloading'")

    # --- media_items: наши колонки (status, final_filename) ------------------------

    async def set_item_filename(self, unique_id: str,
                                filename: str) -> None:
        await self._db.execute(
            "UPDATE media_items SET final_filename=? WHERE unique_id=?",
            (filename, unique_id))

    async def set_item_status(self, unique_id: str, status: str) -> None:
        await self._db.execute(
            "UPDATE media_items SET status=? WHERE unique_id=?",
            (status, unique_id))

    async def register_downloaded(self, unique_id: str,
                                  filename: str) -> None:
        await self._db.execute(
            "UPDATE media_items SET status='completed', final_filename=? "
            "WHERE unique_id=?", (filename, unique_id))

    async def reset_download_state(self, unique_id: str) -> None:
        await self._db.execute(
            "UPDATE media_items SET status='pending', final_filename=NULL "
            "WHERE unique_id=?", (unique_id,))

    async def planned_pending_items(self, series_id: int) -> list[dict]:
        """pending + error: ошибка ретраится следующим сканом (Р-13) —
        старая система подхватывала error-элементы только рестартом."""
        return await self._db.fetch_all(
            "SELECT * FROM media_items WHERE series_id=? AND "
            f"plan_status IN {_IN_PLAN} AND status IN ('pending', 'error') "
            "AND is_ignored_by_user=0", (series_id,))

    async def completed_items(self, series_id: int) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM media_items WHERE series_id=? AND "
            "status='completed'", (series_id,))

    # --- свёртка статусов (семантика старого sync_vk_statuses) ---------------------

    async def series_flags(self, series_id: int) -> dict:
        planned = await self._db.fetch_all(
            "SELECT status FROM media_items WHERE series_id=? AND "
            f"plan_status IN {_IN_PLAN} AND is_ignored_by_user=0",
            (series_id,))
        statuses = [r["status"] for r in planned]
        any_completed = await self._db.fetch_one(
            "SELECT 1 AS x FROM media_items WHERE series_id=? AND "
            "status='completed' AND is_ignored_by_user=0 LIMIT 1",
            (series_id,))
        return {
            "downloading": "downloading" in statuses,
            "error": "error" in statuses,
            "ready": any_completed is not None,
            "waiting": "pending" in statuses,
        }

