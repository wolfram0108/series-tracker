"""SQL of the voices module: voice_dubs (additional voice-overs of an episode), voice_files (the video of a source
row that covers several episodes, downloaded once and shared by the voice-overs of its episodes, or by the cuts of the
episodes it holds) and voice_cuts (stage 12: an episode cut out of its compilation by the alignment service).

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

from . import cut_lifecycle as cut_steps
from . import lifecycle
from .cut_lifecycle import CutStep as C, CutTransition
from .lifecycle import Transition

log = logging.getLogger("voices")


# Steps in which a voice-over still reads the file it is linked to.
_READING = lifecycle.READS_FILE
_ROWS = "SELECT *, (SELECT temp_path FROM voice_files WHERE id = voice_dubs.file_id) AS file_path FROM voice_dubs"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


# The ends of a voice-over that mean its job ended in an error: a clear queue keeps such a job.
KEPT_JOBS = frozenset({S.FAILED, S.REJECTED})


def _in(steps: Iterable[S]) -> tuple[str, tuple[str, ...]]:
    """`IN (...)` for a set of steps and its parameters."""
    values = tuple(sorted(step.value for step in steps))
    return f"IN ({','.join('?' * len(values))})", values


# What a row points at and how each kind of pointer is removed.
POINTERS = (("output_path", "artifact"), ("move_from", "artifact"), ("aligned_path", "path"), ("temp_path", "path"))
HANDED_OVER = "handed over"
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
    found.append(("INSERT INTO voice_cleanup (path, kind, error, scope, updated_at) SELECT d.submit_key, ?, ?, d.out_dir, ? "
                  f"FROM voice_dubs d WHERE ({where}) AND d.status=? AND d.job_id IS NULL AND d.submit_key IS NOT NULL "
                  f"{_SHARED.format(column='submit_key', where=where)} {_UPSERT}",
                  ("submission", HANDED_OVER, now, *args, S.ALIGNING.value, *args)))
    return found


# A job (or a submission) is the work of a batch of rows, not of one: it is handed over with the last row that holds it. The
# outer `d` is a row that goes; the inner rows are the others, and `where` binds to whichever table is nearest.
_SHARED = ("AND NOT EXISTS (SELECT 1 FROM voice_dubs o WHERE o.{column} = d.{column} AND NOT ({where}))")


def _jobs(where: str, args: tuple) -> list[tuple[str, tuple]]:
    """Journal entries for the jobs of the rows matching `where`, whatever their step: a job that ended still has a
    record in the service's history and a folder it wrote in, and both are the owner's to let go. A job another row
    that stays still holds is not handed over: it is that row's work too."""
    return [("INSERT INTO voice_cleanup (path, kind, error, scope, updated_at) SELECT d.job_id, ?, ?, d.out_dir, ? "
             f"FROM voice_dubs d WHERE ({where}) AND d.job_id IS NOT NULL {_SHARED.format(column='job_id', where=where)} {_UPSERT}",
             ("job", HANDED_OVER, _now(), *args, *args))]


def _folders(folders: Iterable[str]) -> list[tuple[str, tuple]]:
    return [("INSERT INTO voice_cleanup (path, kind, error, updated_at) VALUES (?,?,?,?) " + _UPSERT,
             (folder, "tree", HANDED_OVER, _now())) for folder in folders]


def _journal_file(conn: sqlite3.Connection, temp_path: str | None) -> None:
    """The folder of a file (one folder per file) goes to the journal."""
    if temp_path:
        conn.execute("INSERT INTO voice_cleanup (path, kind, error, updated_at) VALUES (?,?,?,?) " + _UPSERT,
                     (os.path.dirname(temp_path), "tree", HANDED_OVER, _now()))


def _release_idle(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Journal and delete the files that no voice-over and no cut reads, and cut the links of the voice-overs that still
    point at them (they are done with the file). Runs inside the caller's transaction; a file that was downloading is
    returned with that status for the caller to cancel its transfer."""
    marks, steps = _in(_READING)
    cut_marks, reading = _in(cut_steps.READING)
    idle = [dict(row) for row in conn.execute(
        f"SELECT * FROM voice_files WHERE NOT EXISTS (SELECT 1 FROM voice_dubs WHERE file_id = voice_files.id "
        f"AND status {marks}) AND NOT EXISTS (SELECT 1 FROM voice_cuts WHERE file_id = voice_files.id "
        f"AND status {cut_marks})", (*steps, *reading))]
    for file in idle:
        _journal_file(conn, file["temp_path"])
        if file.get("work_dir"):
            conn.execute("INSERT INTO voice_cleanup (path, kind, error, updated_at) VALUES (?,?,?,?) " + _UPSERT,
                         (file["work_dir"], "tree", HANDED_OVER, _now()))
        conn.execute("UPDATE voice_dubs SET file_id=NULL WHERE file_id=?", (file["id"],))
        conn.execute("UPDATE voice_cuts SET file_id=NULL WHERE file_id=?", (file["id"],))
        conn.execute("DELETE FROM voice_files WHERE id=?", (file["id"],))
    return idle


def _cut_hand_over(conn: sqlite3.Connection, where: str, args: tuple, *, copy: bool = True, job: bool = True) -> None:
    """Journal what the cuts matching `where` own: with `copy` the folder of the copy of REF 2 (and an unanswered
    submission); with `job` the job, with the folder the service wrote its video in (`<folder of the file>/_out/<the same
    name>`) as its scope, or that folder alone when there is no job. Runs in the transaction that lets them go."""
    now = _now()
    for cut in conn.execute(f"SELECT * FROM voice_cuts WHERE {where}", args).fetchall():
        output = os.path.join(os.path.dirname(cut["work_dir"]), OUT_DIR, os.path.basename(cut["work_dir"])) \
            if cut["work_dir"] else None
        entries = []
        if copy and cut["work_dir"]:
            entries.append((cut["work_dir"], "tree", None))
            if cut["status"] == C.SUBMITTING and not cut["job_id"] and cut["submit_key"]:
                entries.append((cut["submit_key"], "submission", output))
        if job and cut["job_id"]:
            entries.append((cut["job_id"], "job", output))
        elif job and output:
            entries.append((output, "tree", None))
        for path, kind, scope in entries:
            conn.execute("INSERT INTO voice_cleanup (path, kind, error, scope, updated_at) VALUES (?,?,?,?,?) " + _UPSERT,
                         (path, kind, HANDED_OVER, scope, now))


# The folder, beside the copies of REF 2, the service writes the videos of a batch of cuts in (and keeps its cache of the
# compilation in).
OUT_DIR = "_out"


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
        yet. A row that is downloading, ready or further is left alone. An empty chain is no failure: no source has the
        voice-over for the episode, and the row is `absent` until one does.

        A new row is not admitted to download: a sync of the series admits it (`admit`). With `offer` (that sync) a row
        that failed or waits is offered again from its main candidate. Without it a failed row is left alone, except one
        that was absent and now has a source: nothing was tried, so nothing is retried. A waiting row
        keeps the file it was linked to only while its chain is the same. True when the row was created or taken over."""
        status = S.PENDING.value if chain else lifecycle.ABSENT.target.value
        error = None
        marks, steps = _in(lifecycle.OFFER.sources)
        if offer:
            where, extra = f"voice_dubs.status {marks}", steps
        else:
            where = ("((voice_dubs.status = ? AND voice_dubs.attempt = 0 AND (voice_dubs.chain IS NOT excluded.chain "
                     "OR voice_dubs.voice_label IS NOT excluded.voice_label OR voice_dubs.status IS NOT excluded.status "
                     "OR voice_dubs.error IS NOT excluded.error)) "
                     "OR (voice_dubs.status = ? AND excluded.status IS NOT voice_dubs.status))")
            extra = (S.PENDING.value, lifecycle.ABSENT.target.value)
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
            _cut_hand_over(conn, "series_id=?", (series_id,))
            for file in conn.execute("SELECT work_dir FROM voice_files WHERE series_id=? AND work_dir IS NOT NULL",
                                     (series_id,)).fetchall():
                conn.execute("INSERT INTO voice_cleanup (path, kind, error, updated_at) VALUES (?,?,?,?) " + _UPSERT,
                             (file["work_dir"], "tree", HANDED_OVER, _now()))
            conn.execute("DELETE FROM voice_dubs WHERE series_id=?", (series_id,))
            conn.execute("DELETE FROM voice_cuts WHERE series_id=?", (series_id,))
            conn.execute("DELETE FROM voice_files WHERE series_id=?", (series_id,))
        await self._db.transaction(work)

    async def next_epoch(self) -> int:
        """The number of this run of the module: kept in the database and raised at every start."""
        await self._db.execute("INSERT INTO voice_epoch (id, value) VALUES (1, 1) "
                               "ON CONFLICT (id) DO UPDATE SET value = value + 1")
        return (await self._db.fetch_all("SELECT value FROM voice_epoch WHERE id=1"))[0]["value"]

    async def series_with_live_dubs(self) -> list[int]:
        """Series with a voice-over a sync admitted that is not at its end: work of a run."""
        marks, steps = _in(lifecycle.FINAL)
        rows = await self._db.fetch_all(
            f"SELECT DISTINCT series_id FROM voice_dubs WHERE admitted = 1 AND NOT (status {marks})", steps)
        return [row["series_id"] for row in rows]

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
                              out_dir: str, aligned_to: str | None = None, ref_rev: int | None = None) -> bool:
        """Write down that these voice-overs are being sent as one job, with where its tracks are asked
        for, the identity of the video they are aligned to and the revision of its file, before the request goes out."""
        marks = ",".join("?" * len(dub_ids))
        return await self._move(
            lifecycle.SEND, "job_id=NULL, submit_key=?, ref_uid=?, ref_path=?, out_dir=?, aligned_to=?, ref_rev=?, "
            "error=NULL, outcome=NULL", (key, ref_uid, ref_path, out_dir, aligned_to or None, ref_rev),
            f"id IN ({marks})", tuple(dub_ids), expected=len(dub_ids))

    async def attach_job(self, key: str, job_id: str) -> None:
        """The service has the job: every voice-over sent with the key gets its number at once."""
        await self._db.execute(
            "UPDATE voice_dubs SET job_id=?, updated_at=? WHERE submit_key=? AND status=? "
            "AND job_id IS NULL", (job_id, _now(), key, S.ALIGNING.value))

    async def release_submission(self, key: str) -> bool:
        """The job was never accepted: its voice-overs go back to waiting for a new one."""
        return await self._move(lifecycle.UNSEND, "submit_key=NULL, ref_uid=NULL, ref_path=NULL, out_dir=NULL, aligned_to=NULL",
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

    async def mark_aligned(self, dub_id: int, aligned_path: str, doubt: str | None = None) -> bool:
        """`doubt` is the mark the track keeps through its placing: the service did not vouch for its sync."""
        return await self._move(lifecycle.ALIGNED, "aligned_path=?, outcome=?", (aligned_path, doubt), "id=?", (dub_id,))

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

    async def turn_to_next(self, dub_id: int, outcome: str, attempt: int) -> bool:
        """The service turned the pair down: the video of the current source is not the episode. The candidate `attempt`
        of the chain (the next other video) is tried, from its download; what the row owned (the file, its share of the
        job) goes to the cleanup journal in the same transaction."""
        marks, steps = _in(lifecycle.NEXT_SOURCE.sources)
        return await self._move(
            lifecycle.NEXT_SOURCE, "attempt=?, job_id=NULL, submit_key=NULL, out_dir=NULL, ref_uid=NULL, "
            "ref_path=NULL, aligned_to=NULL, ref_rev=NULL, aligned_path=NULL, output_path=NULL, move_from=NULL, "
            "temp_path=NULL, file_id=NULL, outcome=NULL, error=?",
            (attempt, outcome), "id=?", (dub_id,), before=_hand_over(f"id=? AND status {marks}", (dub_id, *steps)))

    async def release_to_ready(self, dub_ids: list[int]) -> None:
        """The daemon lost the job: the voice-overs are offered to it again."""
        for dub_id in dub_ids:
            await self._move(lifecycle.UNSEND, "job_id=NULL", (), "id=?", (dub_id,))

    async def reset(self, dub_id: int, reason: str | None) -> bool:
        """Back to the start, from the main source: the track belongs to a reference that is gone (`reason`), or the user
        asked to try again what failed (no reason). What the row owned goes to the cleanup journal in the same
        transaction."""
        marks, steps = _in(lifecycle.RESTART.sources)
        return await self._move(
            lifecycle.RESTART, "attempt=0, job_id=NULL, submit_key=NULL, out_dir=NULL, ref_uid=NULL, ref_path=NULL, aligned_to=NULL, "
            "ref_rev=NULL, "
            "aligned_path=NULL, output_path=NULL, move_from=NULL, temp_path=NULL, file_id=NULL, outcome=NULL, error=?",
            (reason,), "id=?", (dub_id,), before=_hand_over(f"id=? AND status {marks}", (dub_id, *steps)))

    async def follow_reference(self, dub_id: int, ref_uid: str, ref_path: str, ref_rev: int | None = None) -> None:
        """The reference is a better version of the same video (the same identity): the track stays valid and follows it."""
        await self._db.execute("UPDATE voice_dubs SET ref_uid=?, ref_path=?, ref_rev=?, updated_at=? WHERE id=?",
                               (ref_uid, ref_path, ref_rev, _now(), dub_id))

    async def mark_packed(self, dub_ids: list[int], container: str, aligned_to: str) -> None:
        """The tracks are inside the container of their episode now, aligned to its video: no file of their own is left
        to follow or remove; what is packed is the container's (ЭТАП_11)."""
        if dub_ids:
            marks = ",".join("?" * len(dub_ids))
            await self._db.execute(f"UPDATE voice_dubs SET output_path=NULL, ref_path=?, aligned_to=?, updated_at=? "
                                   f"WHERE id IN ({marks})", (container, aligned_to, _now(), *dub_ids))

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
        folders they wrote in are removed. A job that ended in an error stays: a voice-over of it that failed or was
        turned down still holds it, so its record and its plots stay for the owner to look into."""
        marks, kept = _in(KEPT_JOBS)
        await self._db.execute_many(_jobs(f"series_id=? AND season=? AND episode=? AND part=? AND status NOT {marks}",
                                          (series_id, season, episode, part, *kept)))

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

    async def files_of(self, series_id: int, quality: str = "dub") -> list[dict[str, Any]]:
        return await self._db.fetch_all("SELECT * FROM voice_files WHERE series_id=? AND quality=? ORDER BY id",
                                        (series_id, quality))

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
            where = "WHERE series_id=? AND source_id=? AND row_key=? AND url=? AND quality='dub'"
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
            # A cut has one compilation to be cut out of: no file, no cut (the user's retry plans it again).
            moved = cut_steps.FILE_READY if ok else cut_steps.FAIL
            conn.execute("UPDATE voice_cuts SET status=?, error=?, updated_at=? WHERE file_id=? AND status=?",
                         (moved.target.value, None if ok else error, _now(), file_id, C.DOWNLOADING.value))
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

    # --- cuts out of compilations (stage 12) ------------------------------------------------------------------------

    @staticmethod
    def _cut(row: dict | None) -> dict | None:
        if row is None:
            return None
        found = dict(row)
        found["cut"] = json.loads(found["cut"]) if found.get("cut") else None
        return found

    async def cuts_of(self, series_id: int | None = None) -> list[dict[str, Any]]:
        query, args = ("SELECT * FROM voice_cuts WHERE series_id=? ORDER BY season, episode, part", (series_id,)) \
            if series_id is not None else ("SELECT * FROM voice_cuts ORDER BY series_id, season, episode, part", ())
        return [self._cut(row) for row in await self._db.fetch_all(query, args)]

    async def cut(self, cut_id: int) -> dict[str, Any] | None:
        return self._cut(await self._db.fetch_one("SELECT * FROM voice_cuts WHERE id=?", (cut_id,)))

    async def cuts_by(self, column: str, value: Any) -> list[dict[str, Any]]:
        assert column in ("job_id", "submit_key", "file_id")
        return [self._cut(row) for row in await self._db.fetch_all(f"SELECT * FROM voice_cuts WHERE {column}=? ORDER BY id",
                                                                  (value,))]

    async def plan_cut(self, series_id: int, season: int, episode: int, part: int, cut: dict[str, Any], ref_uid: str,
                       admitted: bool) -> bool:
        """The slot is to be cut out of `cut` by the single video of `ref_uid`. A cut of the slot for another compilation or
        another REF 2 lets go of what it owned (to the journal) and the new one waits; the same one stays as it is (and is
        admitted when `admitted`). True when a new cut was recorded."""
        def work(conn: sqlite3.Connection) -> bool:
            conn.execute("BEGIN IMMEDIATE")
            where, args = "series_id=? AND season=? AND episode=? AND part=?", (series_id, season, episode, part)
            found = conn.execute(f"SELECT * FROM voice_cuts WHERE {where}", args).fetchone()
            if found is not None and found["cut_key"] == cut["key"] and found["ref_uid"] == ref_uid \
                    and found["status"] != C.OFFERED:
                if admitted and not found["admitted"]:
                    conn.execute("UPDATE voice_cuts SET admitted=1, updated_at=? WHERE id=?", (_now(), found["id"]))
                return False
            if found is not None:
                _cut_hand_over(conn, where, args)
                conn.execute(f"DELETE FROM voice_cuts WHERE {where}", args)
            conn.execute("INSERT INTO voice_cuts (series_id, season, episode, part, cut_key, cut, ref_uid, status, admitted,"
                         " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (series_id, season, episode, part, cut["key"], json.dumps(cut, ensure_ascii=False), ref_uid,
                          C.PLANNED.value, int(admitted), _now()))
            return True
        recorded = await self._db.transaction(work)
        await self.release_idle_files()
        return recorded

    async def drop_cuts(self, series_id: int, keep: Collection[tuple[int, int, int]]) -> list[dict[str, Any]]:
        """The cuts of the series whose slot is not in `keep` go, with what they own; returns those that went."""
        def work(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            conn.execute("BEGIN IMMEDIATE")
            gone = [dict(row) for row in conn.execute("SELECT * FROM voice_cuts WHERE series_id=?", (series_id,))
                    if (row["season"], row["episode"], row["part"]) not in keep]
            for each in gone:
                _cut_hand_over(conn, "id=?", (each["id"],))
                conn.execute("DELETE FROM voice_cuts WHERE id=?", (each["id"],))
            return gone, _release_idle(conn)
        gone, released = await self._db.transaction(work)
        self._cancel_transfers(released)
        return gone

    async def admit_cuts(self, series_id: int) -> None:
        marks, ends = _in(cut_steps.FINAL)
        await self._db.execute(f"UPDATE voice_cuts SET admitted=1, updated_at=? WHERE series_id=? AND admitted=0 "
                               f"AND status NOT {marks}", (_now(), series_id, *ends))

    async def cut_join_file(self, cut_id: int, series_id: int, source_id: int, row_key: str, url: str,
                            path_of: Callable[[int], str]) -> dict[str, Any] | None:
        """The planned cut takes the file of its compilation (best quality): the file is made if there is none and claimed
        for download when nobody has it; the cut is downloading, or ready when the file is on disk already."""
        def work(conn: sqlite3.Connection) -> dict[str, Any] | None:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM voice_cuts WHERE id=? AND status=? AND admitted=1",
                            (cut_id, C.PLANNED.value)).fetchone() is None:
                return None
            where = "WHERE series_id=? AND source_id=? AND row_key=? AND url=? AND quality='best'"
            found = conn.execute(f"SELECT * FROM voice_files {where}", (series_id, source_id, row_key, url)).fetchone()
            if found is None:
                conn.execute("INSERT INTO voice_files (series_id, source_id, row_key, url, quality, status, updated_at) "
                             "VALUES (?,?,?,?,'best',?,?)", (series_id, source_id, row_key, url, S.PENDING.value, _now()))
                found = conn.execute(f"SELECT * FROM voice_files {where}", (series_id, source_id, row_key, url)).fetchone()
            file = dict(found)
            ready = file["status"] == S.READY.value
            moved = cut_steps.FILE_READY if ready else cut_steps.FETCH
            conn.execute("UPDATE voice_cuts SET status=?, file_id=?, error=NULL, updated_at=? WHERE id=?",
                         (moved.target.value, file["id"], _now(), cut_id))
            file["claimed"] = file["status"] in (S.PENDING.value, S.FAILED.value)
            if file["claimed"]:
                file["temp_path"] = file["temp_path"] or path_of(file["id"])
                conn.execute("UPDATE voice_files SET status=?, attempt=attempt+1, error=NULL, temp_path=?, "
                             "updated_at=? WHERE id=?", (S.DOWNLOADING.value, file["temp_path"], _now(), file["id"]))
                file["status"] = S.DOWNLOADING.value
            return file
        return await self._db.transaction(work)

    async def cut_step(self, cut_id: int, transition: CutTransition, **fields: Any) -> bool:
        """Move a cut by a declared transition, writing `fields`; False when it was not in one of its sources."""
        names = sorted(fields)
        marks, sources = _in(transition.sources)
        sets = ", ".join([*(f"{name}=?" for name in names), "status=?", "updated_at=?"])
        return bool(await self._db.execute(
            f"UPDATE voice_cuts SET {sets} WHERE id=? AND status {marks}",
            (*(fields[name] for name in names), transition.target.value, _now(), cut_id, *sources)))

    async def attach_cut_job(self, key: str, job_id: str) -> bool:
        attach = cut_steps.ATTACH
        marks, sources = _in(attach.sources)
        return bool(await self._db.execute(f"UPDATE voice_cuts SET job_id=?, status=?, updated_at=? "
                                           f"WHERE submit_key=? AND job_id IS NULL AND status {marks}",
                                           (job_id, attach.target.value, _now(), key, *sources)))

    async def release_cuts(self) -> list[dict[str, Any]]:
        """Files no voice-over and no cut reads go to the journal (see `release_idle_files`)."""
        return await self.release_idle_files()

    async def cut_jobs_open(self) -> list[str]:
        rows = await self._db.fetch_all("SELECT job_id FROM voice_cuts WHERE status=? AND job_id IS NOT NULL",
                                        (C.ALIGNING.value,))
        return [row["job_id"] for row in rows]

    async def cut_submissions_open(self) -> list[str]:
        rows = await self._db.fetch_all("SELECT submit_key FROM voice_cuts WHERE status=? AND job_id IS NULL "
                                        "AND submit_key IS NOT NULL", (C.SUBMITTING.value,))
        return [row["submit_key"] for row in rows]

    async def hand_over_cut(self, cut_id: int, *, copy: bool, job: bool) -> None:
        """A cut lets go of the copy of its REF 2 (`copy`) and of its job with the folder of its video (`job`)."""
        def work(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            conn.execute("BEGIN IMMEDIATE")
            _cut_hand_over(conn, "id=?", (cut_id,), copy=copy, job=job)
            if job:
                conn.execute("UPDATE voice_cuts SET job_id=NULL, out_path=NULL WHERE id=?", (cut_id,))
            if copy and job:
                conn.execute("UPDATE voice_cuts SET work_dir=NULL WHERE id=?", (cut_id,))
            return _release_idle(conn)
        self._cancel_transfers(await self._db.transaction(work))

    async def set_file_work_dir(self, file_id: int, work_dir: str) -> None:
        await self._db.execute("UPDATE voice_files SET work_dir=? WHERE id=? AND work_dir IS NULL", (work_dir, file_id))
