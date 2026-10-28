"""SQL of the voices module: the table voice_dubs (additional voice-overs of an episode)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


class VoicesRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _row(row: dict | None) -> dict | None:
        if row is not None:
            row["chain"] = json.loads(row["chain"])
        return row

    async def for_series(self, series_id: int) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM voice_dubs WHERE series_id=? ORDER BY season, episode, voice_key", (series_id,))
        return [self._row(row) for row in rows]

    async def for_episode(self, series_id: int, season: int, episode: int) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM voice_dubs WHERE series_id=? AND season=? AND episode=? ORDER BY voice_key",
            (series_id, season, episode))
        return [self._row(row) for row in rows]

    async def get(self, dub_id: int) -> dict[str, Any] | None:
        return self._row(await self._db.fetch_one("SELECT * FROM voice_dubs WHERE id=?", (dub_id,)))

    async def upsert(self, series_id: int, season: int, episode: int, site_episode: int,
                     voice_key: str, label: str, chain: list[dict[str, str]]) -> None:
        """Offer the voice-over again: an untouched row takes the fresh chain and starts from its
        main source; a row that is downloading or ready is left alone. An empty chain is a failure."""
        status = "pending" if chain else "failed"
        error = None if chain else "серии нет ни в одном источнике"
        await self._db.execute(
            "INSERT INTO voice_dubs (series_id, season, episode, site_episode, voice_key, "
            "voice_label, chain, status, error, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (series_id, season, episode, voice_key) DO UPDATE SET "
            "site_episode=excluded.site_episode, voice_label=excluded.voice_label, "
            "chain=excluded.chain, attempt=0, status=excluded.status, error=excluded.error, "
            "updated_at=excluded.updated_at WHERE voice_dubs.status IN ('pending', 'failed')",
            (series_id, season, episode, site_episode, voice_key, label,
             json.dumps(chain, ensure_ascii=False), status, error, _now()))

    async def pending(self, series_id: int | None = None) -> list[dict[str, Any]]:
        where, args = ("WHERE status='pending'", ())
        if series_id is not None:
            where, args = ("WHERE status='pending' AND series_id=?", (series_id,))
        rows = await self._db.fetch_all(f"SELECT * FROM voice_dubs {where} ORDER BY id", args)
        return [self._row(row) for row in rows]

    async def claim_download(self, dub_id: int, temp_path: str) -> bool:
        """Take a waiting voice-over for download; False when someone else has already taken it."""
        taken = await self._db.execute(
            "UPDATE voice_dubs SET status='downloading', temp_path=?, error=NULL, updated_at=? "
            "WHERE id=? AND status='pending'", (temp_path, _now(), dub_id))
        return bool(taken)

    async def active_episodes(self) -> set[tuple[int, int, int]]:
        """Episodes that hold temporary files or are on their way to them: (series, season, episode)."""
        rows = await self._db.fetch_all(
            "SELECT DISTINCT series_id, season, episode FROM voice_dubs WHERE "
            "status IN ('downloading', 'ready', 'aligning', 'aligned') OR (status='pending' AND attempt>0)")
        return {(row["series_id"], row["season"], row["episode"]) for row in rows}

    async def mark_ready(self, dub_id: int) -> None:
        await self._db.execute(
            "UPDATE voice_dubs SET status='ready', error=NULL, updated_at=? WHERE id=?", (_now(), dub_id))

    async def advance(self, dub_id: int, error: str) -> None:
        """The current source failed: the next one of the chain is tried."""
        await self._db.execute(
            "UPDATE voice_dubs SET attempt=attempt+1, status='pending', error=?, updated_at=? WHERE id=?",
            (error, _now(), dub_id))

    async def mark_failed(self, dub_id: int, error: str) -> None:
        await self._db.execute(
            "UPDATE voice_dubs SET status='failed', move_from=NULL, error=?, updated_at=? WHERE id=?",
            (error, _now(), dub_id))

    async def requeue_interrupted(self) -> int:
        return await self._db.execute(
            "UPDATE voice_dubs SET status='pending' WHERE status='downloading'")

    async def drop_unplanned(self, series_id: int,
                             planned: set[tuple[int, int, str]]) -> list[dict[str, Any]]:
        """Rows of voice-overs that left the plan and have no file; returns what was removed."""
        gone = [row for row in await self.for_series(series_id)
                if (row["season"], row["episode"], row["voice_key"]) not in planned
                and row["status"] in ("pending", "failed")]
        for row in gone:
            await self._db.execute("DELETE FROM voice_dubs WHERE id=?", (row["id"],))
        return gone

    async def mark_pending(self, dub_id: int, error: str) -> None:
        await self._db.execute(
            "UPDATE voice_dubs SET status='pending', attempt=0, error=?, updated_at=? WHERE id=?",
            (error, _now(), dub_id))

    async def delete_for_series(self, series_id: int) -> None:
        await self._db.execute("DELETE FROM voice_dubs WHERE series_id=?", (series_id,))

    async def series_waiting(self) -> list[int]:
        """Series with a voice-over that is on disk or aligned and still has to move on."""
        rows = await self._db.fetch_all(
            "SELECT DISTINCT series_id FROM voice_dubs WHERE status IN ('ready', 'aligned')")
        return [row["series_id"] for row in rows]

    async def by_job(self, job_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM voice_dubs WHERE job_id=? ORDER BY id", (job_id,))
        return [self._row(row) for row in rows]

    async def aligning_jobs(self) -> list[str]:
        rows = await self._db.fetch_all(
            "SELECT DISTINCT job_id FROM voice_dubs WHERE status='aligning' AND job_id IS NOT NULL")
        return [row["job_id"] for row in rows]

    async def mark_aligning(self, dub_ids: list[int], job_id: str, ref_uid: str, ref_path: str) -> None:
        """All voice-overs of a job at once: a reader sees the job with all of them or with none."""
        marks = ",".join("?" * len(dub_ids))
        await self._db.execute(
            "UPDATE voice_dubs SET status='aligning', job_id=?, ref_uid=?, ref_path=?, error=NULL, "
            f"outcome=NULL, updated_at=? WHERE id IN ({marks})", (job_id, ref_uid, ref_path, _now(), *dub_ids))

    async def mark_aligned(self, dub_id: int, aligned_path: str) -> None:
        await self._db.execute(
            "UPDATE voice_dubs SET status='aligned', aligned_path=?, updated_at=? WHERE id=?",
            (aligned_path, _now(), dub_id))

    async def begin_move(self, dub_id: int, destination: str, source: str) -> None:
        """Write down where a track is going before it moves; a restart finishes the move from this."""
        await self._db.execute(
            "UPDATE voice_dubs SET sidecar_path=?, move_from=?, updated_at=? WHERE id=?",
            (destination, source, _now(), dub_id))

    async def mark_placed(self, dub_id: int, sidecar_path: str, ref_path: str) -> None:
        await self._db.execute(
            "UPDATE voice_dubs SET status='placed', sidecar_path=?, ref_path=?, aligned_path=NULL, "
            "move_from=NULL, error=NULL, updated_at=? WHERE id=?",
            (sidecar_path, ref_path, _now(), dub_id))

    async def mark_rejected(self, dub_id: int, outcome: str) -> None:
        await self._db.execute(
            "UPDATE voice_dubs SET status='rejected', outcome=?, aligned_path=NULL, "
            "move_from=NULL, updated_at=? WHERE id=?", (outcome, _now(), dub_id))

    async def release_to_ready(self, dub_ids: list[int]) -> None:
        """The daemon lost the job: the voice-overs are offered to it again."""
        for dub_id in dub_ids:
            await self._db.execute(
                "UPDATE voice_dubs SET status='ready', job_id=NULL, updated_at=? WHERE id=?",
                (_now(), dub_id))

    async def reset(self, dub_id: int, reason: str) -> None:
        """The track belongs to a reference that is gone: back to the start, from the main source."""
        await self._db.execute(
            "UPDATE voice_dubs SET status='pending', attempt=0, job_id=NULL, submit_key=NULL, ref_uid=NULL, "
            "ref_path=NULL, aligned_path=NULL, sidecar_path=NULL, move_from=NULL, temp_path=NULL, outcome=NULL, "
            "error=?, updated_at=? WHERE id=?", (reason, _now(), dub_id))

    async def set_sidecar(self, dub_id: int, sidecar_path: str, ref_path: str) -> None:
        await self._db.execute(
            "UPDATE voice_dubs SET sidecar_path=?, ref_path=?, move_from=NULL, updated_at=? WHERE id=?",
            (sidecar_path, ref_path, _now(), dub_id))

    async def journal_cleanup(self, path: str, kind: str, error: str) -> None:
        """Something that could not be removed: it stays on this list until it is gone."""
        await self._db.execute(
            "INSERT INTO voice_cleanup (path, kind, error, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT (path) DO UPDATE SET kind=excluded.kind, error=excluded.error, "
            "updated_at=excluded.updated_at", (path, kind, error, _now()))

    async def cleanup_list(self) -> list[dict[str, Any]]:
        return await self._db.fetch_all("SELECT * FROM voice_cleanup ORDER BY updated_at")

    async def cleanup_done(self, path: str) -> None:
        await self._db.execute("DELETE FROM voice_cleanup WHERE path=?", (path,))

    async def delete(self, dub_id: int) -> None:
        await self._db.execute("DELETE FROM voice_dubs WHERE id=?", (dub_id,))

    async def forget_temp(self, series_id: int, season: int, episode: int) -> None:
        await self._db.execute(
            "UPDATE voice_dubs SET temp_path=NULL, aligned_path=NULL "
            "WHERE series_id=? AND season=? AND episode=?", (series_id, season, episode))
