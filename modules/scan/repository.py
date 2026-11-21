"""Repository of scan: the table scan_tasks (the reference of a video series is its plan's, modules/planner).

scan_tasks: the journal that lets a torrent scan resume. The plan of replacements is persisted BEFORE it is carried
out (task_data) and the results of the additions as they come (results_data); status='error' with a message is the
carrier of the error flag of the status fold (Р-11).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.db import Database


class ScanRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- scan_tasks -----------------------------------------------------------

    async def create_task(self, series_id: int, task_data: list[dict], results: dict | None = None) -> int:
        return await self._db.insert(
            "INSERT INTO scan_tasks (series_id, created_at, status, "
            "task_data, results_data) VALUES (?, ?, 'processing', ?, ?)",
            (series_id, datetime.now(timezone.utc).isoformat(),
             json.dumps(task_data, ensure_ascii=False), json.dumps(results or {}, ensure_ascii=False)))

    async def update_results(self, task_id: int, results: dict) -> None:
        await self._db.execute(
            "UPDATE scan_tasks SET results_data=? WHERE id=?",
            (json.dumps(results, ensure_ascii=False), task_id))

    async def set_error(self, task_id: int, message: str) -> None:
        # The message goes to results_data['error']: the schema is fixed and
        # scan_tasks has no separate column for the error text.
        task = await self.get_task(task_id)
        results = task["results_data"] if task else {}
        results["error"] = message
        await self._db.execute(
            "UPDATE scan_tasks SET status='error', results_data=? WHERE id=?",
            (json.dumps(results, ensure_ascii=False), task_id))

    async def delete_task(self, task_id: int) -> None:
        await self._db.execute("DELETE FROM scan_tasks WHERE id=?", (task_id,))

    async def get_task(self, task_id: int) -> dict | None:
        row = await self._db.fetch_one(
            "SELECT * FROM scan_tasks WHERE id=?", (task_id,))
        return self._task_row(row) if row else None

    async def incomplete_tasks(self) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM scan_tasks WHERE status='processing' ORDER BY id")
        return [self._task_row(r) for r in rows]

    async def error_tasks(self) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM scan_tasks WHERE status='error' ORDER BY id")
        return [self._task_row(r) for r in rows]

    async def delete_error_tasks(self, series_id: int) -> None:
        """A new scan clears the previous error (Р-11: cleared by a repeated scan
        or by a user action)."""
        await self._db.execute(
            "DELETE FROM scan_tasks WHERE series_id=? AND status='error'",
            (series_id,))

    @staticmethod
    def _task_row(row: dict) -> dict:
        row["task_data"] = json.loads(row["task_data"] or "[]")
        row["results_data"] = json.loads(row["results_data"] or "{}")
        return row

    async def delete_for_series(self, series_id: int) -> None:
        """The cascade of Р-19: a series deleted, our table is cleaned."""
        await self._db.execute(
            "DELETE FROM scan_tasks WHERE series_id=?", (series_id,))
