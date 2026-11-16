"""Репозиторий torrents: таблицы torrents, torrent_files, agent_tasks
и torrent_progress (прогресс раздач для UI).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.db import Database

from . import selection


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


# Счётчик «скачано» не считает спешелы (сезон 0): они не соответствуют TMDB,
# total их не учитывает — иначе «скачано» вышло бы больше «всего». Файлы без
# извлечённого сезона (NULL) считаются (одиночные серии без явного сезона).
_NOT_SPECIALS = ("(json_extract(tf.extracted_metadata, '$.season') IS NULL "
                 "OR json_extract(tf.extracted_metadata, '$.season') != 0)")

_ONLY_VIDEOS = ("(json_extract(tf.extracted_metadata, '$.media_type') = 'video' "
                "OR json_extract(tf.extracted_metadata, '$.media_type') IS NULL) "
                "AND (json_extract(tf.extracted_metadata, '$.is_extra') IS NULL "
                "OR json_extract(tf.extracted_metadata, '$.is_extra') = 0)")

_FILE_BOOLS = ("is_excluded",)


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

    async def refresh_listing(self, db_id: int, torrent: dict,
                              qb_hash: str) -> None:
        """ПУНКТ 3: тот же infohash перевыложен под новым ярлыком — обновляем
        метку/дату/качество и держим строку активной. Без закачки и дублей;
        нужно лишь чтобы детектор по дате (ПУНКТ 1) сошёлся и трекер не
        дёргался каждый скан."""
        link = torrent.get("link") or torrent.get("magnet")
        await self._db.execute(
            "UPDATE torrents SET is_active=1, qb_hash=?, torrent_id=?, "
            "link=?, date_time=?, quality=?, episodes=? WHERE id=?",
            (qb_hash, torrent["torrent_id"], link, torrent.get("date_time"),
             torrent.get("quality"), torrent.get("episodes"), db_id))

    async def deactivate_and_clear_files(self, db_id: int) -> None:
        """Замена раздачи (ПУНКТ 4): строку пометить неактивной — она
        ОСТАЁТСЯ как запись истории релиза, — а записи о её файлах снять
        (файлы на диске замещены новой раздачей). Ключ — id строки (точная
        строка), как в старой системе (deactivate_torrent_and_clear_files),
        а НЕ ярлык с трекера: иначе можно деактивировать не ту строку."""
        await self._db.execute_many(self._deactivation(db_id))

    @staticmethod
    def _deactivation(db_id: int) -> list[tuple[str, tuple]]:
        # One transaction: a reader never sees the files gone while the row is still active.
        return [("DELETE FROM torrent_files WHERE torrent_db_id=?", (db_id,)),
                ("UPDATE torrents SET is_active=0 WHERE id=?", (db_id,))]

    async def drop_task_and_row(self, torrent_hash: str, db_id: int | None) -> None:
        """The task of a vanished torrent ends together with its row, so nobody sees the task gone and the row active."""
        statements = self._deactivation(db_id) if db_id is not None else []
        statements.append(("DELETE FROM agent_tasks WHERE torrent_hash=?", (torrent_hash,)))
        await self._db.execute_many(statements)

    async def deactivate_all(self, series_id: int) -> list[str]:
        rows = await self.active_torrents(series_id)
        for r in rows:
            await self.deactivate_and_clear_files(r["id"])
        return [r["qb_hash"] for r in rows if r.get("qb_hash")]

    async def deactivate_orphans(self, live_hashes: set[str]) -> list[int]:
        """Reconcile с qBit (crash-tolerance): активные torrents-записи,
        чьего qb_hash НЕТ в реальном qBit (торрент удалён вручную/пропал),
        сбрасываются — файлы удаляются, is_active=0. Серия возвращается в
        состояние «торрента нет», следующий скан добавит раздачу заново.
        Возвращает series_id затронутых серий."""
        affected: list[int] = []
        for row in await self.all_active():
            qh = (row.get("qb_hash") or "").lower()
            if qh and qh not in live_hashes:
                await self.deactivate_and_clear_files(row["id"])
                affected.append(row["series_id"])
        return affected

    async def torrent_by_hash(self, qb_hash: str) -> dict | None:
        """Строка-владелец infohash. При исторических двойниках (та же
        раздача под старым ярлыком) возвращается АКТИВНАЯ — привязка файлов
        и прогресс идут только на живую строку (инвариант И2)."""
        return await self._db.fetch_one(
            "SELECT * FROM torrents WHERE qb_hash=? "
            "ORDER BY is_active DESC, id LIMIT 1", (qb_hash,))

    async def row_by_hash(self, series_id: int, qb_hash: str) -> dict | None:
        """Знает ли серия этот infohash (активный или исторический). Ключ
        идентичности раздачи (И1): по нему решается «это та же раздача»."""
        return await self._db.fetch_one(
            "SELECT * FROM torrents WHERE series_id=? AND qb_hash=? "
            "ORDER BY is_active DESC, id LIMIT 1", (series_id, qb_hash))

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
        """Переименованные файлы по сериям — счётчик карточек (Р-19).
        Только АКТИВНЫЕ раздачи (И4): заменённые строки — история, в счёт
        не идут; иначе налипший дубль даёт «16 вместо 8»."""
        rows = await self._db.fetch_all(
            "SELECT t.series_id AS series_id, COUNT(*) AS n "
            "FROM torrent_files tf JOIN torrents t ON t.id=tf.torrent_db_id "
            f"WHERE tf.status='renamed' AND t.is_active=1 AND {_NOT_SPECIALS} "
            f"AND {_ONLY_VIDEOS} AND tf.is_excluded=0 GROUP BY t.series_id")
        return {r["series_id"]: r["n"] for r in rows}

    async def downloaded_count_for_series(self, series_id: int) -> int:
        """Счётчик «скачано» одной серии (переименованные файлы активных
        раздач) — для реактивного обновления карточки (Д1)."""
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM torrent_files tf "
            "JOIN torrents t ON t.id=tf.torrent_db_id "
            "WHERE tf.status='renamed' AND t.is_active=1 AND t.series_id=? "
            f"AND {_NOT_SPECIALS} AND {_ONLY_VIDEOS} AND tf.is_excluded=0",
            (series_id,))
        return row["n"] if row else 0

    async def seasons_for_series(self, series_id: int) -> list[int]:
        """Реальные сезоны серии — distinct season из extracted_metadata
        файлов АКТИВНЫХ раздач (для агрегата TMDB по нескольким сезонам)."""
        rows = await self._db.fetch_all(
            "SELECT DISTINCT json_extract(tf.extracted_metadata, '$.season') "
            "AS season FROM torrent_files tf "
            "JOIN torrents t ON t.id=tf.torrent_db_id "
            "WHERE t.is_active=1 AND t.series_id=? AND tf.is_excluded=0 "
            "AND season IS NOT NULL",
            (series_id,))
        return [r["season"] for r in rows if r["season"] is not None]

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
            "DELETE FROM torrent_progress WHERE series_id=?", (series_id,))

    # --- agent_tasks (задачи конвейера) -----------------------------------------------

    async def all_tasks(self) -> list[dict]:
        return await self._db.fetch_all("SELECT * FROM agent_tasks")

    async def task_by_hash(self, torrent_hash: str) -> dict | None:
        """Задача конвейера по infohash — для различения «уже здоров»
        (задачи нет) и «ошибка, нужен перезапуск» (стадия error)."""
        return await self._db.fetch_one(
            "SELECT * FROM agent_tasks WHERE torrent_hash=?", (torrent_hash,))

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
        get_torrent_files_for_series). Только АКТИВНЫЕ раздачи (И4):
        заменённые — история без файлов."""
        rows = await self._db.fetch_all(
            "SELECT tf.*, t.qb_hash, COALESCE(tp.progress, 0) AS progress "
            "FROM torrent_files tf "
            "JOIN torrents t ON t.id = tf.torrent_db_id "
            "LEFT JOIN torrent_progress tp ON tp.qb_hash = t.qb_hash "
            "WHERE t.series_id=? AND t.is_active=1", (series_id,))
        return self._db.coerce_bools(rows, _FILE_BOOLS)

    async def files_for_torrent(self, torrent_db_id: int) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM torrent_files WHERE torrent_db_id=?",
            (torrent_db_id,))
        return self._db.coerce_bools(rows, _FILE_BOOLS)

    async def files_by_hash(self, qb_hash: str) -> list[dict]:
        """Файлы раздачи по infohash — вход применения приоритетов."""
        rows = await self._db.fetch_all(
            "SELECT tf.* FROM torrent_files tf "
            "JOIN torrents t ON t.id=tf.torrent_db_id WHERE t.qb_hash=?",
            (qb_hash,))
        return self._db.coerce_bools(rows, _FILE_BOOLS)

    async def active_hashes(self, series_id: int) -> list[str]:
        rows = await self._db.fetch_all(
            "SELECT qb_hash FROM torrents WHERE series_id=? AND is_active=1 "
            "AND qb_hash IS NOT NULL", (series_id,))
        return [r["qb_hash"] for r in rows if r["qb_hash"]]

    async def fully_excluded_hashes(self, series_id: int) -> set[str]:
        """Раздачи, где не нужен ни один файл: qBittorrent отдаёт по ним
        progress=1.0 (нечего качать), и без этого списка карточка
        показала бы «готов» при нуле файлов на диске."""
        rows = await self._db.fetch_all(
            "SELECT t.qb_hash AS qb_hash, COUNT(*) AS total, "
            "SUM(tf.is_excluded) AS excluded FROM torrent_files tf "
            "JOIN torrents t ON t.id=tf.torrent_db_id "
            "WHERE t.series_id=? AND t.is_active=1 AND t.qb_hash IS NOT NULL "
            "GROUP BY t.qb_hash", (series_id,))
        return {r["qb_hash"] for r in rows
                if r["total"] and r["total"] == (r["excluded"] or 0)}

    async def project_selection(self, series_id: int,
                                ignored_seasons: set[int],
                                ignored_episodes: dict[int, set[int]]) -> int:
        """Память сериала -> колонка is_excluded файлов активных раздач.

        Проекция, а не второй источник правды: агрегаты и приоритеты
        читают одну колонку, память остаётся в series и переживает
        замену раздачи."""
        changed = 0
        for row in await self.files_for_series(series_id):
            meta = row.get("extracted_metadata")
            extracted = json.loads(meta) if meta else {}
            wanted, reason = selection.is_wanted(
                extracted, ignored_seasons, ignored_episodes)
            excluded = 0 if wanted else 1
            if (int(bool(row.get("is_excluded"))) == excluded
                    and (row.get("exclude_reason") or None) == reason):
                continue
            await self._db.execute(
                "UPDATE torrent_files SET is_excluded=?, exclude_reason=? "
                "WHERE id=?", (excluded, reason, row["id"]))
            changed += 1
        return changed

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
                    "extracted_metadata=?, qb_file_index=? WHERE id=?",
                    (f.get("renamed_path"), f["status"], meta,
                     f.get("qb_file_index"), existing["id"]))
            else:
                await self._db.execute(
                    "INSERT INTO torrent_files (torrent_db_id, "
                    "original_path, renamed_path, status, "
                    "extracted_metadata, qb_file_index) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (torrent_db_id, f["original_path"],
                     f.get("renamed_path"), f["status"], meta,
                     f.get("qb_file_index")))

    # --- torrent_progress: the progress of the releases for the interface ---------------

    async def upsert_progress(self, series_id: int, qb_hash: str,
                              info: dict, error_message: str | None = None
                              ) -> None:
        progress = int(round(info.get("progress", 0) * 100))
        await self._db.execute(
            "INSERT INTO torrent_progress (qb_hash, series_id, status, "
            "progress, dlspeed, eta, error_message, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(qb_hash) DO UPDATE SET series_id=excluded.series_id, "
            "status=excluded.status, progress=excluded.progress, "
            "dlspeed=excluded.dlspeed, eta=excluded.eta, "
            "error_message=excluded.error_message, "
            "updated_at=excluded.updated_at",
            (qb_hash, series_id, info.get("state"), progress,
             info.get("dlspeed", 0), info.get("eta", 0), error_message,
             _now(), _now()))

    async def remove_stale_progress(self, series_id: int,
                                    live_hashes: list[str]) -> None:
        placeholders = ",".join("?" for _ in live_hashes) or "''"
        await self._db.execute(
            f"DELETE FROM torrent_progress WHERE series_id=? AND "
            f"qb_hash NOT IN ({placeholders})", (series_id, *live_hashes))

    async def torrent_progress(self, series_id: int) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM torrent_progress WHERE series_id=?", (series_id,))

    async def all_torrent_progress(self) -> list[dict]:
        """Прогресс всех раздач (контракт старого get_all_active_torrent_tasks —
        таблица мониторинга в UI)."""
        return await self._db.fetch_all(
            "SELECT * FROM torrent_progress ORDER BY id")


class ReleaseStore:
    """The releases of the torrent sources (stage 15): tables torrent_releases, torrent_release_files and
    torrent_release_versions, keyed by the page and the id of the release on it."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, url: str, release: str) -> dict | None:
        return await self._db.fetch_one("SELECT * FROM torrent_releases WHERE url=? AND release=?", (url, release))

    async def of_page(self, url: str) -> list[dict]:
        return await self._db.fetch_all("SELECT * FROM torrent_releases WHERE url=? ORDER BY id", (url,))

    async def all(self) -> list[dict]:
        return await self._db.fetch_all("SELECT * FROM torrent_releases ORDER BY id")

    async def files(self, release_id: int) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT path, size, progress, priority FROM torrent_release_files WHERE release_id=? ORDER BY path",
            (release_id,))

    async def save(self, url: str, release: str, **fields) -> int:
        """Insert the release or change the fields given; returns its id."""
        fields["updated_at"] = _now()
        found = await self.get(url, release)
        if found is None:
            columns = ["url", "release", *fields]
            return await self._db.insert(
                f"INSERT INTO torrent_releases ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                (url, release, *fields.values()))
        await self._db.execute(f"UPDATE torrent_releases SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                               (*fields.values(), found["id"]))
        return found["id"]

    async def add_version(self, release_id: int, infohash: str, published: str | None) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO torrent_release_versions (release_id, infohash, published, added_at) VALUES (?, ?, ?, ?)",
            (release_id, infohash, published, _now()))

    async def versions(self, release_id: int) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT infohash, published, added_at FROM torrent_release_versions WHERE release_id=? ORDER BY added_at",
            (release_id,))

    async def set_files(self, release_id: int, files: list[dict]) -> None:
        """The files of the version in the client, as it lists them now: what it no longer lists goes."""
        statements = [("DELETE FROM torrent_release_files WHERE release_id=?", (release_id,))]
        statements += [("INSERT INTO torrent_release_files (release_id, path, size, progress, priority) VALUES (?, ?, ?, ?, ?)",
                        (release_id, f["path"], f["size"], min(1.0, max(0.0, f["progress"])), max(0, f["priority"])))
                       for f in files]
        await self._db.execute_many(statements)

    async def delete(self, release_id: int) -> None:
        await self._db.execute("DELETE FROM torrent_releases WHERE id=?", (release_id,))
