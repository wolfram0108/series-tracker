"""SQL of the voices module: the table voice_dubs (additional voice-overs of an episode)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from core.contracts import DubStatus as S
from core.db import Database
from core.reasons import reason

from . import lifecycle
from .lifecycle import Transition

log = logging.getLogger("voices")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _in(steps: Iterable[S]) -> tuple[str, tuple[str, ...]]:
    """`IN (...)` for a set of steps and its parameters."""
    values = tuple(sorted(step.value for step in steps))
    return f"IN ({','.join('?' * len(values))})", values


# What a row points at and how each kind of pointer is removed.
POINTERS = (("output_path", "artifact"), ("move_from", "artifact"), ("aligned_path", "path"), ("temp_path", "path"))
HANDED_OVER = "handed over"
_UPSERT = ("ON CONFLICT (path) DO UPDATE SET kind=excluded.kind, error=excluded.error, "
           "scope=COALESCE(excluded.scope, voice_cleanup.scope), updated_at=excluded.updated_at")


def _hand_over(where: str, args: tuple, *, files: bool = True) -> list[tuple[str, tuple]]:
    """Journal entries for what the rows matching `where` own: their files, and the job or the unanswered
    submission of those being aligned. They run in the transaction that lets the rows go, so a name is never
    left without a record or without an owner."""
    now = _now()
    found: list[tuple[str, tuple]] = []
    if files:
        for column, kind in POINTERS:
            found.append((f"INSERT INTO voice_cleanup (path, kind, error, updated_at) SELECT {column}, ?, ?, ? "
                          f"FROM voice_dubs WHERE ({where}) AND {column} IS NOT NULL {_UPSERT}",
                          (kind, HANDED_OVER, now, *args)))
    for column, kind, condition in (("job_id", "job", "job_id IS NOT NULL"),
                                    ("submit_key", "submission", "job_id IS NULL AND submit_key IS NOT NULL")):
        found.append((f"INSERT INTO voice_cleanup (path, kind, error, scope, updated_at) SELECT {column}, ?, ?, out_dir, ? "
                      f"FROM voice_dubs WHERE ({where}) AND status=? AND {condition} {_UPSERT}",
                      (kind, HANDED_OVER, now, *args, S.ALIGNING.value)))
    return found


def _folders(folders: Iterable[str]) -> list[tuple[str, tuple]]:
    return [("INSERT INTO voice_cleanup (path, kind, error, updated_at) VALUES (?,?,?,?) " + _UPSERT,
             (folder, "tree", HANDED_OVER, _now())) for folder in folders]


class VoicesRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def _move(self, transition: Transition, assignments: str, args: tuple, where: str,
                    where_args: tuple, expected: int | None = 1, before: list | None = None) -> bool:
        """Change the step of rows by a transition of the lifecycle: rows in a step the transition does not
        start from are left as they are. True when something changed. `before` runs in the same transaction."""
        marks, steps = _in(transition.sources)
        query = (f"UPDATE voice_dubs SET status=?, {assignments + ', ' if assignments else ''}updated_at=? "
                 f"WHERE ({where}) AND status {marks}")
        params = (transition.target.value, *args, _now(), *where_args, *steps)
        if before:
            changed = await self._db.execute_many([*before, (query, params)])
        else:
            changed = await self._db.execute(query, params)
        if not transition.contested and expected is not None and changed != expected:
            log.warning("озвучки: переход %s выполнен для %s строк из %s: остальные не в тех состояниях %s",
                        transition.name, changed, expected, "/".join(steps))
        return bool(changed)

    @staticmethod
    def _row(row: dict | None) -> dict | None:
        if row is not None:
            row["chain"] = json.loads(row["chain"])
        return row

    async def for_series(self, series_id: int) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM voice_dubs WHERE series_id=? ORDER BY season, episode, voice_key", (series_id,))
        return [self._row(row) for row in rows]

    async def all_rows(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM voice_dubs ORDER BY series_id, season, episode, voice_key")
        return [self._row(row) for row in rows]

    async def for_episode(self, series_id: int, season: int, episode: int) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM voice_dubs WHERE series_id=? AND season=? AND episode=? ORDER BY voice_key",
            (series_id, season, episode))
        return [self._row(row) for row in rows]

    async def get(self, dub_id: int) -> dict[str, Any] | None:
        return self._row(await self._db.fetch_one("SELECT * FROM voice_dubs WHERE id=?", (dub_id,)))

    async def upsert(self, series_id: int, season: int, episode: int, site_episode: int,
                     voice_key: str, label: str, chain: list[dict[str, str]]) -> bool:
        """Offer the voice-over again: an untouched row takes the fresh chain and starts from its
        main source; a row that is downloading or ready is left alone. An empty chain is a failure.
        True when the row was created or taken over."""
        status = S.PENDING.value if chain else S.FAILED.value
        error = None if chain else reason("not_in_any_source")
        return bool(await self._db.execute(
            "INSERT INTO voice_dubs (series_id, season, episode, site_episode, voice_key, "
            "voice_label, chain, status, error, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (series_id, season, episode, voice_key) DO UPDATE SET "
            "site_episode=excluded.site_episode, voice_label=excluded.voice_label, "
            "chain=excluded.chain, attempt=0, status=excluded.status, error=excluded.error, "
            f"updated_at=excluded.updated_at WHERE voice_dubs.status {_in(lifecycle.OFFER.sources)[0]}",
            (series_id, season, episode, site_episode, voice_key, label,
             json.dumps(chain, ensure_ascii=False), status, error, _now(), *_in(lifecycle.OFFER.sources)[1])))

    async def pending(self, series_id: int | None = None) -> list[dict[str, Any]]:
        where, args = ("WHERE status=?", (S.PENDING.value,))
        if series_id is not None:
            where, args = ("WHERE status=? AND series_id=?", (S.PENDING.value, series_id))
        rows = await self._db.fetch_all(f"SELECT * FROM voice_dubs {where} ORDER BY id", args)
        return [self._row(row) for row in rows]

    async def claim_download(self, dub_id: int, temp_path: str) -> bool:
        """Take a waiting voice-over for download; False when someone else has already taken it."""
        return await self._move(lifecycle.CLAIM, "temp_path=?, error=NULL", (temp_path,), "id=?", (dub_id,))

    async def active_episodes(self) -> set[tuple[int, int, int]]:
        """Episodes that hold temporary files or are on their way to them: (series, season, episode)."""
        marks, steps = _in(lifecycle.HOLDS_FILES)
        rows = await self._db.fetch_all(
            "SELECT DISTINCT series_id, season, episode FROM voice_dubs WHERE "
            f"status {marks} OR (status=? AND attempt>0)", (*steps, S.PENDING.value))
        return {(row["series_id"], row["season"], row["episode"]) for row in rows}

    async def mark_ready(self, dub_id: int) -> bool:
        return await self._move(lifecycle.DOWNLOADED, "error=NULL", (), "id=?", (dub_id,))

    async def advance(self, dub_id: int, error: str) -> bool:
        """The current source failed: the next one of the chain is tried."""
        return await self._move(lifecycle.RETRY, "attempt=attempt+1, error=?", (error,), "id=?", (dub_id,))

    async def mark_failed(self, dub_id: int, error: str) -> bool:
        return await self._move(lifecycle.FAIL, "move_from=NULL, error=?", (error,), "id=?", (dub_id,))

    async def requeue_interrupted(self) -> int:
        marks, steps = _in(lifecycle.RETRY.sources)
        return await self._db.execute(
            f"UPDATE voice_dubs SET status=? WHERE status {marks}", (lifecycle.RETRY.target.value, *steps))

    async def drop_unplanned(self, series_id: int,
                             planned: set[tuple[int, int, str]]) -> list[dict[str, Any]]:
        """Rows of voice-overs that left the plan before any work was done on them, or after it failed; returns
        what was removed. Whatever such a row still points at goes to the cleanup journal with it."""
        gone = [row for row in await self.for_series(series_id)
                if (row["season"], row["episode"], row["voice_key"]) not in planned
                and row["status"] in lifecycle.OFFER.sources]
        for row in gone:
            await self._db.execute_many([*_hand_over("id=?", (row["id"],)),
                                         ("DELETE FROM voice_dubs WHERE id=?", (row["id"],))])
        return gone

    async def mark_pending(self, dub_id: int, error: str) -> bool:
        return await self._move(lifecycle.FILE_LOST, "attempt=0, error=?", (error,), "id=?", (dub_id,))

    async def delete_for_series(self, series_id: int, folders: Iterable[str] = ()) -> None:
        """The series is gone: its rows, with the jobs they wait for and the `folders` to remove, go to the journal."""
        await self._db.execute_many([*_hand_over("series_id=?", (series_id,), files=False), *_folders(folders),
                                     ("DELETE FROM voice_dubs WHERE series_id=?", (series_id,))])

    async def next_epoch(self) -> int:
        """The number of this run of the module: kept in the database and raised at every start."""
        await self._db.execute("INSERT INTO voice_epoch (id, value) VALUES (1, 1) "
                               "ON CONFLICT (id) DO UPDATE SET value = value + 1")
        return (await self._db.fetch_all("SELECT value FROM voice_epoch WHERE id=1"))[0]["value"]

    async def series_with_open_work(self) -> list[int]:
        """Series that have something to carry on after a stop: a voice-over that waits for its reference, or
        an operation that was begun and not finished, whatever step the voice-over is at."""
        marks, steps = _in(lifecycle.WAITS_FOR_REFERENCE)
        rows = await self._db.fetch_all(
            f"SELECT DISTINCT series_id FROM voice_dubs WHERE status {marks} OR move_from IS NOT NULL", steps)
        return [row["series_id"] for row in rows]

    async def by_job(self, job_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM voice_dubs WHERE job_id=? ORDER BY id", (job_id,))
        return [self._row(row) for row in rows]

    async def aligning_jobs(self) -> list[str]:
        rows = await self._db.fetch_all(
            "SELECT DISTINCT job_id FROM voice_dubs WHERE status=? AND job_id IS NOT NULL", (S.ALIGNING.value,))
        return [row["job_id"] for row in rows]

    async def mark_submitting(self, dub_ids: list[int], key: str, ref_uid: str, ref_path: str,
                              out_dir: str) -> bool:
        """Write down that these voice-overs are being sent as one job, with where its tracks are asked
        for, before the request goes out."""
        marks = ",".join("?" * len(dub_ids))
        return await self._move(
            lifecycle.SEND, "job_id=NULL, submit_key=?, ref_uid=?, ref_path=?, out_dir=?, error=NULL, outcome=NULL",
            (key, ref_uid, ref_path, out_dir), f"id IN ({marks})", tuple(dub_ids), expected=len(dub_ids))

    async def attach_job(self, key: str, job_id: str) -> None:
        """The service has the job: every voice-over sent with the key gets its number at once."""
        await self._db.execute(
            "UPDATE voice_dubs SET job_id=?, updated_at=? WHERE submit_key=? AND status=? "
            "AND job_id IS NULL", (job_id, _now(), key, S.ALIGNING.value))

    async def release_submission(self, key: str) -> bool:
        """The job was never accepted: its voice-overs go back to waiting for a new one."""
        return await self._move(lifecycle.UNSEND, "submit_key=NULL, ref_uid=NULL, ref_path=NULL, out_dir=NULL",
                                (), "submit_key=? AND job_id IS NULL", (key,), expected=None)

    async def unsent_submissions(self) -> list[str]:
        """Keys of jobs sent without a known answer."""
        rows = await self._db.fetch_all(
            "SELECT DISTINCT submit_key FROM voice_dubs WHERE status=? AND job_id IS NULL "
            "AND submit_key IS NOT NULL", (S.ALIGNING.value,))
        return [row["submit_key"] for row in rows]

    async def by_key(self, key: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM voice_dubs WHERE submit_key=? ORDER BY id", (key,))
        return [self._row(row) for row in rows]

    async def mark_aligned(self, dub_id: int, aligned_path: str) -> bool:
        return await self._move(lifecycle.ALIGNED, "aligned_path=?", (aligned_path,), "id=?", (dub_id,))

    async def begin_move(self, dub_id: int, destination: str, source: str) -> None:
        """Write down where a track is going before it moves; a restart finishes the move from this."""
        await self._db.execute(
            "UPDATE voice_dubs SET output_path=?, move_from=?, updated_at=? WHERE id=?",
            (destination, source, _now(), dub_id))

    async def mark_placed(self, dub_id: int, output_path: str, ref_path: str) -> bool:
        return await self._move(lifecycle.PLACE, "output_path=?, ref_path=?, aligned_path=NULL, move_from=NULL, "
                                "error=NULL", (output_path, ref_path), "id=?", (dub_id,))

    async def mark_rejected(self, dub_id: int, outcome: str) -> bool:
        return await self._move(lifecycle.REFUSE, "outcome=?, aligned_path=NULL, move_from=NULL",
                                (outcome,), "id=?", (dub_id,))

    async def release_to_ready(self, dub_ids: list[int]) -> None:
        """The daemon lost the job: the voice-overs are offered to it again."""
        for dub_id in dub_ids:
            await self._move(lifecycle.UNSEND, "job_id=NULL", (), "id=?", (dub_id,))

    async def reset(self, dub_id: int, reason: str) -> bool:
        """The track belongs to a reference that is gone: back to the start, from the main source. What the row
        owned goes to the cleanup journal in the same transaction."""
        marks, steps = _in(lifecycle.RESTART.sources)
        return await self._move(
            lifecycle.RESTART, "attempt=0, job_id=NULL, submit_key=NULL, out_dir=NULL, ref_uid=NULL, ref_path=NULL, "
            "aligned_path=NULL, output_path=NULL, move_from=NULL, temp_path=NULL, outcome=NULL, error=?",
            (reason,), "id=?", (dub_id,), before=_hand_over(f"id=? AND status {marks}", (dub_id, *steps)))

    async def set_output(self, dub_id: int, output_path: str, ref_path: str) -> None:
        await self._db.execute(
            "UPDATE voice_dubs SET output_path=?, ref_path=?, move_from=NULL, updated_at=? WHERE id=?",
            (output_path, ref_path, _now(), dub_id))

    async def journal_cleanup(self, path: str, kind: str, error: str, scope: str | None = None) -> None:
        """Something to remove, or a job to stop (with the folder it writes in): it stays on this list until done."""
        await self._db.execute(
            "INSERT INTO voice_cleanup (path, kind, error, scope, updated_at) VALUES (?,?,?,?,?) " + _UPSERT,
            (path, kind, error, scope, _now()))

    async def cleanup_list(self) -> list[dict[str, Any]]:
        return await self._db.fetch_all("SELECT * FROM voice_cleanup ORDER BY updated_at")

    async def cleanup_done(self, path: str) -> None:
        await self._db.execute("DELETE FROM voice_cleanup WHERE path=?", (path,))

    async def drop_episode(self, series_id: int, season: int, episode: int, folders: Iterable[str]) -> None:
        """An episode left the plan: its rows go, and everything they owned, with the `folders`, to the journal."""
        where, args = "series_id=? AND season=? AND episode=?", (series_id, season, episode)
        await self._db.execute_many([*_hand_over(where, args), *_folders(folders),
                                     (f"DELETE FROM voice_dubs WHERE {where}", args)])

    async def settle_episode(self, series_id: int, season: int, episode: int, folders: Iterable[str]) -> None:
        """The episode is done: its temporary `folders` go to the journal as its rows let go of their files."""
        await self._db.execute_many([*_folders(folders), (
            "UPDATE voice_dubs SET temp_path=NULL, aligned_path=NULL "
            "WHERE series_id=? AND season=? AND episode=?", (series_id, season, episode))])
