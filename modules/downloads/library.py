"""The files of a series in the library against the rows of its plan: what ought to lie there, and what does.

The model downloads keeps (it is checked by `tests/model_support.check` over every operation of the plan and every
failure of the work):

  L1 a name a row holds is a file with the video of that row; L2 a name is held by one row;
  L3 a whole file in the library is held by a row; L4 a work file has a task;
  L5 a row is ready exactly when it holds a name; L6 a task is never left half-way;
  L7 the file of a row the configuration took out is gone; L8 a row the plan wants gets its file, under the name its number gives;
  L9 a path in the library is held by one row of all the series that share its folder; L10 a ready file is where its row says, or
  the row says (`rename_to`) where it is going; L11 what a task could not remove stays recorded until it is removed.

Every step here is a function of what the database and the disk hold now, so it can be taken at any time and any number of
times: an event is a reason to look, never the only carrier of an obligation, and a stop loses nothing.
"""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable

from core import storage

from . import ytdlp
from .repository import DownloadsRepository

# (series, row) -> the name of the file of the row under the rules of naming, relative to the folder of the series.
Naming = Callable[[dict, dict], Awaitable[str]]
# series -> the other series of the library whose folder is this one, holds it or lies inside it: they may hold the same path.
Neighbours = Callable[[dict], Awaitable[list[dict]]]

# The outcomes of one step of a rename.
DONE, DEFERRED, ABANDONED, UNKNOWN = "done", "deferred", "abandoned", "unknown"
# A file that steps aside while the names of a ring of files change owners: not a work file, no row's key is its first word.
ASIDE_PREFIX = ".renaming."


async def _alone(_series: dict) -> list[dict]:
    return []


def _inside(base: str, path: str) -> bool:
    root = os.path.realpath(base)
    return os.path.commonpath([root, os.path.realpath(path)]) == root


class Library:
    def __init__(self, repo: DownloadsRepository, naming: Naming, log, neighbours: Neighbours = _alone) -> None:
        self._repo, self._naming, self._log, self._neighbours = repo, naming, log, neighbours
        self._guards: dict[str, asyncio.Lock] = {}

    # --- where things lie ----------------------------------------------------------

    async def relative(self, series: dict, item: dict) -> str:
        """The name of the file of a row as the plan numbers it now."""
        return await self._naming(series, item)

    async def target(self, series: dict, item: dict) -> str:
        return os.path.join(series["save_path"], await self._naming(series, item))

    def guard(self, path: str) -> asyncio.Lock:
        """One decision at a time about the files of one folder, whichever series they belong to: that a path is free and the
        act of taking it are not apart."""
        return self._guards.setdefault(os.path.normpath(os.path.dirname(path)), asyncio.Lock())

    async def foreign_holder(self, series: dict, filename: str, *, others: list[dict] | None = None) -> dict | None:
        """A row, or a live task, of another series that holds the file the name means in the folder of this series, or is to
        leave one there: the path is not this series' to take, to adopt, to replace or to remove."""
        others = await self._neighbours(series) if others is None else others
        path = os.path.normpath(os.path.join(series["save_path"], filename))
        for other in others:
            base = os.path.normpath(other["save_path"])
            if os.path.commonpath([base, path]) != base:
                continue
            held = await self._repo.holders_of_file(other["id"], os.path.relpath(path, base).replace(os.sep, "/"), except_uid="")
            if held:
                return {"series_id": other["id"], "unique_id": held[0]["unique_id"]}
            writing = await self._repo.task_paths(other["id"])
            if any(os.path.normpath(each) == path for each in writing):
                return {"series_id": other["id"], "unique_id": writing.get(path)}
        return None

    async def work_identity(self, task: dict) -> tuple[int, int] | None:
        """(size, file number) of the work file of a task, or None when there is none: what the file is, to be known again
        under another name."""
        try:
            info = await storage.call(os.stat, self.work_path(task))
        except (OSError, storage.StorageTimeout):
            return None
        return info.st_size, info.st_ino

    async def is_work_file(self, path: str, task: dict) -> bool:
        """Whether the file at `path` is the file the task had whole (by the identity recorded when its publication began). A
        task that recorded none cannot tell, and nothing is assumed."""
        size, ino = task.get("work_size"), task.get("work_ino")
        if size is None:
            return False
        try:
            info = await storage.call(os.stat, path)
        except (OSError, storage.StorageTimeout):
            return False
        return info.st_size == size and (not ino or not info.st_ino or info.st_ino == ino)

    @staticmethod
    def pending_name(unique_id: str) -> str:
        """The hidden name of the video a row holds for the next build of its container, in the folder of the series."""
        return f".{unique_id}.video.mkv"

    @staticmethod
    def work_path_for(series: dict, unique_id: str) -> str:
        """The hidden file a task writes into, in the folder of the series: it does not depend on the name of the video."""
        return os.path.join(series["save_path"], f".{unique_id}.mp4")

    @staticmethod
    def work_path(task: dict) -> str:
        """Where a task writes. A task made before the column existed wrote beside the name of the video."""
        if task.get("work_path"):
            return task["work_path"]
        directory, name = os.path.split(task["save_path"])
        return os.path.join(directory, f".{task['task_key']}.{name}")

    # --- the rows that wait for a file ---------------------------------------------

    async def adopt(self, series: dict) -> int:
        """A row of the plan that waits for a file, and whose file already lies under its name, is ready without a download."""
        adopted = 0
        writing = await self._repo.task_paths(series["id"])
        others = await self._neighbours(series)
        for item in await self._repo.planned_pending_items(series["id"]):
            filename = await self._naming(series, item)
            path = os.path.join(series["save_path"], filename)
            async with self.guard(path):
                if await self._repo.holders_of_file(series["id"], filename, except_uid=item["unique_id"]):
                    continue  # the file is another row's (the video this one displaces): this one is fetched, not adopted
                if writing.get(path, item["unique_id"]) != item["unique_id"]:
                    continue  # the task of another row is to leave a file there: it is that row's
                if await self.foreign_holder(series, filename, others=others):
                    continue  # a row of another series that shares the folder holds this file
                if await storage.call(os.path.exists, path):
                    await self._repo.register_file(item["unique_id"], filename)
                    self._log.info("усыновлён существующий файл: %s", filename)
                    adopted += 1
        return adopted

    async def release_lost(self, series: dict) -> int:
        """A ready row whose file is not there any more waits for a file again. A sliced compilation is the slicing's."""
        lost = 0
        for item in await self._repo.completed_items(series["id"]):
            if (item.get("slicing_status") or "none") in ("completed", "completed_with_errors"):
                continue
            name = item.get("final_filename")
            if not name or item.get("rename_to"):
                continue  # a file on its way to another name lies aside or under the new one: the move tells whether it is lost
            path = os.path.join(series["save_path"], name)
            if not await storage.call(os.path.exists, path):
                await self._repo.release_file(item["unique_id"])
                self._log.warning("файл пропал: %s — статус сброшен", path)
                lost += 1
        return lost

    async def release_unbacked(self, series: dict) -> int:
        """A whole single episode that is ready and holds no name waits for a file (L5)."""
        released = await self._repo.release_unbacked(series["id"])
        if released:
            self._log.info("серия %d: строк без файла, помеченных готовыми, возвращено в ожидание: %d", series["id"], released)
        return released

    # --- what the plan no longer wants on the disk ---------------------------------

    async def retire_superseded(self, series: dict) -> int:
        """The files of rows whose slot another ready row holds go; the winner's file must lie there, so a lone copy is never lost."""
        base, removed = series["save_path"], 0
        others = await self._neighbours(series)
        for row in await self._repo.superseded_files(series["id"]):
            path = os.path.join(base, row["filename"])
            winner_path = os.path.join(base, row["winner_filename"])
            if os.path.realpath(path) == os.path.realpath(winner_path):
                continue
            if not _inside(base, path):
                self._log.warning("файл вне каталога серии не тронут: %s", path)
                continue
            async with self.guard(path):
                if not await storage.call(os.path.isfile, winner_path):
                    continue
                if await self.foreign_holder(series, row["filename"], others=others):
                    continue  # the file is held by a row of another series as well: it is not this one's to remove
                if not await self._remove(path):
                    continue
                await self._repo.release_file(row["unique_id"])
            self._log.info("вытеснен победителем слота, файл удалён: %s", row["filename"])
            removed += 1
        return removed

    async def remove_unwanted(self, series: dict) -> int:
        """What lies on the disk is what the panel configures: the file of an episode or a season the user switched off, or
        of a source or profile taken away, goes, and its row is not fetched any more. A file another row the plan wants holds
        under the same name is that row's and stays."""
        base, removed = series["save_path"], 0
        others = await self._neighbours(series)
        for row in await self._repo.unwanted_files(series["id"]):
            path = os.path.join(base, row["final_filename"])
            if not _inside(base, path):
                self._log.warning("файл вне каталога серии не тронут: %s", path)
                continue
            async with self.guard(path):
                kept = (await self._repo.wanted_holder(series["id"], row["final_filename"], except_uid=row["unique_id"])
                        or await self.foreign_holder(series, row["final_filename"], others=others))
                if not kept:
                    if await storage.call(os.path.isfile, path) and not await self._remove(path):
                        continue
                    removed += 1
                await self._repo.release_file(row["unique_id"])
        return removed

    async def _remove(self, path: str) -> bool:
        try:
            await storage.call(os.remove, path, owns=path)
        except FileNotFoundError:
            return True
        except (OSError, storage.StorageTimeout) as exc:
            self._log.warning("файл %s не удалён: %s", path, exc)
            return False
        return True

    # --- the files on their way to another name -------------------------------------

    @staticmethod
    def aside_path(base: str, name: str, unique_id: str) -> str:
        return os.path.join(os.path.dirname(os.path.join(base, name)), f"{ASIDE_PREFIX}{unique_id}")

    async def settle_names(self, series: dict) -> list[tuple[str, str]]:
        """Bring the ready files whose rows carry a `rename_to` to their names, as the disk shows the step to be: the file lies
        under the old name (it goes), aside (it goes on), or under the new one (the name is recorded). A name another file holds
        and gives up waits; a ring of files that hold each other's names is broken by letting one step aside; a name held by
        what is not going away is not taken, and the row keeps its own. Returns (key, name) of the names recorded."""
        others = await self._neighbours(series)
        recorded: list[tuple[str, str]] = []
        for _ in range(2 * len(await self._repo.pending_renames(series["id"])) + 2):
            pending = await self._repo.pending_renames(series["id"])
            if not pending:
                break
            outcomes = {each["unique_id"]: await self._settle_one(series, each, others) for each in pending}
            recorded += [(each["unique_id"], each["rename_to"]) for each in pending if outcomes[each["unique_id"]] == DONE]
            if any(outcome in (DONE, ABANDONED) for outcome in outcomes.values()):
                continue
            deferred = [each for each in pending if outcomes[each["unique_id"]] == DEFERRED]
            if not deferred or not await self._step_aside(series, deferred[0]):
                break
        return recorded

    async def _settle_one(self, series: dict, row: dict, others: list[dict]) -> str:
        base, uid, target = series["save_path"], row["unique_id"], row["rename_to"]
        old_abs, new_abs = os.path.join(base, row["final_filename"]), os.path.join(base, target)
        aside_abs = self.aside_path(base, row["final_filename"], uid)
        try:
            at_aside, at_old, at_new = [await storage.call(os.path.exists, each) for each in (aside_abs, old_abs, new_abs)]
        except (OSError, storage.StorageTimeout) as exc:
            self._log.warning("имя файла %s: диск не ответил, шаг повторится: %s", row["final_filename"], exc)
            return UNKNOWN
        async with self.guard(new_abs):
            if await self.foreign_holder(series, target, others=others):
                return await self._abandon(row, "его держит строка другого сериала")
            holders = await self._repo.holders_of_file(series["id"], target, except_uid=uid)
            if any(each["final_filename"] != target and each["unique_id"] < uid for each in holders):
                return await self._abandon(row, "в то же имя идёт файл другой строки")
            named = [each for each in holders if each["final_filename"] == target]
            leaving = [each for each in named if each["rename_to"] and each["rename_to"] != target]
            if at_aside or at_old:
                if at_new:
                    return DEFERRED if named and len(leaving) == len(named) else await self._abandon(row, "оно занято чужим файлом")
                return await self._move_to_name(row, aside_abs if at_aside else old_abs, new_abs)
            if at_new:  # moved before a stop: the file lies under its name, the name is recorded now
                return await self._record_name(row)
            if named:
                return DEFERRED if len(leaving) == len(named) else await self._abandon(row, "оно записано за другой строкой")
            return await self._record_name(row)  # no file: the name follows the plan, the look at the disk finds the file missing

    async def _move_to_name(self, row: dict, source: str, destination: str) -> str:
        def move() -> None:
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            os.rename(source, destination)

        try:
            await storage.call(move, owns=(source, destination))
        except (storage.StorageTimeout, storage.StorageBusy) as exc:
            self._log.warning("исход переноса %s не известен, его скажет диск: %s", row["final_filename"], exc)
            return UNKNOWN
        except OSError as exc:
            self._log.warning("файл %s не перенесён на имя %s: %s", row["final_filename"], row["rename_to"], exc)
            return UNKNOWN
        self._log.info("переименован: %s -> %s", row["final_filename"], row["rename_to"])
        return await self._record_name(row)

    async def _record_name(self, row: dict) -> str:
        await self._repo.finish_rename(row["unique_id"], row["rename_to"])
        return DONE

    async def _abandon(self, row: dict, why: str) -> str:
        self._log.warning("файл %s остаётся под своим именем: имя %s не отдано — %s", row["final_filename"], row["rename_to"], why)
        await self._repo.drop_rename(row["unique_id"])
        return ABANDONED

    async def _step_aside(self, series: dict, row: dict) -> bool:
        """A ring of names: one file leaves its name so that the next can take it."""
        base = series["save_path"]
        old_abs = os.path.join(base, row["final_filename"])
        aside_abs = self.aside_path(base, row["final_filename"], row["unique_id"])
        try:
            if await storage.call(os.path.exists, aside_abs) or not await storage.call(os.path.exists, old_abs):
                return False
            await storage.call(os.rename, old_abs, aside_abs, owns=(old_abs, aside_abs))
        except (OSError, storage.StorageTimeout, storage.StorageBusy) as exc:
            self._log.warning("файл %s не отошёл в сторону: %s", row["final_filename"], exc)
            return False
        return True

    # --- the work files ------------------------------------------------------------

    async def holds_work(self, task: dict, video_url: str) -> bool:
        """Whether a whole video of this address lies in the work file of the task (a download leaves its work file only
        when it is whole)."""
        return task.get("video_url") == video_url and await storage.call(os.path.isfile, self.work_path(task))

    async def clear_task_files(self, task: dict) -> bool:
        """What a task wrote goes: its work file and the pieces of the download beside it, and the pieces an earlier version
        left beside the name of the video. The file under the name of the video is never touched. True when nothing is left;
        a task that could not clear after itself stays as the record of what is left."""
        work = self.work_path(task)
        owned = (work, task["save_path"]) if task.get("save_path") else (work,)
        try:
            return bool(await storage.call(self._remove_pieces, work, task.get("save_path"),
                                           timeout=storage.REMOVE_TIMEOUT_S, owns=owned))
        except (OSError, storage.StorageTimeout) as exc:
            self._log.warning("рабочие файлы задачи %s не убраны: %s", task.get("task_key"), exc)
            return False

    @staticmethod
    def _remove_pieces(work: str, save_path: str | None) -> bool:
        """The work file with the pieces of its download (names of the key of the task, nobody else's), and the pieces an
        earlier version left beside the video: `<name>.part`, `<name>.remux.mp4`, `<name>.download.*`. Matched by strings,
        not by glob: the folders of the library carry `[tmdbid-NNNN]`, which a glob reads as a class of characters."""
        clean = Library.remove_download(work)
        paths: list[str] = []
        if save_path:
            directory, base = os.path.split(save_path)
            try:
                names = os.listdir(directory)
            except OSError:
                names = []
            paths += [os.path.join(directory, name) for name in names
                      if name in (base + ".part", base + ".remux.mp4") or name.startswith(base + ".download.")]
        for path in paths:
            if os.path.isfile(path) and not storage.remove_file(path):
                clean = False
        return clean

    @staticmethod
    def remove_download(path: str) -> bool:
        """The file of a download with the pieces of its download: names built on its own name, nobody else's. True when
        none is left."""
        clean = True
        for each in (path, *ytdlp.download_artifacts(path)):
            if os.path.isfile(each) and not storage.remove_file(each):
                clean = False
        return clean

    async def sweep_work(self, series: dict) -> int:
        """A work file of a row that has no task has no owner (a stop left it): it goes. Only a hidden file of the folder of
        the series named by the key of a row of this series is taken."""
        rows, owned = await self._repo.work_owners(series["id"])
        free = rows - owned
        if not free:
            return 0
        # The video a row holds for the next build of its container (stage 12) is that row's, task or not, and so are the
        # tracks voices aligned to it and placed beside it (named after it).
        held = tuple(os.path.splitext(os.path.normpath(os.path.join(series["save_path"], each)))[0]
                     for each in await self._repo.pending_paths(series["id"]))
        try:
            found = await storage.call(self._hidden_of, series["save_path"], free)
        except (OSError, storage.StorageTimeout) as exc:
            self._log.warning("серия %d: каталог не прочитан, рабочие файлы не проверены: %s", series["id"], exc)
            return 0
        removed = 0
        for path in found:
            if os.path.normpath(path).startswith(held):
                continue
            if await storage.discard(path):
                removed += 1
                self._log.info("убран рабочий файл без задачи: %s", path)
        return removed

    @staticmethod
    def _hidden_of(base: str, keys: set[str]) -> list[str]:
        found: list[str] = []
        for entry in os.scandir(base):
            key, dot, _ = entry.name[1:].partition(".")
            if entry.name.startswith(".") and dot and key in keys and entry.is_file():
                found.append(entry.path)
        return found
