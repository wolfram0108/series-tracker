"""Repository of scan: the tables scan_tasks, scan_references and media_items.

media_items: the rows and plan_status belong to scan (agreed in place of catalog); status and final_filename belong
to downloads, slicing_status and chapters* to slicing and are never written here.

scan_references: the outcome of the choice of the reference for every slot (season, episode, part), including the
slots that have no row in media_items (`none`, `unfit`).

scan_tasks: the journal that lets a torrent scan resume. The plan of replacements is persisted BEFORE it is carried
out (task_data) and the results of the additions as they come (results_data); status='error' with a message is the
carrier of the error flag of the status fold (R-11).
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
        # Сообщение — в results_data['error']: схема фиксирована,
        # отдельной колонки под текст ошибки у scan_tasks нет.
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
        """Новый скан сбрасывает прошлую ошибку (Р-11: сброс — повторным
        сканом или действием пользователя)."""
        await self._db.execute(
            "DELETE FROM scan_tasks WHERE series_id=? AND status='error'",
            (series_id,))

    @staticmethod
    def _task_row(row: dict) -> dict:
        row["task_data"] = json.loads(row["task_data"] or "[]")
        row["results_data"] = json.loads(row["results_data"] or "{}")
        return row

    # --- media_items: строки и план ---------------------------------------------

    _BOOLS = ("is_ignored_by_user", "is_available")

    async def items_for_series(self, series_id: int) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM media_items WHERE series_id=?", (series_id,))
        return self._db.coerce_bools(rows, self._BOOLS)

    async def downloaded_counts(self) -> dict[int, int]:
        """Скачанные эпизоды по сериям (final_filename есть) — батч для
        карточек списка (устранение N+1 старого GET /api/series)."""
        rows = await self._db.fetch_all(
            "SELECT series_id, COUNT(*) AS n FROM media_items "
            "WHERE final_filename IS NOT NULL "
            "AND (season IS NULL OR season != 0) GROUP BY series_id")
        return {r["series_id"]: r["n"] for r in rows}

    async def seasons_for_series(self, series_id: int) -> list[int]:
        """Реальные сезоны серии — distinct media_items.season (для агрегата
        TMDB по нескольким сезонам)."""
        rows = await self._db.fetch_all(
            "SELECT DISTINCT season FROM media_items "
            "WHERE series_id=? AND season IS NOT NULL", (series_id,))
        return [r["season"] for r in rows if r["season"] is not None]

    async def delete_for_series(self, series_id: int) -> None:
        """Каскад Р-19: серия удалена — наши таблицы чистятся."""
        await self._db.execute(
            "DELETE FROM media_items WHERE series_id=?", (series_id,))
        await self._db.execute(
            "DELETE FROM scan_tasks WHERE series_id=?", (series_id,))
        await self._db.execute(
            "DELETE FROM scan_references WHERE series_id=?", (series_id,))

    async def set_ignored(self, unique_id: str, ignored: bool) -> None:
        await self._db.execute(
            "UPDATE media_items SET is_ignored_by_user=? WHERE unique_id=?",
            (1 if ignored else 0, unique_id))

    async def set_ignored_bulk_for_season(self, series_id: int, season: int,
                                          ignored: bool) -> int:
        return await self._db.execute(
            "UPDATE media_items SET is_ignored_by_user=? "
            "WHERE series_id=? AND season=?",
            (1 if ignored else 0, series_id, season))

    async def set_ignored_for_episode(self, series_id: int, season: int, episode: int, ignored: bool) -> int:
        """All rows of one episode of a season (its parts and its versions): the user's choice is about the episode."""
        return await self._db.execute(
            "UPDATE media_items SET is_ignored_by_user=? WHERE series_id=? AND season=? AND episode_start=?",
            (1 if ignored else 0, series_id, season, episode))

    async def get_item(self, unique_id: str) -> dict | None:
        return await self._db.fetch_one(
            "SELECT * FROM media_items WHERE unique_id=?", (unique_id,))

    async def references(self, series_id: int) -> list[dict]:
        """Stored outcome of the choice, one record per slot; the slot with no part sorts first."""
        return await self._db.fetch_all(
            "SELECT season, episode, part, mark, source_id, row_key, unique_id, player, voice_group, height, reason "
            "FROM scan_references WHERE series_id=? "
            "ORDER BY season, episode, part IS NOT NULL, part", (series_id,))

    async def save_outcome(self, series_id: int, *, inserts: list[dict], updates: list[tuple[str, dict]],
                           deletes: list[str], statuses: dict[str, str], superseded: dict[str, str],
                           references: list[dict]) -> None:
        """Rows, plan and references of the series in one transaction: readers never see a plan that does not
        match its references. Rows the planner skipped (switched off) fall back to 'candidate'; the columns that
        belong to other modules (status, final_filename, slicing_*) are never written here."""
        statements: list[tuple[str, tuple]] = [
            ("DELETE FROM media_items WHERE unique_id=?", (unique_id,)) for unique_id in deletes]
        for row in inserts:
            statements.append((
                "INSERT INTO media_items (series_id, unique_id, source_title, season, episode_start, episode_end, "
                "part, plan_status, status, is_ignored_by_user, source_url, publication_date, voiceover_tag, "
                "slicing_status, is_available, resolution, download_url, source_id, row_key) VALUES "
                "(?, ?, ?, ?, ?, NULL, ?, 'candidate', 'pending', ?, ?, ?, ?, 'none', 1, ?, NULL, ?, ?)",
                (series_id, row["unique_id"], row["source_title"], row["season"], row["episode_start"], row["part"],
                 int(bool(row["is_ignored_by_user"])), row["source_url"], row["publication_date"],
                 row["voiceover_tag"], row.get("resolution"), row["source_id"], row["row_key"])))
        for unique_id, fields in updates:
            columns = sorted(fields)
            # A video that began to be fetched while the plan was counted keeps its address: the file is of that video.
            guard = (" AND status != 'downloading' AND final_filename IS NULL AND status != 'completed'"
                     if "source_url" in fields else "")
            statements.append((
                f"UPDATE media_items SET {', '.join(f'{name}=?' for name in columns)} WHERE unique_id=?{guard}",
                (*(fields[name] for name in columns), unique_id)))
        statements.append(("UPDATE media_items SET plan_status='candidate', superseded_by=NULL WHERE series_id=?",
                           (series_id,)))
        statements.extend((
            "UPDATE media_items SET plan_status=?, superseded_by=? WHERE unique_id=?",
            (plan_status, superseded.get(unique_id), unique_id)) for unique_id, plan_status in statuses.items())
        statements.append(("DELETE FROM scan_references WHERE series_id=?", (series_id,)))
        statements.extend((
            "INSERT INTO scan_references (series_id, season, episode, part, mark, source_id, row_key, unique_id, "
            "player, voice_group, height, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (series_id, each["season"], each["episode"], each["part"], each["mark"], each["source_id"],
             each["row_key"], each["unique_id"], each["player"], each["voice_group"], each["height"],
             each["reason"])) for each in references)
        await self._db.execute_many(statements)
