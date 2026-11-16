"""Модуль library — файловая система медиатеки.

Листинг каталогов (этап 2) + перемещение сериалов (Р-17, этап 4). Диск меняется только в синхронизации (И-59):
  query library.relocation.plan {series_id, new_path} -> {new_path | None} — сохранение карточки записывает план
      переноса (проверки оригинала: абсолютный путь, разрешённый корень, один диск); путь, где сериал уже лежит,
      снимает план; во время переноса — отказ;
  query library.relocation.get {series_id} -> {new_path | None} — перенос, который ждёт синхронизации или идёт;
  query library.relocation.apply {series_id} -> {moved} — первый шаг прогона синхронизации (scan): перенос по плану,
      ответ после его конца; провал — ошибка запроса, план остаётся до следующей синхронизации. Имена файлов
      синхронизация приводит к правилам сама, после переноса.
События: library.relocation.planned {series_id, new_path | None}; library.relocation.started/finished (контракты SSE);
series.busy.contribution {source: library} — карточка занята на время перемещения (и только на время работы —
находка 36). Перенос, прерванный остановкой процесса, продолжается на старте (это работа начатой синхронизации).

По разбору старого routes/filebrowser.py (находка №9): список
allowed_roots содержал '/', что делало проверку фиктивной — ЛЮБОЙ
абсолютный путь начинается с '/'. Честное решение: корни задаются
конструктором; по умолчанию вся ФС (фактическое поведение старой
системы в однопользовательском LAN-сервисе), но теперь это явная
настройка, а не иллюзия защиты. Нормализация пути — ДО проверок.

Дисковые операции — в потоке (to_thread): listdir/rename на сетевом
диске может висеть, событийный цикл ждать не обязан.
"""
from __future__ import annotations

import asyncio
import os

from core import BaseModule, sanitize_path, storage
from core.db import Database
from core.envelope import Envelope
from core.reasons import reason

from .repository import LibraryRepository



class LibraryError(RuntimeError):
    pass


class LibraryModule(BaseModule):
    name = "library"

    def __init__(self, bus, db: Database, *,
                 allowed_roots: list[str] | None = None) -> None:
        # None = вся ФС (поведение старой системы); список — явные корни.
        self._allowed_roots = allowed_roots
        self.repo = LibraryRepository(db)
        self._relocations: dict[int, asyncio.Task] = {}  # Д2: отмена при удалении
        super().__init__(bus)

    def register(self) -> None:
        self.handle("library.directories.list", self.on_list_directories)
        self.handle("library.relocation.plan", self.on_plan)
        self.handle("library.relocation.get", self.on_get)
        self.handle("library.relocation.apply", self.on_apply)
        self.handle("series.deleted", self.on_series_deleted)
        self.handle("system.started", self.on_system_started)

    async def on_system_started(self, env: Envelope) -> None:
        """Every module is up. Reconcile: незавершённые перемещения продолжаются (os.rename идемпотентен —
        перемещённое пропускается); они спрашивают торренты и переименование, которые на старте этого модуля ещё
        не отвечают."""
        for task in await self.repo.unfinished():
            self.log.info("возобновление перемещения: задача %d, сериал %d",
                          task["id"], task["series_id"])
            self._start(task)

    def _check_allowed(self, path: str) -> None:
        if self._allowed_roots is None:
            return
        if not any(path == root or path.startswith(root.rstrip("/") + "/")
                   for root in self._allowed_roots):
            raise LibraryError(reason("path_forbidden", path=path))

    @staticmethod
    def _list_dirs_sync(path: str) -> list[dict]:
        items = []
        for name in os.listdir(path):
            full = os.path.join(path, name)
            if os.path.isdir(full):
                items.append({"name": name, "path": full,
                              "type": "directory"})
        items.sort(key=lambda x: x["name"].lower())
        return items

    async def on_list_directories(self, env: Envelope) -> dict:
        raw = (env.payload or {}).get("path") or "/"
        path = os.path.normpath(raw)
        self._check_allowed(path)
        if not await storage.call(os.path.isdir, path):
            raise LibraryError(reason("not_directory", path=path))
        try:
            items = await storage.call(self._list_dirs_sync, path)
        except PermissionError as exc:
            raise LibraryError(reason("path_no_access", path=path)) from exc
        return {"path": path, "items": items}

    # --- перемещение (Р-17) ----------------------------------------------------------

    async def on_plan(self, env: Envelope) -> dict:
        """The save of the card: the move is written down, the files stay where they are until the sync (И-59)."""
        series_id = env.payload["series_id"]
        new_path = sanitize_path(env.payload["new_path"])
        series = await self.request("catalog.series.get",
                                    {"series_id": series_id})
        if series_id in self._relocations or await self.repo.moving(series_id):
            raise LibraryError(reason("move_in_progress"))
        if new_path == series["save_path"]:
            if await self.repo.drop_plan(series_id):
                self.publish_event("library.relocation.planned", {"series_id": series_id, "new_path": None})
            return {"new_path": None}
        if not os.path.isabs(new_path):
            raise LibraryError(reason("save_path_relative", path=new_path))
        self._check_allowed(os.path.normpath(new_path))
        await storage.call(self._check_same_device, series["save_path"], new_path)
        await self.repo.plan(series_id, new_path)
        self.publish_event("library.relocation.planned", {"series_id": series_id, "new_path": new_path})
        return {"new_path": new_path}

    async def on_get(self, env: Envelope) -> dict:
        task = await self.repo.of_series(env.payload["series_id"])
        return {"new_path": task["new_path"] if task else None}

    async def on_apply(self, env: Envelope) -> dict:
        """The first step of a sync: the planned move is made and answered when it has ended; a move a restart took up
        is waited for. A failure is the failure of the request, and the plan stays for the next sync."""
        series_id = env.payload["series_id"]
        running = self._relocations.get(series_id)
        if running is None:
            task = await self.repo.of_series(series_id)
            if task is None:
                return {"moved": False}
            running = self._start(task)
        await running
        return {"moved": True}

    def _start(self, task: dict) -> asyncio.Task:
        t = self.spawn(self._execute(task))
        # A move taken up at the start has no one waiting: its failure is in the log and on its plan.
        t.add_done_callback(lambda done: done.cancelled() or done.exception())
        self._relocations[task["series_id"]] = t
        return t

    @staticmethod
    def _check_same_device(old_path: str, new_path: str) -> None:
        """Проверка оригинала: перемещение между дисками не
        поддерживается (os.rename через границы ФС не работает)."""
        new_parent = os.path.dirname(new_path)
        if os.path.exists(old_path) and os.path.exists(new_parent):
            if os.stat(old_path).st_dev != os.stat(new_parent).st_dev:
                raise LibraryError(reason("move_cross_device"))

    async def _execute(self, task: dict) -> None:
        series_id = task["series_id"]
        self._busy(series_id, True)
        self.publish_event("library.relocation.started",
                           {"series_id": series_id})
        try:
            await self.repo.set_status(task["id"], "in_progress")
            series = await self.request("catalog.series.get",
                                        {"series_id": series_id})
            old_base, new_base = series["save_path"], task["new_path"]
            await self._move_plan_files(series_id, old_base, new_base)
            await self._move_torrents(series_id, new_base)
            await self.request("catalog.series.set_save_path", {
                "series_id": series_id, "save_path": new_base})
            await self.repo.delete(task["id"])
            self.log.info("сериал %d перемещён: %s -> %s", series_id,
                          old_base, new_base)
            self.publish_event("library.relocation.finished", {
                "series_id": series_id, "success": True,
                "message": reason("relocation_done")})
        except Exception as exc:
            # The plan stays with its error: the next sync makes the move again (a moved file is skipped).
            self.log.exception("перемещение сериала %d не удалось",
                               series_id)
            await self.repo.set_status(task["id"], "planned", str(exc))
            self.publish_event("library.relocation.finished", {
                "series_id": series_id, "success": False,
                "message": str(exc)})
            raise
        finally:
            # busy — только активная работа (находка 36)
            self._relocations.pop(series_id, None)
            self._busy(series_id, False)

    async def _move_plan_files(self, series_id: int, old_base: str,
                               new_base: str) -> None:
        # The files of a series are the asset of its plan (modules/planner).
        paths = list((await self.request("planner.files.get", {"series_id": series_id}))["names"])
        for rel in paths:
            await storage.call(self._move_one, old_base, new_base, rel, timeout=storage.MOVE_TIMEOUT_S,
                               owns=(os.path.join(old_base, rel), os.path.join(new_base, rel)))

    @staticmethod
    def _move_one(old_base: str, new_base: str, rel: str) -> None:
        old_abs = os.path.join(old_base, rel)
        new_abs = os.path.join(new_base, rel)
        if not os.path.exists(old_abs):
            return  # уже перемещён (идемпотентность resume)
        os.makedirs(os.path.dirname(new_abs), exist_ok=True)
        os.rename(old_abs, new_abs)

    async def _move_torrents(self, series_id: int, new_base: str) -> None:
        """The torrents of the old pipeline (stage 15, step 11) lie in the folder of the series: the client moves them."""
        for torrent in await self.request("torrents.db.active",
                                          {"series_id": series_id}):
            if torrent.get("qb_hash"):
                await self.request("torrents.set_location", {
                    "hash": torrent["qb_hash"], "location": new_base})

    def _busy(self, series_id: int, busy: bool) -> None:
        self.publish_event("series.busy.contribution", {
            "source": "library", "series_id": series_id, "busy": busy})

    async def on_series_deleted(self, env):
        """Каскад Р-19 + Д2: прервать идущее перемещение этой серии
        (os.rename атомарен — полуфайлов нет, останавливаем перенос
        оставшихся файлов) и вычистить relocation_tasks."""
        series_id = env.payload["series_id"]
        t = self._relocations.pop(series_id, None)
        if t and not t.done():
            t.cancel()
        await self.repo.delete_for_series(series_id)
        self.publish_event("series.purged",
                           {"series_id": series_id, "owner": self.name})
