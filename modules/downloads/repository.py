"""The SQL of downloads: the table download_tasks and the columns media_items.status, final_filename, rename_to and file_rev (column
ownership: the rows and plan_status are scan's, slicing_* are slicing's, read only here).

The state of a row of the plan that downloads owns is two columns that say one thing. A row is READY exactly when it holds
a file name (status 'completed' and final_filename set); otherwise it is pending, downloading (a task has it) or in error.
`register_file` and `release_file` are the only writes that move a row between the two; nothing in this module leaves a row
ready without a name or with a name and not ready.

A ready row whose file is on its way to another name carries that name in `rename_to` from before the file moves until the
name is recorded: the row holds both names for the others (neither is free to take), and a stop at any step leaves the
obligation, which the disk then tells how to finish.

A task is a work order for one row. What it holds of the plan is a snapshot taken when it begins (`claim`: the address of
the video) or a projection kept current (`retarget_task`: where the file will lie); the plan stays the truth, and where the
file takes its name the plan is read again (`begin_publish`).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable

from core.db import Database

_IN_PLAN = "('in_plan_single', 'in_plan_compilation')"

# A task holds its place in every one of these states; in the first two a worker is at it, in the last one the file is
# taking its name. Every query that means "a task that is alive" says it with these, not with a list of its own.
RUNNING_STATES = ("downloading", "processing")
LIVE_STATES = ("pending", *RUNNING_STATES, "publishing")

# A row that is a whole single episode: not a compilation, not sliced. Only such rows are downloads' to release or remove.
_SINGLE = "slicing_status='none' AND (episode_end IS NULL OR episode_end <= episode_start)"
# The columns of a task that the ones who stop it and clear after it need.
_TASK = "id, task_key, series_id, save_path, work_path, status"

# How a publication may begin, as `begin_publish` says it.
PUBLISH_OK, PUBLISH_GONE, PUBLISH_STALE, PUBLISH_TAKEN = "ok", "gone", "stale", "taken"


def _marks(states: Iterable[str]) -> str:
    return "(" + ", ".join(f"'{each}'" for each in states) + ")"


_LIVE, _RUNNING = _marks(LIVE_STATES), _marks(RUNNING_STATES)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


class DownloadsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- the queue ----------------------------------------------------------------

    async def active_tasks(self) -> list[dict]:
        """The live tasks (pending, downloading, processing, publishing): the queue of the window and the snapshot of scan."""
        return await self._db.fetch_all(
            "SELECT * FROM download_tasks WHERE task_type='vk_video' AND "
            f"status IN {_LIVE} ORDER BY created_at")

    async def next_pending(self, limit: int) -> list[dict]:
        """The tasks to take next: only those whose row is in the plan."""
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

    async def replace_or_create_task(self, unique_id: str, series_id: int, video_url: str, save_path: str,
                                     work_path: str) -> bool:
        """A task for a row that has none alive; the failed task of the row is replaced (a scan retries, Р-13)."""
        if await self.task_exists_active(unique_id):
            return False
        await self._db.execute(
            "DELETE FROM download_tasks WHERE task_key=? AND "
            "task_type='vk_video' AND status='error'", (unique_id,))
        await self._db.execute(
            "INSERT INTO download_tasks (task_key, series_id, video_url, "
            "save_path, work_path, status, attempts, created_at, updated_at, "
            "task_type, progress, dlspeed, eta) VALUES "
            "(?, ?, ?, ?, ?, 'pending', 0, ?, ?, 'vk_video', 0, 0, 0)",
            (unique_id, series_id, video_url, save_path, work_path, _now(), _now()))
        return True

    async def retarget_task(self, unique_id: str, save_path: str) -> None:
        """Where the file of a live task will lie, as the plan says now (the projection; the file is placed by the plan
        read again when it takes its name)."""
        await self._db.execute(
            "UPDATE download_tasks SET save_path=? WHERE task_key=? AND task_type='vk_video' AND save_path != ? "
            "AND status IN ('pending', 'downloading', 'processing')", (save_path, unique_id, save_path))

    async def claim(self, task_id: int) -> dict | None:
        """The task begins. It takes the address its row has now, and the row is marked as being fetched in the same step:
        the scan, which leaves the address of such a row alone, either wrote its address before this step or does not
        write it. The task comes back with `previous_url`, the address its work file (if one lies) was made from. None
        when the task is not waiting any more."""
        def work(conn) -> dict | None:
            before = conn.execute("SELECT video_url FROM download_tasks WHERE id=? AND status='pending'", (task_id,)).fetchone()
            if before is None:
                return None
            conn.execute(
                "UPDATE download_tasks SET status='downloading', attempts=attempts+1, updated_at=?, video_url=COALESCE("
                "(SELECT COALESCE(NULLIF(mi.download_url, ''), mi.source_url) FROM media_items mi "
                "WHERE mi.unique_id = download_tasks.task_key), video_url) WHERE id=?", (_now(), task_id))
            # The status of a row is of the file it holds: a row whose file is being downloaded again stays ready.
            conn.execute("UPDATE media_items SET status='downloading' WHERE final_filename IS NULL AND unique_id="
                         "(SELECT task_key FROM download_tasks WHERE id=?)", (task_id,))
            claimed = dict(conn.execute("SELECT * FROM download_tasks WHERE id=?", (task_id,)).fetchone())
            claimed["previous_url"] = before["video_url"]
            return claimed
        return await self._db.transaction(work)

    async def requeue_task(self, task_id: int) -> None:
        """Back to pending, attempts keep growing."""
        await self._db.execute(
            "UPDATE download_tasks SET status='pending', progress=0, "
            "dlspeed=0, eta=0, error_message=NULL, updated_at=? WHERE id=?",
            (_now(), task_id))

    async def mark_error(self, task_id: int, message: str) -> None:
        await self._db.execute(
            "UPDATE download_tasks SET status='error', error_message=?, "
            "updated_at=? WHERE id=?", (message, _now(), task_id))

    async def update_progress(self, task_id: int, data: dict) -> None:
        # The remux phase is the status 'processing': the post-processing shows its progress and time left (ffmpeg metrics).
        status = "processing" if data.get("phase") == "remux" \
            else "downloading"
        await self._db.execute(
            "UPDATE download_tasks SET status=?, progress=?, dlspeed=?, "
            "eta=?, total_size_mb=COALESCE(?, total_size_mb), updated_at=? "
            "WHERE id=?",
            (status, data.get("progress", 0), data.get("dlspeed", 0),
             data.get("eta", 0), data.get("total_size_mb"), _now(), task_id))

    async def remove_task(self, task_id: int, unique_id: str) -> None:
        """The task goes, and a row that was being fetched waits for a task again, in one step: nobody sees a row that is
        fetched by no task."""
        await self._db.execute_many([
            ("DELETE FROM download_tasks WHERE id=?", (task_id,)),
            ("UPDATE media_items SET status='pending' WHERE unique_id=? AND status='downloading'", (unique_id,))])

    async def keep_failed(self, task_id: int, unique_id: str, message: str) -> None:
        """The task could not clear after itself: it stays as the record of what is left, failed, and its row waits."""
        await self._db.execute_many([
            ("UPDATE download_tasks SET status='error', error_message=?, updated_at=? WHERE id=?", (message, _now(), task_id)),
            ("UPDATE media_items SET status='pending' WHERE unique_id=? AND status='downloading'", (unique_id,))])

    async def get_task(self, task_id: int) -> dict | None:
        return await self._db.fetch_one(
            "SELECT * FROM download_tasks WHERE id=? AND task_type='vk_video'",
            (task_id,))

    async def failed_task_of(self, unique_id: str) -> dict | None:
        return await self._db.fetch_one(
            f"SELECT {_TASK}, video_url FROM download_tasks WHERE task_key=? AND task_type='vk_video' AND status='error'", (unique_id,))

    async def live_task_slots(self, series_id: int) -> list[dict]:
        """The live tasks of the series with the episode of their row; a task whose row is gone has no episode."""
        return await self._db.fetch_all(
            "SELECT dt.id, mi.season, mi.episode_start, mi.part FROM download_tasks dt "
            "LEFT JOIN media_items mi ON mi.unique_id = dt.task_key "
            f"WHERE dt.series_id=? AND dt.task_type='vk_video' AND dt.status IN {_LIVE}", (series_id,))

    async def tasks_for_series(self, series_id: int) -> list[dict]:
        return await self._db.fetch_all(
            f"SELECT {_TASK} FROM download_tasks WHERE series_id=? AND task_type='vk_video'", (series_id,))

    async def clearable_tasks(self) -> list[dict]:
        """The tasks the clearing of the queue removes (/downloads/queue/clear): waiting and failed ones; a download under
        way and a file that is taking its name stay."""
        return await self._db.fetch_all(
            f"SELECT {_TASK} FROM download_tasks WHERE task_type='vk_video' AND status IN ('pending', 'error')")

    async def unneeded_waiting_tasks(self, series_id: int) -> list[dict]:
        """Waiting and failed tasks whose row needs no file: it is out of the plan or gone (nobody retries them), or it is
        ready already (its file was adopted) and the task was not asked to replace that file."""
        return await self._db.fetch_all(
            f"SELECT {_TASK} FROM download_tasks WHERE series_id=? AND "
            "task_type='vk_video' AND status IN ('pending', 'error') AND NOT EXISTS "
            "(SELECT 1 FROM media_items mi WHERE mi.unique_id = download_tasks.task_key "
            f"AND mi.plan_status IN {_IN_PLAN} AND (mi.status != 'completed' OR download_tasks.replace_file=1))", (series_id,))

    async def active_tasks_outside_plan(self, series_id: int) -> list[dict]:
        """Downloads under way whose row is no longer in the plan: the plan does not want that video any more."""
        return await self._db.fetch_all(
            "SELECT dt.id, dt.task_key, dt.series_id, dt.save_path, dt.work_path, dt.status FROM download_tasks dt "
            "JOIN media_items mi ON mi.unique_id = dt.task_key "
            f"WHERE dt.series_id=? AND dt.task_type='vk_video' AND dt.status IN {_RUNNING} "
            f"AND mi.plan_status NOT IN {_IN_PLAN}", (series_id,))

    async def tasks_without_row(self, series_id: int) -> list[dict]:
        """Tasks of any state whose media_items row is gone (scan deletes the rows of a slot that lost)."""
        return await self._db.fetch_all(
            f"SELECT {_TASK} FROM download_tasks WHERE series_id=? AND task_type='vk_video' "
            "AND NOT EXISTS (SELECT 1 FROM media_items mi WHERE mi.unique_id = download_tasks.task_key)",
            (series_id,))

    async def requeue_interrupted(self) -> int:
        """At the start the downloads that were cut short go back to the queue; a failed task is not healed by a restart (Р-13)."""
        return await self._db.execute(
            "UPDATE download_tasks SET status='pending' WHERE "
            f"task_type='vk_video' AND status IN {_RUNNING}")

    async def tasks_without_series(self, series_ids: Iterable[int]) -> list[dict]:
        """Tasks of a series that does not exist any more: the record of what its deletion could not remove."""
        ids = list(series_ids)
        marks = ", ".join("?" * len(ids)) or "NULL"
        return await self._db.fetch_all(
            f"SELECT {_TASK} FROM download_tasks WHERE task_type='vk_video' AND series_id NOT IN ({marks})", tuple(ids))

    async def set_pending(self, unique_id: str, relative: str, cut: str | None) -> None:
        """The row holds a video for the next build of its container (stage 12): written before the file is moved in, so
        that the name always has an owner."""
        await self._db.execute("UPDATE media_items SET pending_video=?, pending_cut=? WHERE unique_id=?",
                               (relative, cut, unique_id))

    async def clear_pending(self, unique_id: str, relative: str) -> bool:
        return bool(await self._db.execute("UPDATE media_items SET pending_video=NULL, pending_cut=NULL "
                                           "WHERE unique_id=? AND pending_video=?", (unique_id, relative)))

    async def pending_paths(self, series_id: int) -> set[str]:
        rows = await self._db.fetch_all("SELECT pending_video FROM media_items WHERE series_id=? AND pending_video IS NOT NULL",
                                        (series_id,))
        return {each["pending_video"] for each in rows}

    async def work_owners(self, series_id: int) -> tuple[set[str], set[str]]:
        """(keys of the rows of the series, keys of its tasks): a work file of a row that has no task has no owner."""
        rows = await self._db.fetch_all("SELECT unique_id FROM media_items WHERE series_id=?", (series_id,))
        tasks = await self._db.fetch_all("SELECT task_key FROM download_tasks WHERE series_id=? AND task_type='vk_video'",
                                         (series_id,))
        return {each["unique_id"] for each in rows}, {each["task_key"] for each in tasks}

    async def task_paths(self, series_id: int) -> dict[str, str]:
        """{path of the file: key of the row} of the live tasks that are to leave a file of the series."""
        rows = await self._db.fetch_all(
            "SELECT save_path, task_key FROM download_tasks WHERE series_id=? AND task_type='vk_video' "
            f"AND status IN {_LIVE}", (series_id,))
        return {each["save_path"]: each["task_key"] for each in rows}

    # --- a file takes its name -----------------------------------------------------

    async def begin_publish(self, task_id: int, target: str, relative: str, *, foreign: bool = False,
                            identity: tuple[int, int] | None = None) -> str:
        """The file is whole and is about to take the name `relative` (the path `target`). The plan is read again in the
        same step the task says `publishing`, and the task goes on only if the file is still what the plan wants: the row is
        in the plan and not switched off ('gone' otherwise), it still shows the video the file was fetched from ('stale'),
        and no other row the plan wants holds the name ('taken': the task waits for the name, `publishing`); a row of another
        series that holds the same file (`foreign`) takes it the same. The task is recorded as `publishing` with the path and the
        identity of its whole work file (size, file number), so that a stop at any point leaves a record from which the step is
        finished and from which a file under the name is known to be this one."""
        def work(conn) -> str:
            task = conn.execute("SELECT * FROM download_tasks WHERE id=?", (task_id,)).fetchone()
            row = conn.execute("SELECT series_id, plan_status, is_ignored_by_user, download_url, source_url FROM media_items "
                               "WHERE unique_id=?", (task["task_key"],)).fetchone()
            if row is None or row["plan_status"] not in ("in_plan_single", "in_plan_compilation") or row["is_ignored_by_user"]:
                return PUBLISH_GONE
            if (row["download_url"] or row["source_url"]) != task["video_url"]:
                return PUBLISH_STALE
            taken = conn.execute(
                "SELECT 1 FROM media_items WHERE series_id=? AND (final_filename=? OR rename_to=?) AND unique_id != ? "
                f"AND is_ignored_by_user=0 AND plan_status IN {_IN_PLAN}", (row["series_id"], relative, relative, task["task_key"])).fetchone()
            size, ino = identity if identity is not None else (None, None)
            conn.execute("UPDATE download_tasks SET status='publishing', save_path=?, progress=100, dlspeed=0, eta=0, updated_at=?, "
                         "work_size=COALESCE(?, work_size), work_ino=COALESCE(?, work_ino) WHERE id=?",
                         (target, _now(), size, ino, task_id))
            return PUBLISH_TAKEN if taken is not None or foreign else PUBLISH_OK
        return await self._db.transaction(work)

    async def finish_publish(self, task_id: int, unique_id: str, relative: str, *, leftovers: bool = False) -> None:
        """The file has its name: the row holds it, every other row of the series that held that name lets it go (the file
        is another video now: they wait for a file again), and the task is done, in one step. With `leftovers` (pieces of the
        download that could not be removed) the task stays, failed, as the record of what is left, until they are gone."""
        done = ("UPDATE download_tasks SET status='error', error_message=?, updated_at=? WHERE id=?",
                ("рабочие файлы задачи не удалены", _now(), task_id)) if leftovers else (
                "DELETE FROM download_tasks WHERE id=?", (task_id,))
        await self._db.execute_many([
            ("UPDATE media_items SET status='pending', final_filename=NULL, rename_to=NULL WHERE final_filename=? AND unique_id != ? "
             "AND series_id=(SELECT series_id FROM media_items WHERE unique_id=?)", (relative, unique_id, unique_id)),
            # A published download is a new file of the row: what was judged against its old file is judged again.
            ("UPDATE media_items SET status='completed', final_filename=?, rename_to=NULL, file_rev=file_rev+1 "
             "WHERE unique_id=?", (relative, unique_id)),
            done])

    async def forget_failures(self, series_id: int) -> int:
        """The rows whose download failed are videos to fetch again: the choice of the reference takes them once more."""
        return await self._db.execute(
            "UPDATE media_items SET status='pending' WHERE series_id=? AND status='error'", (series_id,))

    async def fail_publish(self, task_id: int, unique_id: str, message: str) -> None:
        """The file could not be put under its name and certainly was not: the task fails, its work file stays."""
        await self.keep_failed(task_id, unique_id, message)
        await self.fail_item(unique_id)

    async def publishing_tasks(self, series_id: int | None = None) -> list[dict]:
        """Tasks whose file was taking its name when the process stopped, or whose outcome is not known."""
        where, args = ("AND series_id=? ", (series_id,)) if series_id is not None else ("", ())
        return await self._db.fetch_all(
            f"SELECT * FROM download_tasks WHERE task_type='vk_video' AND status='publishing' {where}ORDER BY id", args)

    # --- the row: ready or not -----------------------------------------------------

    async def item(self, unique_id: str) -> dict | None:
        return await self._db.fetch_one("SELECT * FROM media_items WHERE unique_id=?", (unique_id,))

    async def series_of_item(self, unique_id: str) -> int | None:
        row = await self._db.fetch_one(
            "SELECT series_id FROM media_items WHERE unique_id=?", (unique_id,))
        return row["series_id"] if row else None

    async def source_of_item(self, unique_id: str) -> int | None:
        """The source of the series a candidate was found by (scan's column, read only); NULL on earlier rows."""
        row = await self._db.fetch_one(
            "SELECT source_id FROM media_items WHERE unique_id=?", (unique_id,))
        return row["source_id"] if row else None

    async def register_file(self, unique_id: str, filename: str) -> None:
        """The row holds a file that lies in the library: it is ready."""
        await self._db.execute(
            "UPDATE media_items SET status='completed', final_filename=?, rename_to=NULL, file_rev=file_rev+1 "
            "WHERE unique_id=?", (filename, unique_id))

    async def release_file(self, unique_id: str) -> None:
        """The row holds no file any more (gone, removed, displaced, overwritten): it is not ready, it waits for one."""
        await self._db.execute(
            "UPDATE media_items SET status='pending', final_filename=NULL, rename_to=NULL WHERE unique_id=?", (unique_id,))

    async def release_unbacked(self, series_id: int) -> int:
        """A whole single episode that is ready and holds no name is not ready (earlier versions let a displaced row stay
        so): it waits for a file. Compilations and sliced files are not looked at here."""
        return await self._db.execute(
            f"UPDATE media_items SET status='pending' WHERE series_id=? AND status='completed' AND final_filename IS NULL "
            f"AND {_SINGLE}", (series_id,))

    async def set_item_filename(self, unique_id: str, filename: str) -> None:
        """Slicing reports the name of a file it made or took over."""
        await self._db.execute(
            "UPDATE media_items SET final_filename=?, rename_to=NULL WHERE unique_id=?",
            (filename, unique_id))

    # --- a ready file on its way to another name ------------------------------------

    async def record_renames(self, series_id: int, names: dict[str, str]) -> int:
        """The names the plan gives the ready files of the series now: each ready row whose name differs carries the new one in
        `rename_to`, each whose name is the same carries none (an earlier wish is cancelled). Written before any file moves.
        Returns how many rows owe a move."""
        def work(conn) -> int:
            owed = 0
            rows = conn.execute("SELECT unique_id, final_filename, rename_to FROM media_items WHERE series_id=? "
                                "AND status='completed' AND final_filename IS NOT NULL", (series_id,)).fetchall()
            for row in rows:
                if row["unique_id"] not in names:
                    continue
                wanted = None if names[row["unique_id"]] == row["final_filename"] else names[row["unique_id"]]
                if wanted != row["rename_to"]:
                    conn.execute("UPDATE media_items SET rename_to=? WHERE unique_id=?", (wanted, row["unique_id"]))
                owed += wanted is not None
            return owed
        return await self._db.transaction(work)

    async def pending_renames(self, series_id: int) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT unique_id, final_filename, rename_to FROM media_items WHERE series_id=? AND rename_to IS NOT NULL "
            "AND status='completed' AND final_filename IS NOT NULL ORDER BY unique_id", (series_id,))

    async def finish_rename(self, unique_id: str, filename: str) -> bool:
        """The file lies under its new name: the row holds it and owes nothing."""
        return bool(await self._db.execute(
            "UPDATE media_items SET final_filename=?, rename_to=NULL WHERE unique_id=? AND rename_to=?",
            (filename, unique_id, filename)))

    async def drop_rename(self, unique_id: str) -> None:
        """The file cannot go to the name (it is held by what is not this row's): the row keeps its name."""
        await self._db.execute("UPDATE media_items SET rename_to=NULL WHERE unique_id=?", (unique_id,))

    async def fail_item(self, unique_id: str) -> None:
        """The download of the video of a row failed: a row with no file waits as failed (the choice of the reference
        passes it over); a row that holds its file keeps it and stays ready, the failed task being the record."""
        await self._db.execute("UPDATE media_items SET status='error' WHERE unique_id=? AND final_filename IS NULL",
                               (unique_id,))

    async def create_replacing_task(self, unique_id: str, series_id: int, video_url: str, save_path: str,
                                    work_path: str) -> bool:
        """A task that downloads the video of a ready row again; its file takes the name of the row when it is whole
        (publication.py). False when the row has a live task already."""
        if not await self.replace_or_create_task(unique_id, series_id, video_url, save_path, work_path):
            return False
        await self._db.execute("UPDATE download_tasks SET replace_file=1 WHERE task_key=? AND task_type='vk_video' "
                               "AND status='pending'", (unique_id,))
        return True

    async def set_item_status(self, unique_id: str, status: str) -> None:
        await self._db.execute(
            "UPDATE media_items SET status=? WHERE unique_id=?",
            (status, unique_id))

    async def planned_pending_items(self, series_id: int) -> list[dict]:
        """The rows the plan wants that wait for a file: pending, and failed ones (a scan retries them, Р-13)."""
        return await self._db.fetch_all(
            "SELECT * FROM media_items WHERE series_id=? AND "
            f"plan_status IN {_IN_PLAN} AND status IN ('pending', 'error') "
            "AND is_ignored_by_user=0", (series_id,))

    async def reference_items(self, series_id: int) -> list[dict]:
        """Single-episode rows of the plan in any download state: the reference episodes."""
        return await self._db.fetch_all(
            "SELECT unique_id, season, episode_start, part, source_url, status, final_filename, superseded_by, source_id, "
            "row_key, file_rev, want_cut, cut, pending_video, pending_cut FROM media_items "
            "WHERE series_id=? AND plan_status='in_plan_single' "
            "AND is_ignored_by_user=0 ORDER BY season, episode_start", (series_id,))

    async def slot_files(self, series_id: int) -> list[dict]:
        """The single-episode rows of the series that hold a file, with their place in the plan: what packing reads."""
        return await self._db.fetch_all(
            "SELECT unique_id, season, episode_start, part, plan_status, status, final_filename, superseded_by, source_id, "
            "row_key, is_ignored_by_user, pending_video, pending_cut, cut "
            f"FROM media_items WHERE series_id=? AND final_filename IS NOT NULL AND {_SINGLE}",
            (series_id,))

    async def take_over_name(self, winner: str, filename: str, losers: list[str], video: dict | None = None) -> None:
        """The winner holds the container under `filename`; the rows it displaced hold nothing any more: one transaction, so
        a name is never held by two rows nor by none in between (L2, L5). `video`: the container took the video the row
        held for it ({pending, cut}): the row says what its video is cut of now and holds no other."""
        statements = [("UPDATE media_items SET status='pending', final_filename=NULL, rename_to=NULL WHERE unique_id=?",
                       (uid,)) for uid in losers]
        statements.append(("UPDATE media_items SET status='completed', final_filename=?, rename_to=NULL WHERE unique_id=?",
                           (filename, winner)))
        if video is not None:
            statements.append(("UPDATE media_items SET cut=?, pending_video=NULL, pending_cut=NULL "
                               "WHERE unique_id=? AND pending_video=?",
                               (json.dumps(video["cut"], ensure_ascii=False) if video.get("cut") else None, winner,
                                video["pending"])))
        await self._db.execute_many(statements)

    # --- the journal of the publications of containers (pack_publications, stage 11) ----------------------------------

    _PUBLICATION_LISTS = ("signature", "losers", "leftovers", "dub_ids")

    async def publication_begin(self, record: dict) -> int:
        """What a publication of a container must do, written before its build: kept until the publication is whole."""
        values = {**record, **{name: json.dumps(record[name], ensure_ascii=False) for name in self._PUBLICATION_LISTS},
                  "state": "building", "created_at": datetime.now(timezone.utc).isoformat()}
        values["video"] = json.dumps(record["video"], ensure_ascii=False) if record.get("video") else None
        names = ("series_id", "unique_id", "publication", "name", "staging", "identity", *self._PUBLICATION_LISTS, "state",
                 "created_at", "video")
        return await self._db.insert(
            f"INSERT INTO pack_publications ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
            tuple(values[name] for name in names))

    async def publication_placed(self, publication_id: int) -> None:
        await self._db.execute("UPDATE pack_publications SET state='placed' WHERE id=?", (publication_id,))

    async def publication_end(self, publication_id: int) -> None:
        await self._db.execute("DELETE FROM pack_publications WHERE id=?", (publication_id,))

    async def publications(self, series_id: int) -> list[dict]:
        rows = await self._db.fetch_all("SELECT * FROM pack_publications WHERE series_id=? ORDER BY id", (series_id,))
        return [{**row, **{name: json.loads(row[name]) for name in self._PUBLICATION_LISTS},
                 "video": json.loads(row["video"]) if row.get("video") else None} for row in rows]

    async def all_publications(self) -> list[dict]:
        """Every publication a stop cut short, of every series: what the window shows of them at the start."""
        rows = await self._db.fetch_all("SELECT * FROM pack_publications ORDER BY series_id, id")
        return [{**row, **{name: json.loads(row[name]) for name in self._PUBLICATION_LISTS}} for row in rows]

    async def drop_publications(self, series_id: int) -> None:
        """A deleted series takes its journal with it; its files are not removed (deleting a series deletes no files)."""
        await self._db.execute("DELETE FROM pack_publications WHERE series_id=?", (series_id,))

    async def completed_items(self, series_id: int) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM media_items WHERE series_id=? AND "
            "status='completed'", (series_id,))

    async def unwanted_files(self, series_id: int) -> list[dict]:
        """Whole files of single episodes that the configuration took out of the plan: the user switched the episode or
        its season off, or the source (or its profile) the row came from is gone. A compilation and a sliced file are the
        business of the slicing and are not named here."""
        return await self._db.fetch_all(
            f"SELECT unique_id, final_filename FROM media_items WHERE series_id=? AND final_filename IS NOT NULL AND {_SINGLE} "
            "AND (is_ignored_by_user=1 OR plan_status='removed')", (series_id,))

    async def wanted_holder(self, series_id: int, filename: str, *, except_uid: str) -> bool:
        """Whether a row the plan wants holds a file of this name: then the file is not an unwanted one."""
        row = await self._db.fetch_one(
            "SELECT 1 FROM media_items WHERE series_id=? AND (final_filename=? OR rename_to=?) AND unique_id != ? "
            f"AND is_ignored_by_user=0 AND plan_status IN {_IN_PLAN}", (series_id, filename, filename, except_uid))
        return row is not None

    async def superseded_files(self, series_id: int) -> list[dict]:
        """Downloaded rows beaten by the plan's winner of their slot, with the winner's file. Only a winner that is itself
        downloaded qualifies."""
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

    async def holders_of_file(self, series_id: int, filename: str, *, except_uid: str) -> list[dict]:
        """The other rows of the series that hold a file of this name or are moving a file to it. A file one row holds is not
        an orphan that another row may take."""
        return await self._db.fetch_all(
            "SELECT unique_id, final_filename, rename_to FROM media_items WHERE series_id=? AND (final_filename=? OR rename_to=?) "
            "AND unique_id != ?", (series_id, filename, filename, except_uid))

    async def downloaded_count(self, series_id: int) -> int:
        """The counter of what is downloaded for a VK series, as scan.media.downloaded_counts has it (a name held), for the
        reactive SSE (Д1). Specials (season 0) are not counted: the total has none (TMDB)."""
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM media_items WHERE series_id=? AND "
            "final_filename IS NOT NULL AND (season IS NULL OR season != 0)",
            (series_id,))
        return row["n"] if row else 0

    # --- the contribution to the catalog's fold of statuses ------------------------

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
