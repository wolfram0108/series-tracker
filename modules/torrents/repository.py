"""Репозиторий torrents: таблицы torrents, torrent_files, agent_tasks
и торрент-часть download_tasks (прогресс для UI).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


class TorrentsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- torrents (реестр раздач) ----------------------------------------------------

    async def active_torrents(self, series_id: int) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM torrents WHERE series_id=? AND is_active=1",
            (series_id,))

    async def all_active(self) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT id, series_id, qb_hash FROM torrents WHERE is_active=1 "
            "AND qb_hash IS NOT NULL")

    async def upsert_registered(self, series_id: int, torrent: dict,
                                qb_hash: str) -> None:
        """Идемпотентная фиксация раздачи: link в БД — то, от чего
        считался torrent_id (для magnet-трекеров это magnet —
        констрейнт данных, Р-12)."""
        link = torrent.get("link") or torrent.get("magnet")
        existing = await self._db.fetch_one(
            "SELECT id FROM torrents WHERE torrent_id=?",
            (torrent["torrent_id"],))
        if existing:
            await self._db.execute(
                "UPDATE torrents SET is_active=1, qb_hash=? WHERE id=?",
                (qb_hash, existing["id"]))
        else:
            await self._db.execute(
                "INSERT INTO torrents (series_id, torrent_id, link, "
                "date_time, quality, episodes, is_active, qb_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (series_id, torrent["torrent_id"], link,
                 torrent.get("date_time"), torrent.get("quality"),
                 torrent.get("episodes"), qb_hash))

    async def deactivate_and_clear_files(self, torrent_id: str) -> None:
        """Замена раздачи: пометить неактивной, записи о файлах удалить
        (семантика старого deactivate_torrent_and_clear_files)."""
        row = await self._db.fetch_one(
            "SELECT id FROM torrents WHERE torrent_id=?", (torrent_id,))
        if not row:
            return
        await self._db.execute(
            "DELETE FROM torrent_files WHERE torrent_db_id=?", (row["id"],))
        await self._db.execute(
            "UPDATE torrents SET is_active=0 WHERE id=?", (row["id"],))

    async def deactivate_all(self, series_id: int) -> list[str]:
        rows = await self.active_torrents(series_id)
        for r in rows:
            await self.deactivate_and_clear_files(r["torrent_id"])
        return [r["qb_hash"] for r in rows if r.get("qb_hash")]

    async def torrent_by_hash(self, qb_hash: str) -> dict | None:
        return await self._db.fetch_one(
            "SELECT * FROM torrents WHERE qb_hash=?", (qb_hash,))

    async def history(self, series_id: int) -> list[dict]:
        """Вся история раздач серии (контракт старого get_torrents)."""
        rows = await self._db.fetch_all(
            "SELECT * FROM torrents WHERE series_id=? ORDER BY id",
            (series_id,))
        return self._db.coerce_bools(rows, ("is_active",))

    async def add_registered_rows(self, series_id: int,
                                  torrents: list[dict]) -> int:
        """Регистрация раздач формы добавления серии (без qBit —
        семантика старого add_series: скан подхватит позже)."""
        added = 0
        for t in torrents:
            if not t.get("link"):
                continue
            await self._db.execute(
                "INSERT INTO torrents (series_id, torrent_id, link, "
                "date_time, quality, episodes, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, 1)",
                (series_id, t.get("torrent_id"), t["link"],
                 t.get("date_time"), t.get("quality"), t.get("episodes")))
            added += 1
        return added

    async def downloaded_counts(self) -> dict[int, int]:
        """Переименованные файлы по сериям — счётчик карточек (Р-19)."""
        rows = await self._db.fetch_all(
            "SELECT t.series_id AS series_id, COUNT(*) AS n "
            "FROM torrent_files tf JOIN torrents t ON t.id=tf.torrent_db_id "
            "WHERE tf.status='renamed' GROUP BY t.series_id")
        return {r["series_id"]: r["n"] for r in rows}

    async def delete_for_series(self, series_id: int) -> None:
        """Каскад Р-19: серия удалена — наши таблицы чистятся."""
        await self._db.execute(
            "DELETE FROM torrent_files WHERE torrent_db_id IN "
            "(SELECT id FROM torrents WHERE series_id=?)", (series_id,))
        await self._db.execute(
            "DELETE FROM agent_tasks WHERE series_id=?", (series_id,))
        await self._db.execute(
            "DELETE FROM torrents WHERE series_id=?", (series_id,))
        await self._db.execute(
            "DELETE FROM download_tasks WHERE series_id=? AND "
            "task_type='torrent'", (series_id,))

    # --- agent_tasks (задачи конвейера) -----------------------------------------------

    async def all_tasks(self) -> list[dict]:
        return await self._db.fetch_all("SELECT * FROM agent_tasks")

    async def tasks_for_series(self, series_id: int) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM agent_tasks WHERE series_id=?", (series_id,))

    async def upsert_task(self, task: dict) -> None:
        await self._db.execute(
            "INSERT INTO agent_tasks (torrent_hash, series_id, torrent_id, "
            "old_torrent_id, stage) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(torrent_hash) DO UPDATE SET stage=excluded.stage",
            (task["torrent_hash"], task["series_id"], task["torrent_id"],
             task.get("old_torrent_id") or "None", task["stage"]))

    async def set_stage(self, torrent_hash: str, stage: str) -> None:
        await self._db.execute(
            "UPDATE agent_tasks SET stage=? WHERE torrent_hash=?",
            (stage, torrent_hash))

    async def delete_task(self, torrent_hash: str) -> None:
        await self._db.execute(
            "DELETE FROM agent_tasks WHERE torrent_hash=?", (torrent_hash,))

    # --- torrent_files ----------------------------------------------------------------

    async def files_for_series(self, series_id: int) -> list[dict]:
        """Файлы с qb_hash и прогрессом (контракт старого
        get_torrent_files_for_series)."""
        return await self._db.fetch_all(
            "SELECT tf.*, t.qb_hash, COALESCE(dt.progress, 0) AS progress "
            "FROM torrent_files tf "
            "JOIN torrents t ON t.id = tf.torrent_db_id "
            "LEFT JOIN download_tasks dt ON dt.task_key = t.qb_hash "
            "AND dt.task_type='torrent' WHERE t.series_id=?", (series_id,))

    async def files_for_torrent(self, torrent_db_id: int) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM torrent_files WHERE torrent_db_id=?",
            (torrent_db_id,))

    async def set_file_status(self, file_id: int, status: str) -> None:
        await self._db.execute(
            "UPDATE torrent_files SET status=? WHERE id=?", (status, file_id))

    async def set_files_status_by_hash(self, qb_hash: str, from_status: str,
                                       to_status: str) -> None:
        await self._db.execute(
            "UPDATE torrent_files SET status=? WHERE status=? AND "
            "torrent_db_id IN (SELECT id FROM torrents WHERE qb_hash=?)",
            (to_status, from_status, qb_hash))

    async def upsert_files(self, torrent_db_id: int,
                           files: list[dict]) -> None:
        for f in files:
            existing = await self._db.fetch_one(
                "SELECT id FROM torrent_files WHERE torrent_db_id=? AND "
                "original_path=?", (torrent_db_id, f["original_path"]))
            meta = f.get("extracted_metadata")
            if isinstance(meta, dict):
                meta = json.dumps(meta, ensure_ascii=False)
            if existing:
                await self._db.execute(
                    "UPDATE torrent_files SET renamed_path=?, status=?, "
                    "extracted_metadata=? WHERE id=?",
                    (f.get("renamed_path"), f["status"], meta,
                     existing["id"]))
            else:
                await self._db.execute(
                    "INSERT INTO torrent_files (torrent_db_id, "
                    "original_path, renamed_path, status, "
                    "extracted_metadata) VALUES (?, ?, ?, ?, ?)",
                    (torrent_db_id, f["original_path"],
                     f.get("renamed_path"), f["status"], meta))

    # --- download_tasks: прогресс торрентов для UI -------------------------------------

    async def upsert_progress(self, series_id: int, qb_hash: str,
                              info: dict, error_message: str | None = None
                              ) -> None:
        progress = int(round(info.get("progress", 0) * 100))
        existing = await self._db.fetch_one(
            "SELECT id FROM download_tasks WHERE task_key=? AND "
            "task_type='torrent'", (qb_hash,))
        if existing:
            await self._db.execute(
                "UPDATE download_tasks SET status=?, progress=?, dlspeed=?, "
                "eta=?, error_message=?, updated_at=? WHERE id=?",
                (info.get("state"), progress, info.get("dlspeed", 0),
                 info.get("eta", 0), error_message, _now(), existing["id"]))
        else:
            await self._db.execute(
                "INSERT INTO download_tasks (task_key, series_id, "
                "task_type, status, progress, dlspeed, eta, error_message, "
                "attempts, created_at, updated_at) VALUES (?, ?, 'torrent', "
                "?, ?, ?, ?, ?, 0, ?, ?)",
                (qb_hash, series_id, info.get("state"), progress,
                 info.get("dlspeed", 0), info.get("eta", 0), error_message,
                 _now(), _now()))

    async def remove_stale_progress(self, series_id: int,
                                    live_hashes: list[str]) -> None:
        placeholders = ",".join("?" for _ in live_hashes) or "''"
        await self._db.execute(
            f"DELETE FROM download_tasks WHERE series_id=? AND "
            f"task_type='torrent' AND task_key NOT IN ({placeholders})",
            (series_id, *live_hashes))

    async def torrent_progress(self, series_id: int) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM download_tasks WHERE series_id=? AND "
            "task_type='torrent'", (series_id,))

    async def all_torrent_progress(self) -> list[dict]:
        """Прогресс всех торрент-загрузок (контракт старого
        get_all_active_torrent_tasks — таблица мониторинга в UI)."""
        return await self._db.fetch_all(
            "SELECT * FROM download_tasks WHERE task_type='torrent' "
            "ORDER BY id")
