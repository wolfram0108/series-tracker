"""Repository for torrents: tables torrents, torrent_files, agent_tasks
and torrent_progress (per-release progress for the UI).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.db import Database

from calc.torrents import selection


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


# "Downloaded" skips specials (season 0): TMDB does not match them and "total" excludes them,
# so counting them would exceed "total". Files with no extracted season (NULL) still count (single episodes).
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

    # --- torrents (release registry) ----------------------------------------------------

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
        """Idempotent recording of a release: the link in the DB is what torrent_id
        was computed from (for magnet trackers it is the magnet: a data constraint, Р-12)."""
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
        """POINT 3: the same infohash re-published under a new label: update the
        label/date/quality and keep the row active. No download and no duplicates; it only
        lets the date detector (POINT 1) converge and keeps the tracker from being polled on every scan."""
        link = torrent.get("link") or torrent.get("magnet")
        await self._db.execute(
            "UPDATE torrents SET is_active=1, qb_hash=?, torrent_id=?, "
            "link=?, date_time=?, quality=?, episodes=? WHERE id=?",
            (qb_hash, torrent["torrent_id"], link, torrent.get("date_time"),
             torrent.get("quality"), torrent.get("episodes"), db_id))

    async def deactivate_and_clear_files(self, db_id: int) -> None:
        """Replacing a release (POINT 4): the row is marked inactive and REMAINS as the
        release history record; its file records are removed (files on disk are replaced by the new release).
        The key is the row id (the exact row), as in the old system (deactivate_torrent_and_clear_files),
        NOT the tracker label: otherwise the wrong row could be deactivated."""
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
        """Reconcile with qBit (crash tolerance): active torrents rows whose qb_hash is
        NOT in the live qBit (torrent removed manually or vanished) are reset: files are deleted, is_active=0.
        The series goes back to the "no torrent" state; the next scan adds the release again.
        Returns the series_id values of the affected series."""
        affected: list[int] = []
        for row in await self.all_active():
            qh = (row.get("qb_hash") or "").lower()
            if qh and qh not in live_hashes:
                await self.deactivate_and_clear_files(row["id"])
                affected.append(row["series_id"])
        return affected

    async def torrent_by_hash(self, qb_hash: str) -> dict | None:
        """The row owning the infohash. With historical duplicates (the same release under an old label)
        the ACTIVE row is returned: file binding and progress go only to the live row (invariant И2)."""
        return await self._db.fetch_one(
            "SELECT * FROM torrents WHERE qb_hash=? "
            "ORDER BY is_active DESC, id LIMIT 1", (qb_hash,))

    async def row_by_hash(self, series_id: int, qb_hash: str) -> dict | None:
        """Whether the series knows this infohash (active or historical). The release identity key (И1):
        it decides "this is the same release"."""
        return await self._db.fetch_one(
            "SELECT * FROM torrents WHERE series_id=? AND qb_hash=? "
            "ORDER BY is_active DESC, id LIMIT 1", (series_id, qb_hash))

    async def history(self, series_id: int) -> list[dict]:
        """Full release history of the series (contract of the old get_torrents)."""
        rows = await self._db.fetch_all(
            "SELECT * FROM torrents WHERE series_id=? ORDER BY id",
            (series_id,))
        return self._db.coerce_bools(rows, ("is_active",))

    async def add_registered_rows(self, series_id: int,
                                  torrents: list[dict]) -> int:
        """Registers releases from the series add form (without qBit: the semantics of the old
        add_series; the scan picks them up later)."""
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
        """Renamed files per series: the card counter (Р-19). ACTIVE releases only (И4):
        replaced rows are history and are not counted; otherwise a stuck duplicate gives "16 instead of 8"."""
        rows = await self._db.fetch_all(
            "SELECT t.series_id AS series_id, COUNT(*) AS n "
            "FROM torrent_files tf JOIN torrents t ON t.id=tf.torrent_db_id "
            f"WHERE tf.status='renamed' AND t.is_active=1 AND {_NOT_SPECIALS} "
            f"AND {_ONLY_VIDEOS} AND tf.is_excluded=0 GROUP BY t.series_id")
        return {r["series_id"]: r["n"] for r in rows}

    async def downloaded_count_for_series(self, series_id: int) -> int:
        """The "downloaded" counter of one series (renamed files of active releases),
        for the reactive card update (Д1)."""
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM torrent_files tf "
            "JOIN torrents t ON t.id=tf.torrent_db_id "
            "WHERE tf.status='renamed' AND t.is_active=1 AND t.series_id=? "
            f"AND {_NOT_SPECIALS} AND {_ONLY_VIDEOS} AND tf.is_excluded=0",
            (series_id,))
        return row["n"] if row else 0

    async def seasons_for_series(self, series_id: int) -> list[int]:
        """Real seasons of the series: distinct season from extracted_metadata of files
        of ACTIVE releases (for the TMDB aggregate over several seasons)."""
        rows = await self._db.fetch_all(
            "SELECT DISTINCT json_extract(tf.extracted_metadata, '$.season') "
            "AS season FROM torrent_files tf "
            "JOIN torrents t ON t.id=tf.torrent_db_id "
            "WHERE t.is_active=1 AND t.series_id=? AND tf.is_excluded=0 "
            "AND season IS NOT NULL",
            (series_id,))
        return [r["season"] for r in rows if r["season"] is not None]

    async def delete_for_series(self, series_id: int) -> None:
        """Cascade Р-19: the series is deleted, so our tables are cleaned up."""
        await self._db.execute(
            "DELETE FROM torrent_files WHERE torrent_db_id IN "
            "(SELECT id FROM torrents WHERE series_id=?)", (series_id,))
        await self._db.execute(
            "DELETE FROM agent_tasks WHERE series_id=?", (series_id,))
        await self._db.execute(
            "DELETE FROM torrents WHERE series_id=?", (series_id,))
        await self._db.execute(
            "DELETE FROM torrent_progress WHERE series_id=?", (series_id,))

    # --- agent_tasks (pipeline tasks) -----------------------------------------------

    async def all_tasks(self) -> list[dict]:
        return await self._db.fetch_all("SELECT * FROM agent_tasks")

    async def task_by_hash(self, torrent_hash: str) -> dict | None:
        """Pipeline task by infohash: tells "already healthy" (no task) apart from
        "error, restart needed" (error stage)."""
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
        """Files with qb_hash and progress (contract of the old get_torrent_files_for_series).
        ACTIVE releases only (И4): replaced ones are history without files."""
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
        """Files of a release by infohash: the input for applying priorities."""
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
        """Releases where no file is needed: qBittorrent reports progress=1.0 for them (nothing to download),
        and without this list the card would show "ready" with zero files on disk."""
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
        """Series memory -> is_excluded column of files of active releases.

        A projection, not a second source of truth: aggregates and priorities read one column;
        the memory stays in series and survives release replacement."""
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
        """Progress of all releases (contract of the old get_all_active_torrent_tasks:
        the monitoring table in the UI)."""
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
