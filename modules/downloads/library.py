"""The files of a series in the library against the rows of its plan: what ought to lie there, and what does.

The model downloads keeps (it is checked by `tests/model_support.check` over every operation of the plan and every
failure of the work):

  L1 a name a row holds is a file with the video of that row; L2 a name is held by one row;
  L3 a whole file in the library is held by a row; L4 a work file has a task;
  L5 a row is ready exactly when it holds a name; L6 a task is never left half-way;
  L7 the file of a row the configuration took out is gone; L8 a row the plan wants gets its file, under the name its number gives.

Every step here is a function of what the database and the disk hold now, so it can be taken at any time and any number of
times: an event is a reason to look, never the only carrier of an obligation, and a stop loses nothing.
"""
from __future__ import annotations

import os
from typing import Awaitable, Callable

from core import storage

from . import ytdlp
from .repository import DownloadsRepository

# (series, row) -> the name of the file of the row under the rules of naming, relative to the folder of the series.
Naming = Callable[[dict, dict], Awaitable[str]]


def _inside(base: str, path: str) -> bool:
    root = os.path.realpath(base)
    return os.path.commonpath([root, os.path.realpath(path)]) == root


class Library:
    def __init__(self, repo: DownloadsRepository, naming: Naming, log) -> None:
        self._repo, self._naming, self._log = repo, naming, log

    # --- where things lie ----------------------------------------------------------

    async def relative(self, series: dict, item: dict) -> str:
        """The name of the file of a row as the plan numbers it now."""
        return await self._naming(series, item)

    async def target(self, series: dict, item: dict) -> str:
        return os.path.join(series["save_path"], await self._naming(series, item))

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
        for item in await self._repo.planned_pending_items(series["id"]):
            filename = await self._naming(series, item)
            path = os.path.join(series["save_path"], filename)
            if await self._repo.holders_of_file(series["id"], filename, except_uid=item["unique_id"]):
                continue  # the file is another row's (the video this one displaces): this one is fetched, not adopted
            if writing.get(path, item["unique_id"]) != item["unique_id"]:
                continue  # the task of another row is to leave a file there: it is that row's
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
            if not name:
                continue
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
        for row in await self._repo.superseded_files(series["id"]):
            path = os.path.join(base, row["filename"])
            winner_path = os.path.join(base, row["winner_filename"])
            if os.path.realpath(path) == os.path.realpath(winner_path):
                continue
            if not _inside(base, path):
                self._log.warning("файл вне каталога серии не тронут: %s", path)
                continue
            if not await storage.call(os.path.isfile, winner_path):
                continue
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
        for row in await self._repo.unwanted_files(series["id"]):
            path = os.path.join(base, row["final_filename"])
            if not _inside(base, path):
                self._log.warning("файл вне каталога серии не тронут: %s", path)
                continue
            if not await self._repo.wanted_holder(series["id"], row["final_filename"], except_uid=row["unique_id"]):
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
        try:
            found = await storage.call(self._hidden_of, series["save_path"], free)
        except (OSError, storage.StorageTimeout) as exc:
            self._log.warning("серия %d: каталог не прочитан, рабочие файлы не проверены: %s", series["id"], exc)
            return 0
        removed = 0
        for path in found:
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
