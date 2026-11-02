"""SQL of the voices module: voice_dubs (additional voice-overs of an episode) and voice_files (the video of a source
row that covers several episodes, downloaded once and shared by the voice-overs of its episodes).

A file is owned by its row in voice_files: its path is in no voice-over's own `temp_path`, a voice-over that reads it
carries `file_id` and the rows are returned with the path of the file as their `temp_path`. A file goes to the cleanup
journal, in the same transaction that deletes its row, when no voice-over still waits for it; a transfer that is under
way at that moment is handed to the module to cancel (`on_idle_transfer`), so a file is never held for a transfer."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Collection, Iterable

from core.contracts import DubStatus as S
from core.db import Database
from core.reasons import reason

from . import lifecycle
from .lifecycle import Transition

log = logging.getLogger("voices")


# Steps in which a voice-over still reads the file it is linked to.
_READING = lifecycle.READS_FILE
_ROWS = "SELECT *, (SELECT temp_path FROM voice_files WHERE id = voice_dubs.file_id) AS file_path FROM voice_dubs"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _in(steps: Iterable[S]) -> tuple[str, tuple[str, ...]]:
    """`IN (...)` for a set of steps and its parameters."""
    values = tuple(sorted(step.value for step in steps))
    return f"IN ({','.join('?' * len(values))})", values


# What a row points at and how each kind of pointer is removed.
POINTERS = (("output_path", "artifact"), ("move_from", "artifact"), ("aligned_path", "path"), ("temp_path", "path"))
HANDED_OVER = "handed over"
# Stored as it is written, so that a failure for want of any source can be told from the others by equality.
NOT_IN_ANY_SOURCE = reason("not_in_any_source")
_UPSERT = ("ON CONFLICT (path) DO UPDATE SET kind=excluded.kind, error=excluded.error, "
           "scope=COALESCE(excluded.scope, voice_cleanup.scope), version=voice_cleanup.version + 1, "
           "updated_at=excluded.updated_at")


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
    found.extend(_jobs(where, args))
    found.append(("INSERT INTO voice_cleanup (path, kind, error, scope, updated_at) SELECT submit_key, ?, ?, out_dir, ? "
                  f"FROM voice_dubs WHERE ({where}) AND status=? AND job_id IS NULL AND submit_key IS NOT NULL {_UPSERT}",
                  ("submission", HANDED_OVER, now, *args, S.ALIGNING.value)))
    return found


def _jobs(where: str, args: tuple) -> list[tuple[str, tuple]]:
    """Journal entries for the jobs of the rows matching `where`, whatever their step: a job that ended still has a
    record in the service's history and a folder it wrote in, and both are the owner's to let go."""
    return [("INSERT INTO voice_cleanup (path, kind, error, scope, updated_at) SELECT job_id, ?, ?, out_dir, ? "
             f"FROM voice_dubs WHERE ({where}) AND job_id IS NOT NULL {_UPSERT}",
             ("job", HANDED_OVER, _now(), *args))]


def _folders(folders: Iterable[str]) -> list[tuple[str, tuple]]:
    return [("INSERT INTO voice_cleanup (path, kind, error, updated_at) VALUES (?,?,?,?) " + _UPSERT,
             (folder, "tree", HANDED_OVER, _now())) for folder in folders]


def _journal_file(conn: sqlite3.Connection, temp_path: str | None) -> None:
    """The folder of a file (one folder per file) goes to the journal."""
    if temp_path:
        conn.execute("INSERT INTO voice_cleanup (path, kind, error, updated_at) VALUES (?,?,?,?) " + _UPSERT,
                     (os.path.dirname(temp_path), "tree", HANDED_OVER, _now()))


def _release_idle(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Journal and delete the files that no voice-over reads, and cut the links of the voice-overs that still point
    at them (they are done with the file). Runs inside the caller's transaction; a file that was downloading is
    returned with that status for the caller to cancel its transfer."""
    marks, steps = _in(_READING)
    idle = [dict(row) for row in conn.execute(
        f"SELECT * FROM voice_files WHERE NOT EXISTS (SELECT 1 FROM voice_dubs WHERE file_id = voice_files.id "
        f"AND status {marks})", steps)]
    for file in idle:
        _journal_file(conn, file["temp_path"])
        conn.execute("UPDATE voice_dubs SET file_id=NULL WHERE file_id=?", (file["id"],))
        conn.execute("DELETE FROM voice_files WHERE id=?", (file["id"],))
    return idle


class VoicesRepository:
    def __init__(self, db: Database, *, on_idle_transfer: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._db = db
        self._on_idle_transfer = on_idle_transfer

    def _cancel_transfers(self, released: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The files released while their transfer was under way: the module is told, after the transaction has ended."""
        for file in released:
            if file["status"] == S.DOWNLOADING.value and self._on_idle_transfer is not None:
                self._on_idle_transfer(file)
        return released

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
            file_path = row.pop("file_path", None)
            if row.get("file_id") is not None and file_path:
                row["temp_path"] = file_path
        return row

    async def for_series(self, series_id: int) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            f"{_ROWS} WHERE series_id=? ORDER BY season, episode, part, voice_key", (series_id,))
        return [self._row(row) for row in rows]

    async def all_rows(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(f"{_ROWS} ORDER BY series_id, season, episode, part, voice_key")
        return [self._row(row) for row in rows]

    async def for_episode(self, series_id: int, season: int, episode: int, part: int) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            f"{_ROWS} WHERE series_id=? AND season=? AND episode=? AND part=? ORDER BY voice_key",
            (series_id, season, episode, part))
        return [self._row(row) for row in rows]

    async def get(self, dub_id: int) -> dict[str, Any] | None:
        return self._row(await self._db.fetch_one(f"{_ROWS} WHERE id=?", (dub_id,)))

    async def upsert(self, series_id: int, season: int, episode: int, part: int, voice_key: str, label: str,
                     chain: list[dict[str, Any]], *, offer: bool) -> bool:
        """Plan a voice-over: a new row is created; an existing one takes the fresh chain only where nothing was done
        yet. A row that is downloading, ready or further is left alone. An empty chain is a failure.

        A new row is not admitted to download: a sync of the series admits it (`admit`). With `offer` (that sync) a row
        that failed or waits is offered again from its main candidate. Without it a failed row is left alone, except one
        that failed for want of any source and now has one: nothing was tried, so nothing is retried. A waiting row
        keeps the file it was linked to only while its chain is the same. True when the row was created or taken over."""
        status = S.PENDING.value if chain else S.FAILED.value
        error = None if chain else NOT_IN_ANY_SOURCE
        marks, steps = _in(lifecycle.OFFER.sources)
        if offer:
            where, extra = f"voice_dubs.status {marks}", steps
        else:
            where = ("((voice_dubs.status = ? AND voice_dubs.attempt = 0 AND (voice_dubs.chain IS NOT excluded.chain "
                     "OR voice_dubs.voice_label IS NOT excluded.voice_label OR voice_dubs.status IS NOT excluded.status "
                     "OR voice_dubs.error IS NOT excluded.error)) "
                     "OR (voice_dubs.status = ? AND voice_dubs.error = ? AND excluded.status = ?))")
            extra = (S.PENDING.value, S.FAILED.value, NOT_IN_ANY_SOURCE, S.PENDING.value)
        return bool(await self._db.execute(
            "INSERT INTO voice_dubs (series_id, season, episode, part, voice_key, "
            "voice_label, chain, status, error, updated_at, admitted) VALUES (?,?,?,?,?,?,?,?,?,?,0) "
            "ON CONFLICT (series_id, season, episode, part, voice_key) DO UPDATE SET "
            "voice_label=excluded.voice_label, "
            "chain=excluded.chain, attempt=0, status=excluded.status, error=excluded.error, "
            "file_id=CASE WHEN voice_dubs.status = ? AND voice_dubs.chain = excluded.chain "
            "THEN voice_dubs.file_id END, "
            f"updated_at=excluded.updated_at WHERE {where}",
            (series_id, season, episode, part, voice_key, label,
             json.dumps(chain, ensure_ascii=False), status, error, _now(), S.PENDING.value, *extra)))

    async def admit(self, series_id: int, planned: Iterable[tuple[int, int, int, str]]) -> None:
        """A sync of the series was asked for: the voice-overs it plans may be downloaded."""
        await self._db.execute_many([(
            "UPDATE voice_dubs SET admitted=1 WHERE series_id=? AND season=? AND episode=? AND part=? "
            "AND voice_key=? AND admitted=0", (series_id, season, episode, part, key))
            for season, episode, part, key in planned])

    async def relabel(self, series_id: int, season: int, episode: int, part: int, voice_key: str,
                      label: str) -> bool:
        """The layout calls a recorded voice-over by another name (a group was made or renamed): the key stays."""
        return bool(await self._db.execute(
            "UPDATE voice_dubs SET voice_label=?, updated_at=? WHERE series_id=? AND season=? AND episode=? "
            "AND part=? AND voice_key=? AND voice_label != ?",
            (label, _now(), series_id, season, episode, part, voice_key, label)))

    async def pending(self, series_id: int | None = None) -> list[dict[str, Any]]:
        """Waiting voice-overs that a sync admitted; a planned row that was not admitted waits for one."""
        where, args = ("WHERE status=? AND admitted=1", (S.PENDING.value,))
        if series_id is not None:
            where, args = ("WHERE status=? AND admitted=1 AND series_id=?", (S.PENDING.value, series_id))
        rows = await self._db.fetch_all(f"{_ROWS} {where} ORDER BY id", args)
        return [self._row(row) for row in rows]

    async def claim_download(self, dub_id: int, temp_path: str) -> bool:
        """Take a waiting voice-over for download; False when someone else has already taken it."""
        return await self._move(lifecycle.CLAIM, "temp_path=?, error=NULL", (temp_path,), "id=?", (dub_id,))

    async def active_episodes(self) -> set[tuple[int, int, int, int]]:
        """Episodes that hold temporary files or are on their way to them: (series, season, episode, part)."""
        marks, steps = _in(lifecycle.HOLDS_FILES)
        rows = await self._db.fetch_all(
            "SELECT DISTINCT series_id, season, episode, part FROM voice_dubs WHERE "
            f"status {marks} OR (status=? AND attempt>0)", (*steps, S.PENDING.value))
        return {(row["series_id"], row["season"], row["episode"], row["part"]) for row in rows}

    async def mark_ready(self, dub_id: int) -> bool:
        return await self._move(lifecycle.DOWNLOADED, "error=NULL", (), "id=?", (dub_id,))

    async def advance(self, dub_id: int, error: str) -> bool:
        """The current source failed: the next one of the chain is tried."""
        return await self._move(lifecycle.RETRY, "attempt=attempt+1, error=?, file_id=NULL", (error,), "id=?",
                                (dub_id,))

    async def mark_failed(self, dub_id: int, error: str) -> bool:
        return await self._move(lifecycle.FAIL, "move_from=NULL, error=?", (error,), "id=?", (dub_id,))

    async def requeue_interrupted(self) -> int:
        """After a restart no download is under way: voice-overs and files that were downloading wait again."""
        marks, steps = _in(lifecycle.RETRY.sources)
        await self._db.execute("UPDATE voice_files SET status=? WHERE status=?", (S.PENDING.value, S.DOWNLOADING.value))
        return await self._db.execute(
            f"UPDATE voice_dubs SET status=? WHERE status {marks}", (lifecycle.RETRY.target.value, *steps))

    async def _atomically(self, statements: list[tuple[str, tuple]], *, files: bool = True) -> None:
        """The statements in one transaction, and with them the release of the files they leave without a reader."""
        def work(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            conn.execute("BEGIN IMMEDIATE")
            for query, params in statements:
                conn.execute(query, tuple(params))
            return _release_idle(conn) if files else []
        self._cancel_transfers(await self._db.transaction(work))

    async def drop_unplanned(self, series_id: int, planned: set[tuple[int, int, int, str]],
                             keep: Collection[tuple[int, int, int]] = (),
                             referenced: Collection[tuple[int, int, int]] = ()) -> list[dict[str, Any]]:
        """Rows of voice-overs that left the plan; returns what was removed. Whatever such a row owns goes to the
        cleanup journal with it: its files, the track placed for it, the job at the service.

        A voice-over of an episode in `referenced` (the plan of that episode is known) leaves in whatever step it is
        at: a download under way is cancelled by the caller, a track already placed is removed. In another episode,
        which has no reference at the moment, only a row that had no work done on it, or whose work failed, leaves:
        what else it holds is decided when the episode is seen without a reference. The rows of the episodes in `keep`
        stay: their plan is not known at the moment."""
        gone = [row for row in await self.for_series(series_id)
                if (row["season"], row["episode"], row["part"], row["voice_key"]) not in planned
                and (row["season"], row["episode"], row["part"]) not in keep
                and ((row["season"], row["episode"], row["part"]) in referenced
                     or row["status"] in lifecycle.OFFER.sources
                     or (row["status"] == S.DOWNLOADING and row["file_id"] is not None))]
        for row in gone:
            await self.retire(row["id"])
        return gone

    async def retire(self, dub_id: int) -> None:
        """The row is not wanted: whatever it owns goes to the journal with it, in one step."""
        await self._atomically([*_hand_over("id=?", (dub_id,)), ("DELETE FROM voice_dubs WHERE id=?", (dub_id,))])

    async def set_chain(self, dub_id: int, chain: list[dict[str, Any]], attempt: int) -> None:
        """The candidates of a row that has begun: the same work goes on from the candidate it began with."""
        await self._db.execute("UPDATE voice_dubs SET chain=?, attempt=? WHERE id=?",
                               (json.dumps(chain, ensure_ascii=False), attempt, dub_id))

    async def mark_pending(self, dub_id: int, error: str) -> bool:
        return await self._move(lifecycle.FILE_LOST, "attempt=0, error=?", (error,), "id=?", (dub_id,))

    async def delete_for_series(self, series_id: int, folders: Iterable[str] = ()) -> None:
        """The series is gone: its rows, with the jobs they wait for, its files and the `folders` to remove, go to
        the journal."""
        def work(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            for query, params in [*_hand_over("series_id=?", (series_id,), files=False), *_folders(folders)]:
                conn.execute(query, tuple(params))
            for file in conn.execute("SELECT id, temp_path FROM voice_files WHERE series_id=?", (series_id,)).fetchall():
                _journal_file(conn, file["temp_path"])
            conn.execute("DELETE FROM voice_dubs WHERE series_id=?", (series_id,))
            conn.execute("DELETE FROM voice_files WHERE series_id=?", (series_id,))
        await self._db.transaction(work)

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
        rows = await self._db.fetch_all(f"{_ROWS} WHERE job_id=? ORDER BY id", (job_id,))
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
        rows = await self._db.fetch_all(f"{_ROWS} WHERE submit_key=? ORDER BY id", (key,))
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
            "aligned_path=NULL, output_path=NULL, move_from=NULL, temp_path=NULL, file_id=NULL, outcome=NULL, error=?",
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

    async def cleanup_done(self, path: str, version: int) -> bool:
        """End the entry in the version that was read; an entry handed over again since is not touched."""
        return bool(await self._db.execute("DELETE FROM voice_cleanup WHERE path=? AND version=?", (path, version)))

    async def note_cleanup_error(self, path: str, error: str, only_if: str | None = None) -> None:
        """Remember why an attempt did not end the entry (`only_if`: only when it still says that); the version
        stays, since the obligation is the same."""
        if only_if is None:
            await self._db.execute("UPDATE voice_cleanup SET error=?, updated_at=? WHERE path=?", (error, _now(), path))
        else:
            await self._db.execute("UPDATE voice_cleanup SET error=?, updated_at=? WHERE path=? AND error=?",
                                   (error, _now(), path, only_if))

    async def drop_episode(self, series_id: int, season: int, episode: int, part: int,
                           folders: Iterable[str]) -> None:
        """An episode left the plan: its rows go, and everything they owned, with the `folders`, to the journal."""
        where, args = "series_id=? AND season=? AND episode=? AND part=?", (series_id, season, episode, part)
        await self._atomically([*_hand_over(where, args), *_folders(folders),
                                (f"DELETE FROM voice_dubs WHERE {where}", args)])

    async def hand_over_jobs(self, series_id: int, season: int, episode: int, part: int) -> None:
        """The jobs of an episode that is settled go to the journal: the service drops their records and the
        folders they wrote in are removed."""
        await self._db.execute_many(_jobs("series_id=? AND season=? AND episode=? AND part=?",
                                          (series_id, season, episode, part)))

    async def settle_episode(self, series_id: int, season: int, episode: int, part: int,
                             folders: Iterable[str]) -> None:
        """The episode is done: its temporary `folders` go to the journal as its rows let go of their files."""
        await self._atomically([*_folders(folders), (
            "UPDATE voice_dubs SET temp_path=NULL, aligned_path=NULL, file_id=NULL "
            "WHERE series_id=? AND season=? AND episode=? AND part=?", (series_id, season, episode, part))])

    # --- the files of source rows that cover several episodes -------------------------------------------------------

    async def by_file(self, file_id: int) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(f"{_ROWS} WHERE file_id=? ORDER BY id", (file_id,))
        return [self._row(row) for row in rows]

    async def file(self, file_id: int) -> dict[str, Any] | None:
        return await self._db.fetch_one("SELECT * FROM voice_files WHERE id=?", (file_id,))

    async def files_of(self, series_id: int) -> list[dict[str, Any]]:
        return await self._db.fetch_all("SELECT * FROM voice_files WHERE series_id=? ORDER BY id", (series_id,))

    async def join_file(self, dub_id: int, series_id: int, source_id: int, row_key: str, url: str,
                        path_of: Callable[[int], str]) -> dict[str, Any] | None:
        """The waiting voice-over takes the file of a source row for its download: the file is made if there is none,
        the voice-over is linked to it (downloading, or ready when the file is on disk already) and the file is claimed
        for download when nobody has it. The waiting voice-overs of the series whose current candidate is this file are
        linked too, so that the file stays until the last of them is done. None when the voice-over is not waiting any more;
        else the file, with `claimed` true for the caller that has to send the download."""
        def work(conn: sqlite3.Connection) -> dict[str, Any] | None:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM voice_dubs WHERE id=? AND status=?", (dub_id, S.PENDING.value)).fetchone() is None:
                return None  # the voice-over is gone or taken: no file is made for nobody
            where = "WHERE series_id=? AND source_id=? AND row_key=? AND url=?"
            found = conn.execute(f"SELECT * FROM voice_files {where}", (series_id, source_id, row_key, url)).fetchone()
            if found is None:
                conn.execute("INSERT INTO voice_files (series_id, source_id, row_key, url, status, updated_at) "
                             "VALUES (?,?,?,?,?,?)", (series_id, source_id, row_key, url, S.PENDING.value, _now()))
                found = conn.execute(f"SELECT * FROM voice_files {where}", (series_id, source_id, row_key, url)).fetchone()
            file = dict(found)
            target = S.READY.value if file["status"] == S.READY.value else S.DOWNLOADING.value
            if not conn.execute("UPDATE voice_dubs SET status=?, file_id=?, error=NULL, updated_at=? "
                                "WHERE id=? AND status=?", (target, file["id"], _now(), dub_id,
                                                            S.PENDING.value)).rowcount:
                return None
            file["claimed"] = file["status"] in (S.PENDING.value, S.FAILED.value)
            if file["claimed"]:
                file["temp_path"] = file["temp_path"] or path_of(file["id"])
                conn.execute("UPDATE voice_files SET status=?, attempt=attempt+1, error=NULL, temp_path=?, "
                             "updated_at=? WHERE id=?", (S.DOWNLOADING.value, file["temp_path"], _now(), file["id"]))
                file["status"] = S.DOWNLOADING.value
            conn.execute(
                "UPDATE voice_dubs SET file_id=? WHERE series_id=? AND status=? AND file_id IS NULL AND id != ? "
                "AND json_extract(chain, '$[' || attempt || '].file.source_id') = ? "
                "AND json_extract(chain, '$[' || attempt || '].file.row_key') = ? "
                "AND json_extract(chain, '$[' || attempt || '].url') = ?",
                (file["id"], series_id, S.PENDING.value, dub_id, source_id, row_key, url))
            return file
        return await self._db.transaction(work)

    async def finish_file(self, file_id: int, ok: bool, error: str | None) -> list[dict[str, Any]] | None:
        """The transfer of a file ended. The file becomes ready, or failed with the error; the voice-overs that waited
        on its transfer are returned (ready now, or still downloading for the caller to move to their next candidate).
        None when the file is not downloading: a late or repeated ending."""
        def work(conn: sqlite3.Connection) -> list[dict[str, Any]] | None:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("UPDATE voice_files SET status=?, error=?, updated_at=? WHERE id=? AND status=?",
                                (S.READY.value if ok else S.FAILED.value, None if ok else error, _now(), file_id,
                                 S.DOWNLOADING.value)).rowcount:
                return None
            if ok:
                conn.execute("UPDATE voice_dubs SET status=?, error=NULL, updated_at=? WHERE file_id=? AND status=?",
                             (S.READY.value, _now(), file_id, S.DOWNLOADING.value))
            ids = [row["id"] for row in conn.execute("SELECT id FROM voice_dubs WHERE file_id=? AND status=?", (
                file_id, S.READY.value if ok else S.DOWNLOADING.value))]
            return ids
        ids = await self._db.transaction(work)
        return None if ids is None else [await self.get(each) for each in ids]

    async def file_lost(self, file_id: int) -> None:
        """The file is gone from the disk: it is downloaded again by the voice-overs that read it."""
        await self._db.execute("UPDATE voice_files SET status=?, updated_at=? WHERE id=? AND status=?",
                               (S.PENDING.value, _now(), file_id, S.READY.value))

    async def release_idle_files(self) -> list[dict[str, Any]]:
        """Files no voice-over reads any more go to the cleanup journal, each with its row deleted in the same
        transaction; the transfer of one that is downloading is cancelled. Returns what was released."""
        def work(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            conn.execute("BEGIN IMMEDIATE")
            return _release_idle(conn)
        return self._cancel_transfers(await self._db.transaction(work))
