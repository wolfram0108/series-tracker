"""Module library — the file system of the media library.

The listing of folders (stage 2) and the move of a series (Р-17, stage 4). The disk changes in a sync only (И-59). The answers and the
events are the models of core/contracts/library.py, handed over as they are; what a path means, how a folder is listed and where the
files go is pure and lives in calc/library/paths.py:
  library.relocation.plan RelocationPlan -> RelocationPath (command) — the save of the card writes the plan of the move (the checks of
      the original: an absolute path, an admitted root, one disk); the path the series already lies in drops the plan; a refusal
      while a move is under way;
  library.relocation.get RelocationOf -> RelocationPath — the move that waits for a sync or is under way;
  library.relocation.apply RelocationOf -> Relocated (command) — the first step of a sync run (scan): the move by the plan, answered
      when it has ended; a failure is the failure of the request, the plan stays until the next sync. The sync brings the names of
      the files to the rules itself, after the move;
  library.directories.list DirectoriesGet -> DirectoryListing — the folders of a folder.
Events: library.relocation.planned {series_id, new_path | None}; library.relocation.started/finished (the contracts of SSE);
series.busy.contribution {source: library} — the card is busy while the move lasts (and only while the work lasts — finding 36). A
move cut short by a stop of the process goes on at the start (it is the work of a sync that began).

From the analysis of the old routes/filebrowser.py (finding №9): its list allowed_roots held '/', which made the check fictitious —
ANY absolute path starts with '/'. The honest decision: the roots are given to the constructor; by default the whole file system (what
the old system actually did in a single-user LAN service), but now it is an explicit setting, not an illusion of protection. The path
is normalized BEFORE the checks.

The disk operations run in a thread (storage.call): listdir/rename on a network disk may hang, and the event loop need not wait.
"""
from __future__ import annotations

import asyncio
import os

from calc.library.paths import destination_refusal, folder_of, forbidden, listing_of, moves_of
from core import storage
from core.fsnames import sanitize_path
from core.contracts import (BusyContribution, DirectoriesGet, DirectoryListing, Relocated, RelocationFinished, RelocationOf,
                            RelocationPath, RelocationPlan, RelocationPlanned, RelocationStarted, SeriesDeleted, SeriesPurged)
from core.module import BaseModule
from core.db import Database
from core.envelope import Envelope
from core.reasons import reason
from core.topics import (CATALOG_SERIES_GET, CATALOG_SERIES_SET_SAVE_PATH, LIBRARY_DIRECTORIES_LIST,
                         LIBRARY_RELOCATION_APPLY, LIBRARY_RELOCATION_FINISHED, LIBRARY_RELOCATION_GET,
                         LIBRARY_RELOCATION_PLAN, LIBRARY_RELOCATION_PLANNED, LIBRARY_RELOCATION_STARTED, PLANNER_FILES_GET,
                         SERIES_BUSY_CONTRIBUTION, SERIES_DELETED, SERIES_PURGED, SYSTEM_STARTED, TORRENTS_DB_ACTIVE,
                         TORRENTS_SET_LOCATION)

from .repository import LibraryRepository


class LibraryError(RuntimeError):
    pass


class LibraryModule(BaseModule):
    name = "library"

    def __init__(self, bus, db: Database, *,
                 allowed_roots: list[str] | None = None) -> None:
        # None = the whole file system (what the old system did); a list = explicit roots.
        self._allowed_roots = allowed_roots
        self.repo = LibraryRepository(db)
        self._relocations: dict[int, asyncio.Task] = {}  # Д2: cancelled when the series is deleted
        super().__init__(bus)

    def register(self) -> None:
        self.handle(LIBRARY_DIRECTORIES_LIST, self.on_list_directories)
        self.handle(LIBRARY_RELOCATION_PLAN, self.on_plan)
        self.handle(LIBRARY_RELOCATION_GET, self.on_get)
        self.handle(LIBRARY_RELOCATION_APPLY, self.on_apply)
        self.handle(SERIES_DELETED, self.on_series_deleted)
        self.handle(SYSTEM_STARTED, self.on_system_started)

    async def on_system_started(self, env: Envelope) -> None:
        """Every module is up. Reconcile: the unfinished moves go on (os.rename is idempotent — what is moved is skipped); they ask
        torrents and renaming, which do not answer yet at the start of this module."""
        for task in await self.repo.unfinished():
            self.log.info("возобновление перемещения: задача %d, сериал %d",
                          task["id"], task["series_id"])
            self._start(task)

    def _check_allowed(self, path: str) -> None:
        if (refused := forbidden(self._allowed_roots, path)) is not None:
            raise LibraryError(refused)

    @staticmethod
    def _entries_sync(path: str) -> list[tuple[str, bool]]:
        return [(name, os.path.isdir(os.path.join(path, name))) for name in os.listdir(path)]

    async def on_list_directories(self, env: Envelope) -> DirectoryListing:
        path = folder_of(DirectoriesGet.model_validate(env.payload or {}).path)
        self._check_allowed(path)
        if not await storage.call(os.path.isdir, path):
            raise LibraryError(reason("not_directory", path=path))
        try:
            entries = await storage.call(self._entries_sync, path)
        except PermissionError as exc:
            raise LibraryError(reason("path_no_access", path=path)) from exc
        return listing_of(path, entries)

    # --- the move (Р-17) ---------------------------------------------------------------

    async def on_plan(self, env: Envelope) -> RelocationPath:
        """The save of the card: the move is written down, the files stay where they are until the sync (И-59)."""
        command = RelocationPlan.model_validate(env.payload)
        series_id = command.series_id
        new_path = sanitize_path(command.new_path)
        series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
        if series_id in self._relocations or await self.repo.moving(series_id):
            raise LibraryError(reason("move_in_progress"))
        if new_path == series.save_path:
            if await self.repo.drop_plan(series_id):
                self._planned(series_id, None)
            return RelocationPath(new_path=None)
        if (refused := destination_refusal(new_path, self._allowed_roots)) is not None:
            raise LibraryError(refused)
        await storage.call(self._check_same_device, series.save_path, new_path)
        await self.repo.plan(series_id, new_path)
        self._planned(series_id, new_path)
        return RelocationPath(new_path=new_path)

    def _planned(self, series_id: int, new_path: str | None) -> None:
        self.publish_event(LIBRARY_RELOCATION_PLANNED,
                           RelocationPlanned(series_id=series_id, new_path=new_path).model_dump(mode="json"))

    async def on_get(self, env: Envelope) -> RelocationPath:
        task = await self.repo.of_series(RelocationOf.model_validate(env.payload).series_id)
        return RelocationPath(new_path=task["new_path"] if task else None)

    async def on_apply(self, env: Envelope) -> Relocated:
        """The first step of a sync: the planned move is made and answered when it has ended; a move a restart took up
        is waited for. A failure is the failure of the request, and the plan stays for the next sync."""
        series_id = RelocationOf.model_validate(env.payload).series_id
        running = self._relocations.get(series_id)
        if running is None:
            task = await self.repo.of_series(series_id)
            if task is None:
                return Relocated(moved=False)
            running = self._start(task)
        await running
        return Relocated(moved=True)

    def _start(self, task: dict) -> asyncio.Task:
        t = self.spawn(self._execute(task))
        # A move taken up at the start has no one waiting: its failure is in the log and on its plan.
        t.add_done_callback(lambda done: done.cancelled() or done.exception())
        self._relocations[task["series_id"]] = t
        return t

    @staticmethod
    def _check_same_device(old_path: str, new_path: str) -> None:
        """A check of the original: a move between disks is not supported (os.rename does not cross the borders of a file system)."""
        new_parent = os.path.dirname(new_path)
        if os.path.exists(old_path) and os.path.exists(new_parent):
            if os.stat(old_path).st_dev != os.stat(new_parent).st_dev:
                raise LibraryError(reason("move_cross_device"))

    async def _execute(self, task: dict) -> None:
        series_id = task["series_id"]
        self._busy(series_id, True)
        self.publish_event(LIBRARY_RELOCATION_STARTED, RelocationStarted(series_id=series_id).model_dump(mode="json"))
        try:
            await self.repo.set_status(task["id"], "in_progress")
            series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
            old_base, new_base = series.save_path, task["new_path"]
            await self._move_plan_files(series_id, old_base, new_base)
            await self._move_torrents(series_id, new_base)
            await self.request(CATALOG_SERIES_SET_SAVE_PATH, {
                "series_id": series_id, "save_path": new_base})
            await self.repo.delete(task["id"])
            self.log.info("сериал %d перемещён: %s -> %s", series_id,
                          old_base, new_base)
            self.publish_event(LIBRARY_RELOCATION_FINISHED, RelocationFinished(
                series_id=series_id, success=True, message=reason("relocation_done")).model_dump(mode="json"))
        except Exception as exc:
            # The plan stays with its error: the next sync makes the move again (a moved file is skipped).
            self.log.exception("перемещение сериала %d не удалось",
                               series_id)
            await self.repo.set_status(task["id"], "planned", str(exc))
            self.publish_event(LIBRARY_RELOCATION_FINISHED, RelocationFinished(
                series_id=series_id, success=False, message=str(exc)).model_dump(mode="json"))
            raise
        finally:
            # busy is only the active work (finding 36)
            self._relocations.pop(series_id, None)
            self._busy(series_id, False)

    async def _move_plan_files(self, series_id: int, old_base: str,
                               new_base: str) -> None:
        # The files of a series are the asset of its plan (modules/planner).
        names = list((await self.reply_of(PLANNER_FILES_GET, {"series_id": series_id})).names)
        for old_abs, new_abs in moves_of(old_base, new_base, names):
            await storage.call(self._move_one, old_abs, new_abs, timeout=storage.MOVE_TIMEOUT_S, owns=(old_abs, new_abs))

    @staticmethod
    def _move_one(old_abs: str, new_abs: str) -> None:
        if not os.path.exists(old_abs):
            return  # already moved (the idempotence of the resume)
        os.makedirs(os.path.dirname(new_abs), exist_ok=True)
        os.rename(old_abs, new_abs)

    async def _move_torrents(self, series_id: int, new_base: str) -> None:
        """The torrents of the old pipeline (stage 15, step 11) lie in the folder of the series: the client moves them."""
        for torrent in await self.request(TORRENTS_DB_ACTIVE,
                                          {"series_id": series_id}):
            if torrent.get("qb_hash"):
                await self.request(TORRENTS_SET_LOCATION, {
                    "hash": torrent["qb_hash"], "location": new_base})

    def _busy(self, series_id: int, busy: bool) -> None:
        self.publish_event(SERIES_BUSY_CONTRIBUTION, BusyContribution(source="library", series_id=series_id, busy=busy)
                           .model_dump(mode="json"))

    async def on_series_deleted(self, env):
        """The cascade of Р-19 + Д2: the move of this series that goes on is cut short (os.rename is atomic — there are no
        half-files; the move of the remaining files stops) and relocation_tasks is cleaned."""
        series_id = SeriesDeleted.model_validate(env.payload).series_id
        t = self._relocations.pop(series_id, None)
        if t and not t.done():
            t.cancel()
        await self.repo.delete_for_series(series_id)
        self.publish_event(SERIES_PURGED, SeriesPurged(series_id=series_id, owner=self.name).model_dump(mode="json"))
