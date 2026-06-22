"""Репозиторий scan: таблицы scan_tasks и media_items (строки +
plan_status — владение согласовано вместо catalog; колонки
status/final_filename принадлежат downloads, slicing_status/chapters* —
slicing, сюда не пишутся).

scan_tasks — журнал ресьюмабельности торрент-скана: план замен
персистится ДО выполнения (task_data), результаты добавлений — по мере
(results_data); status='error' с сообщением — носитель для свёртки
error (Р-11).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.db import Database


class ScanRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- scan_tasks -----------------------------------------------------------

    async def create_task(self, series_id: int, task_data: list[dict]) -> int:
        await self._db.execute(
            "INSERT INTO scan_tasks (series_id, created_at, status, "
            "task_data, results_data) VALUES (?, ?, 'processing', ?, '{}')",
            (series_id, datetime.now(timezone.utc).isoformat(),
             json.dumps(task_data, ensure_ascii=False)))
        row = await self._db.fetch_one(
            "SELECT id FROM scan_tasks WHERE series_id=? "
            "ORDER BY id DESC LIMIT 1", (series_id,))
        return row["id"]

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

    async def upsert_candidates(self, series_id: int,
                                items: list[dict]) -> dict:
        """Апсерт кандидатов скана (семантика старого
        add_or_update_media_items): обновлённым принудительно
        переписываются распарсенные поля и plan_status='candidate';
        «фантомы» (исчезли из выдачи) удаляются ТОЛЬКО в девственном
        состоянии (pending, без нарезки, не ignored) — иначе остаются.
        Чужие колонки (status, final_filename, slicing_*) не трогаются.
        """
        existing = await self._db.fetch_all(
            "SELECT unique_id, status, slicing_status, is_ignored_by_user "
            "FROM media_items WHERE series_id=?", (series_id,))
        existing_ids = {r["unique_id"] for r in existing}
        new_ids = {i["unique_id"] for i in items}

        deleted = kept_phantoms = 0
        for row in existing:
            if row["unique_id"] in new_ids:
                continue
            pristine = (row["status"] == "pending"
                        and row["slicing_status"] == "none"
                        and not row["is_ignored_by_user"])
            if pristine:
                await self._db.execute(
                    "DELETE FROM media_items WHERE unique_id=?",
                    (row["unique_id"],))
                deleted += 1
            else:
                kept_phantoms += 1

        added = updated = 0
        for item in items:
            if item["unique_id"] in existing_ids:
                await self._db.execute(
                    "UPDATE media_items SET season=?, episode_start=?, "
                    "episode_end=?, publication_date=?, voiceover_tag=?, "
                    "resolution=?, source_title=?, plan_status='candidate' "
                    "WHERE unique_id=?",
                    (item.get("season"), item["episode_start"],
                     item.get("episode_end"), item["publication_date"],
                     item.get("voiceover_tag"), item.get("resolution"),
                     item.get("source_title"), item["unique_id"]))
                updated += 1
            else:
                await self._db.execute(
                    "INSERT INTO media_items (series_id, unique_id, "
                    "source_title, season, episode_start, episode_end, "
                    "plan_status, status, is_ignored_by_user, source_url, "
                    "publication_date, voiceover_tag, slicing_status, "
                    "is_available, resolution) VALUES "
                    "(?, ?, ?, ?, ?, ?, 'candidate', 'pending', 0, ?, ?, ?, "
                    "'none', 1, ?)",
                    (series_id, item["unique_id"], item.get("source_title"),
                     item.get("season"), item["episode_start"],
                     item.get("episode_end"), item["source_url"],
                     item["publication_date"], item.get("voiceover_tag"),
                     item.get("resolution")))
                added += 1
        return {"added": added, "updated": updated, "deleted": deleted,
                "kept_phantoms": kept_phantoms}

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

    async def candidates(self, series_id: int) -> list[dict]:
        """Вход планировщика — только plan_status='candidate' (семантика
        SmartCollector: план строится из кандидатов текущего скана;
        ignored участвуют — фильтр по ignore происходит позже)."""
        return await self._db.fetch_all(
            "SELECT * FROM media_items WHERE series_id=? "
            "AND plan_status='candidate'", (series_id,))

    async def set_ignored(self, unique_id: str, ignored: bool) -> None:
        await self._db.execute(
            "UPDATE media_items SET is_ignored_by_user=? WHERE unique_id=?",
            (1 if ignored else 0, unique_id))

    async def get_item(self, unique_id: str) -> dict | None:
        return await self._db.fetch_one(
            "SELECT * FROM media_items WHERE unique_id=?", (unique_id,))

    async def reset_plan_status(self, series_id: int) -> None:
        """Пересборка плана композиции (Р-21): все элементы — снова
        кандидаты (семантика старого reset_plan_status_for_series)."""
        await self._db.execute(
            "UPDATE media_items SET plan_status='candidate' "
            "WHERE series_id=?", (series_id,))

    async def set_plan_statuses(self, plan: dict[str, str]) -> None:
        for unique_id, plan_status in plan.items():
            await self._db.execute(
                "UPDATE media_items SET plan_status=? WHERE unique_id=?",
                (plan_status, unique_id))
