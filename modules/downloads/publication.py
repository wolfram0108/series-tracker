"""A downloaded file takes the name of its video.

The step that moves a whole file from its work file to the name of the video is made of a read of the plan, a replacement of
a file on the disk, and a write of the record. The disk and the database cannot be changed in one step, so the step is made
recoverable: the task says `publishing` first (with the path it is to put the file at), and from then on what is left to do
is told by what lies on the disk, not by what the process remembers.

  - the work file is still there: the file is not placed yet. The plan is read again (the video is still wanted, still
    this one, and its name is free), and the file is put under the name: the replacement is made, or made again when its
    outcome is not known;
  - the work file is gone and the video lies at the path: the replacement is done, and the record is written;
  - neither is there: nothing is left to publish, and the video is fetched again.

While an operation on the work file or on the path has not ended (its hold on the path is not released) its outcome is not
known, and nothing is decided about the file: the task is looked at again when the path is free.

A name that a row the plan wants holds is never taken over: the task waits, `publishing`, and is looked at again when
renaming gives the file its new name (the plan renumbered the episodes and the files follow). After `publishing` nothing ends
the task but the record of the finished file, a plan that does not want the file any more, or a replacement that certainly
did not happen. A replacement whose outcome is not known (the storage did not answer, the path is owned by another operation)
is not a failure: the task stays `publishing` and is looked at again when the path is free.
"""
from __future__ import annotations

import os
from typing import Awaitable, Callable

from core import storage
from core.retry import Budget, retrying

from .library import Library
from .repository import PUBLISH_GONE, PUBLISH_OK, PUBLISH_STALE, PUBLISH_TAKEN, DownloadsRepository

# How the publication of a file ended, as `Publication.run` says it.
PUBLISHED, REFETCH, GONE, REFUSED, WAITING = "published", "refetch", "gone", "refused", "waiting"

# The record of a finished file is written again this many times before the task is left `publishing` for the next look.
# Four tries in thirty seconds: a database that does not take a write for that long is not coming back by being asked more.
PUBLISH_BUDGET = Budget(attempts=4, pauses=(0.5, 2.0, 5.0), limit_s=30.0)


class PublicationUnfinished(RuntimeError):
    """The step is not finished and not failed: the task stays `publishing` and is finished when it is looked at again."""


class Publication:
    def __init__(self, repo: DownloadsRepository, library: Library, log, *, running: Callable[[int], bool],
                 resume: Callable[[int], None], end_task: Callable[[dict], Awaitable[bool]]) -> None:
        self._repo, self._library, self._log = repo, library, log
        self._running = running    # whether a worker is at a task now
        self._resume = resume      # asks for the series to be looked at again
        self._end_task = end_task  # a task the plan does not want any more ends, with what it wrote
        self._watching: set[int] = set()  # tasks that wait for a path to be free

    async def run(self, task: dict, series: dict) -> str:
        """Put the work file of a whole download under the name of its video, if the plan still wants it there."""
        verdict, task = await self._admit(task, series)
        if verdict != PUBLISH_OK:
            return await self._not_admitted(verdict, task)
        # From here nothing but the record of the file ends the task.
        try:
            return await self._complete(task, series)
        except PublicationUnfinished:
            raise
        except Exception as exc:  # noqa: BLE001 - whatever it was, the step is not known to have failed
            raise PublicationUnfinished(f"публикация файла {os.path.basename(task['save_path'])} прервана: {exc}") from exc

    async def finish_unfinished(self, series: dict) -> int:
        """Carry on the publications of a series that a stop, a lost answer, a failed write or a name that was taken left:
        tasks that are `publishing` and have no worker."""
        done = 0
        for task in await self._repo.publishing_tasks(series["id"]):
            if self._running(task["id"]) or self._wait_for_paths(task):
                continue
            try:
                if await storage.call(os.path.exists, self._library.work_path(task)):
                    verdict, task = await self._admit(task, series)
                    if verdict != PUBLISH_OK:
                        await self._not_admitted(verdict, task)
                        continue
                if await self._complete(task, series) == PUBLISHED:
                    done += 1
                    self._log.info("публикация файла закончена: %s", os.path.basename(task["save_path"]))
            except PublicationUnfinished as exc:
                self._log.warning("публикация файла задачи %d не закончена: %s", task["id"], exc)
        return done

    def _wait_for_paths(self, task: dict) -> bool:
        """Whether an operation on the paths of the task has not ended; then the task is looked at again when the path is free."""
        busy = next((path for path in (task["save_path"], self._library.work_path(task)) if storage.busy(path)), None)
        if busy is None:
            return False
        if task["id"] not in self._watching:
            self._watching.add(task["id"])

            def free() -> None:
                self._watching.discard(task["id"])
                self._resume(task["series_id"])

            storage.when_free(busy, free)
        return True

    async def _admit(self, task: dict, series: dict) -> tuple[str, dict]:
        """Read the plan again for a whole work file that is to take its name: the verdict, and the task with the path the
        plan gives the file now."""
        item = await self._repo.item(task["task_key"])
        if item is None:
            return PUBLISH_GONE, task
        relative = await self._library.relative(series, item)
        target = os.path.join(series["save_path"], relative)
        return await self._repo.begin_publish(task["id"], target, relative), {**task, "save_path": target}

    async def _not_admitted(self, verdict: str, task: dict) -> str:
        if verdict == PUBLISH_GONE:  # the plan does not want the video any more
            await self._end_task(task)
            return GONE
        if verdict == PUBLISH_STALE:  # the row shows another video: the work file is of the old one
            if await self._library.clear_task_files(task):
                await self._repo.requeue_task(task["id"])
            else:
                await self._end_task(task)
            return REFETCH
        if verdict == PUBLISH_TAKEN:
            self._log.info("файл %s ждёт имени: его держит строка, которую хочет план", os.path.basename(task["save_path"]))
            return WAITING
        raise ValueError(verdict)

    async def _complete(self, task: dict, series: dict) -> str:
        """Do what is not done yet of the step, as the disk shows it (see the top of the file)."""
        work, target = self._library.work_path(task), task["save_path"]
        relative = os.path.relpath(target, series["save_path"]).replace("\\", "/")
        if await storage.call(os.path.exists, work):
            try:
                await storage.call(os.makedirs, os.path.dirname(target), exist_ok=True)
                await storage.call(os.replace, work, target, owns=(work, target))
            except (storage.StorageTimeout, storage.StorageBusy) as exc:
                if not self._wait_for_paths(task):
                    self._resume(task["series_id"])  # the operation ended meanwhile: look again at once
                raise PublicationUnfinished(f"исход замены файла {relative} не известен: {exc}") from exc
            except OSError as exc:
                await self._repo.fail_publish(task["id"], task["task_key"], f"файл не поставлен под имя {relative}: {exc}")
                return REFUSED
        elif not await storage.call(os.path.exists, target):
            await self._repo.requeue_task(task["id"])
            return REFETCH

        async def record() -> Exception | None:
            try:
                await self._repo.finish_publish(task["id"], task["task_key"], relative)
            except Exception as exc:  # noqa: BLE001 - the file is in place, so the record is asked for again
                return exc
            return None

        failure, _, unsure = await retrying(record, final=lambda result: result is None, budget=PUBLISH_BUDGET)
        if unsure:
            raise PublicationUnfinished(f"запись файла {relative} не сохранена: {failure}") from failure
        await self._library.clear_task_files(task)  # the pieces an earlier version left beside the video, if any
        return PUBLISHED
