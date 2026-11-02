"""Репозиторий downloads: таблица download_tasks (VK-часть) и колонки
media_items.status / final_filename (колоночное владение по ТЗ;
plan_status и строки — у scan, slicing_* — у slicing: только чтение).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from core.db import Database

_IN_PLAN = "('in_plan_single', 'in_plan_compilation')"

# A task holds its place in every one of these states; in the first two a worker is at it, in the last one the file is
# taking its name. Every query that means "a task that is alive" says it with these, not with a list of its own.
RUNNING_STATES = ("downloading", "processing")
LIVE_STATES = ("pending", *RUNNING_STATES, "publishing")


def _marks(states: Iterable[str]) -> str:
    return "(" + ", ".join(f"'{each}'" for each in states) + ")"


_LIVE, _RUNNING = _marks(LIVE_STATES), _marks(RUNNING_STATES)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


class DownloadsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- задачи -------------------------------------------------------------------

    async def active_tasks(self) -> list[dict]:
        """Живые задачи (pending, downloading, processing, publishing) — активная очередь UI и снимок очереди для scan."""
        return await self._db.fetch_all(
            "SELECT * FROM download_tasks WHERE task_type='vk_video' AND "
            f"status IN {_LIVE} ORDER BY created_at")

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
            f"task_type='vk_video' AND status IN {_LIVE}",
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

    async def waiting_outside_plan(self, series_id: int) -> list[dict]:
        """Pending- и error-задачи без строки плана (находка 32): строка либо вышла из плана, либо удалена scan;
        ошибка элемента вне плана никем не повторяется и только мусорит."""
        return await self._db.fetch_all(
            "SELECT id, task_key, save_path, status FROM download_tasks WHERE series_id=? AND "
            "task_type='vk_video' AND status IN ('pending', 'error') AND NOT EXISTS "
            "(SELECT 1 FROM media_items mi WHERE mi.unique_id = download_tasks.task_key "
            f"AND mi.plan_status IN {_IN_PLAN})", (series_id,))

    async def unwanted_files(self, series_id: int) -> list[dict]:
        """Whole files of single episodes that the configuration took out of the plan: the user switched the episode off,
        or the source (or its profile) the row came from is gone. A compilation and a sliced file are the business of the
        slicing and are not named here."""
        return await self._db.fetch_all(
            "SELECT unique_id, final_filename FROM media_items WHERE series_id=? AND final_filename IS NOT NULL "
            "AND slicing_status='none' AND (episode_end IS NULL OR episode_end <= episode_start) "
            "AND (is_ignored_by_user=1 OR plan_status='removed')", (series_id,))

    async def wanted_holder(self, series_id: int, filename: str, *, except_uid: str) -> bool:
        """Whether a row the plan wants holds a file of this name: then the file is not an unwanted one."""
        row = await self._db.fetch_one(
            "SELECT 1 FROM media_items WHERE series_id=? AND final_filename=? AND unique_id != ? "
            f"AND is_ignored_by_user=0 AND plan_status IN {_IN_PLAN}", (series_id, filename, except_uid))
        return row is not None

    async def active_tasks_outside_plan(self, series_id: int) -> list[dict]:
        """Downloads under way whose row is no longer in the plan: the plan does not want that video any more."""
        return await self._db.fetch_all(
            "SELECT dt.id, dt.task_key, dt.save_path, dt.status FROM download_tasks dt "
            "JOIN media_items mi ON mi.unique_id = dt.task_key "
            f"WHERE dt.series_id=? AND dt.task_type='vk_video' AND dt.status IN {_RUNNING} "
            f"AND mi.plan_status NOT IN {_IN_PLAN}", (series_id,))

    async def tasks_without_row(self, series_id: int) -> list[dict]:
        """Tasks of any state whose media_items row is gone (scan deletes the rows of a slot that lost)."""
        return await self._db.fetch_all(
            "SELECT id, task_key, save_path, status FROM download_tasks WHERE series_id=? AND task_type='vk_video' "
            "AND NOT EXISTS (SELECT 1 FROM media_items mi WHERE mi.unique_id = download_tasks.task_key)",
            (series_id,))

    async def claim(self, task_id: int) -> dict | None:
        """The task begins. It takes the address its row has now, and the row is marked as being fetched in the same step:
        the scan, which leaves the address of such a row alone, either wrote its address before this step or does not
        write it. None when the task is not waiting any more."""
        def work(conn) -> dict | None:
            began = conn.execute(
                "UPDATE download_tasks SET status='downloading', attempts=attempts+1, updated_at=?, video_url=COALESCE("
                "(SELECT COALESCE(NULLIF(mi.download_url, ''), mi.source_url) FROM media_items mi "
                "WHERE mi.unique_id = download_tasks.task_key), video_url) WHERE id=? AND status='pending'",
                (_now(), task_id)).rowcount
            if not began:
                return None
            conn.execute("UPDATE media_items SET status='downloading' WHERE unique_id="
                         "(SELECT task_key FROM download_tasks WHERE id=?)", (task_id,))
            return dict(conn.execute("SELECT * FROM download_tasks WHERE id=?", (task_id,)).fetchone())
        return await self._db.transaction(work)

    async def begin_publish(self, task_id: int) -> None:
        """The file is whole and is about to take its name: the task says so before anything is changed, so that a stop at
        any point leaves a record that tells what was to be done."""
        await self._db.execute("UPDATE download_tasks SET status='publishing', progress=100, dlspeed=0, eta=0, updated_at=? "
                               "WHERE id=?", (_now(), task_id))

    async def finish_publish(self, task_id: int, unique_id: str, filename: str) -> None:
        """The file has its name: the row holds it, every other row of the series that held that name lets it go (the
        file is another video now), and the task is done, in one step."""
        await self._db.execute_many([
            ("UPDATE media_items SET final_filename=NULL WHERE final_filename=? AND unique_id != ? AND series_id="
             "(SELECT series_id FROM media_items WHERE unique_id=?)", (filename, unique_id, unique_id)),
            ("UPDATE media_items SET status='completed', final_filename=? WHERE unique_id=?", (filename, unique_id)),
            ("DELETE FROM download_tasks WHERE id=?", (task_id,))])

    async def publishing_tasks(self, series_id: int | None = None) -> list[dict]:
        """Tasks whose file was taking its name when the process stopped or the record could not be written."""
        where, args = ("AND series_id=? ", (series_id,)) if series_id is not None else ("", ())
        return await self._db.fetch_all(
            f"SELECT * FROM download_tasks WHERE task_type='vk_video' AND status='publishing' {where}ORDER BY id", args)

    async def failed_task_of(self, unique_id: str) -> dict | None:
        return await self._db.fetch_one(
            "SELECT id, task_key, save_path, status FROM download_tasks WHERE task_key=? AND task_type='vk_video' "
            "AND status='error'", (unique_id,))

    async def remove_task(self, task_id: int, unique_id: str) -> None:
        """The task goes, and a row that was being fetched waits for a task again, in one step: nobody sees a row that is
        fetched by no task."""
        await self._db.execute_many([
            ("DELETE FROM download_tasks WHERE id=?", (task_id,)),
            ("UPDATE media_items SET status='pending' WHERE unique_id=? AND status='downloading'", (unique_id,))])

    async def work_owners(self, series_id: int) -> tuple[set[str], set[str]]:
        """(keys of the rows of the series, keys of its tasks): a work file of a row that has no task has no owner."""
        rows = await self._db.fetch_all("SELECT unique_id FROM media_items WHERE series_id=?", (series_id,))
        tasks = await self._db.fetch_all("SELECT task_key FROM download_tasks WHERE series_id=? AND task_type='vk_video'",
                                         (series_id,))
        return {each["unique_id"] for each in rows}, {each["task_key"] for each in tasks}

    async def mark_error(self, task_id: int, message: str) -> None:
        await self._db.execute(
            "UPDATE download_tasks SET status='error', error_message=?, "
            "updated_at=? WHERE id=?", (message, _now(), task_id))

    async def requeue_task(self, task_id: int) -> None:
        """Transient failure: back to pending, attempts keep growing."""
        await self._db.execute(
            "UPDATE download_tasks SET status='pending', progress=0, "
            "dlspeed=0, eta=0, error_message=NULL, updated_at=? WHERE id=?",
            (_now(), task_id))

    async def update_progress(self, task_id: int, data: dict) -> None:
        # фаза remux отражается статусом 'processing' (постобработка) —
        # прогресс и ETA там из метрик ffmpeg (ТЗ: постобработка обязана
        # показывать прогресс и оставшееся время)
        status = "processing" if data.get("phase") == "remux" \
            else "downloading"
        await self._db.execute(
            "UPDATE download_tasks SET status=?, progress=?, dlspeed=?, "
            "eta=?, total_size_mb=COALESCE(?, total_size_mb), updated_at=? "
            "WHERE id=?",
            (status, data.get("progress", 0), data.get("dlspeed", 0),
             data.get("eta", 0), data.get("total_size_mb"), _now(), task_id))

    async def delete_task(self, task_id: int) -> None:
        await self._db.execute(
            "DELETE FROM download_tasks WHERE id=?", (task_id,))

    async def get_task(self, task_id: int) -> dict | None:
        return await self._db.fetch_one(
            "SELECT * FROM download_tasks WHERE id=? AND task_type='vk_video'",
            (task_id,))

    async def tasks_for_series(self, series_id: int) -> list[dict]:
        """Задачи загрузки серии (id, task_key, save_path, status) — для отмены
        in-flight при удалении серии (Д2)."""
        return await self._db.fetch_all(
            "SELECT id, task_key, save_path, status FROM download_tasks WHERE "
            "series_id=? AND task_type='vk_video'", (series_id,))

    async def clearable_tasks(self) -> list[dict]:
        """Задачи, которые очистка очереди снимает (контракт /downloads/queue/clear): ждущие и упавшие; идущая загрузка
        и файл, который получает имя, остаются."""
        return await self._db.fetch_all(
            "SELECT id, task_key, save_path, status FROM download_tasks WHERE task_type='vk_video' AND "
            "status IN ('pending', 'error')")

    async def requeue_interrupted(self) -> int:
        """Reconcile при старте: оборванные загрузки — снова в очередь.
        error НЕ трогаем (Р-13: ошибка не лечится рестартом)."""
        return await self._db.execute(
            "UPDATE download_tasks SET status='pending' WHERE "
            f"task_type='vk_video' AND status IN {_RUNNING}")

    # --- media_items: наши колонки (status, final_filename) ------------------------

    async def series_of_item(self, unique_id: str) -> int | None:
        row = await self._db.fetch_one(
            "SELECT series_id FROM media_items WHERE unique_id=?", (unique_id,))
        return row["series_id"] if row else None

    async def source_of_item(self, unique_id: str) -> int | None:
        """The source of the series a candidate was found by (scan's column, read only); NULL on earlier rows."""
        row = await self._db.fetch_one(
            "SELECT source_id FROM media_items WHERE unique_id=?", (unique_id,))
        return row["source_id"] if row else None

    async def set_item_filename(self, unique_id: str,
                                filename: str) -> None:
        await self._db.execute(
            "UPDATE media_items SET final_filename=? WHERE unique_id=?",
            (filename, unique_id))

    async def set_item_status(self, unique_id: str, status: str) -> None:
        await self._db.execute(
            "UPDATE media_items SET status=? WHERE unique_id=?",
            (status, unique_id))

    async def register_downloaded(self, unique_id: str, filename: str, released: Iterable[str] = ()) -> None:
        """The row holds the file; `released` are the rows it displaced, which no longer do, in the same step."""
        await self._db.execute_many([
            ("UPDATE media_items SET final_filename=NULL WHERE unique_id=?", (each,)) for each in released] + [
            ("UPDATE media_items SET status='completed', final_filename=? WHERE unique_id=?", (filename, unique_id))])

    async def task_paths(self, series_id: int) -> dict[str, str]:
        """{final path: key of the row} of the tasks that are to write a file of the series."""
        rows = await self._db.fetch_all(
            "SELECT save_path, task_key FROM download_tasks WHERE series_id=? AND task_type='vk_video' "
            f"AND status IN {_LIVE}", (series_id,))
        return {each["save_path"]: each["task_key"] for each in rows}

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

    async def reference_items(self, series_id: int) -> list[dict]:
        """Single-episode rows of the plan in any download state: the reference episodes."""
        return await self._db.fetch_all(
            "SELECT unique_id, season, episode_start, part, source_url, status, final_filename, superseded_by "
            "FROM media_items WHERE series_id=? AND plan_status='in_plan_single' "
            "AND is_ignored_by_user=0 ORDER BY season, episode_start", (series_id,))

    async def completed_items(self, series_id: int) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM media_items WHERE series_id=? AND "
            "status='completed'", (series_id,))

    async def superseded_files(self, series_id: int) -> list[dict]:
        """Downloaded rows beaten by the plan's winner of their slot, with the
        winner's file. Only a winner that is itself downloaded qualifies."""
        return await self._db.fetch_all(
            "SELECT loser.unique_id AS unique_id, "
            "loser.final_filename AS filename, "
            "winner.final_filename AS winner_filename "
            "FROM media_items loser JOIN media_items winner "
            "ON winner.unique_id = loser.superseded_by "
            "WHERE loser.series_id=? AND loser.final_filename IS NOT NULL "
            "AND loser.slicing_status='none' "
            f"AND winner.plan_status IN {_IN_PLAN} "
            "AND winner.status='completed' AND winner.is_ignored_by_user=0 "
            "AND winner.final_filename IS NOT NULL", (series_id,))

    async def holders_of_file(self, series_id: int, filename: str, *, except_uid: str,
                              displaced_by: str | None = None) -> list[dict]:
        """The other rows of the series that hold a file of this name; with `displaced_by`, only those the plan displaced
        by that row. A file one row holds is not an orphan that another row may take."""
        query = ("SELECT unique_id, final_filename FROM media_items WHERE series_id=? AND final_filename=? "
                 "AND unique_id != ?")
        args: list = [series_id, filename, except_uid]
        if displaced_by is not None:
            query += " AND superseded_by=?"
            args.append(displaced_by)
        return await self._db.fetch_all(query, tuple(args))

    async def clear_item_filename(self, unique_id: str) -> None:
        """The file is gone but the row stays: it remembers the resolution that
        was already fetched, so the next scan does not download it again."""
        await self._db.execute(
            "UPDATE media_items SET final_filename=NULL WHERE unique_id=?",
            (unique_id,))

    async def downloaded_count(self, series_id: int) -> int:
        """Счётчик «скачано» VK-серии — как scan.media.downloaded_counts
        (есть final_filename), для реактивного SSE (Д1). Спешелы (сезон 0)
        не считаем — total их не учитывает (соответствие TMDB)."""
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM media_items WHERE series_id=? AND "
            "final_filename IS NOT NULL AND (season IS NULL OR season != 0)",
            (series_id,))
        return row["n"] if row else 0

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

    async def delete_for_series(self, series_id: int) -> None:
        """Каскад Р-19: серия удалена — наши vk-задачи чистятся."""
        await self._db.execute(
            "DELETE FROM download_tasks WHERE series_id=? AND "
            "task_type='vk_video'", (series_id,))
