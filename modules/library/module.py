"""Модуль library — файловая система медиатеки.

Листинг каталогов (этап 2) + перемещение сериалов (Р-17, этап 4):
  query library.relocate {series_id, new_path} — валидации оригинала
      (существование путей, один диск), отказ при активной задаче;
      исполнение асинхронно, по завершении library сам вызывает
      renaming.reprocess (цепочка «переместили → переименовали»).
  query library.relocation.active {series_id} — задачи для UI.
События: library.relocation.started/finished (контракты SSE),
series.busy.contribution {source: library} — карточка занята на время
перемещения (и только на время работы — находка 36).

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

from core import BaseModule, BusRequestError
from core.db import Database
from core.envelope import Envelope

from .repository import LibraryRepository

_NO_HANDLER = "нет обработчика"


class LibraryError(RuntimeError):
    pass


class LibraryModule(BaseModule):
    name = "library"

    def __init__(self, bus, db: Database, *,
                 allowed_roots: list[str] | None = None) -> None:
        # None = вся ФС (поведение старой системы); список — явные корни.
        self._allowed_roots = allowed_roots
        self.repo = LibraryRepository(db)
        super().__init__(bus)

    def register(self) -> None:
        self.handle("library.directories.list", self.on_list_directories)
        self.handle("library.relocate", self.on_relocate)
        self.handle("library.relocation.active", self.on_relocation_active)
        self.handle("series.deleted", self.on_series_deleted)

    async def on_start(self) -> None:
        # Reconcile: незавершённые перемещения продолжаются
        # (os.rename идемпотентен — перемещённое пропускается).
        for task in await self.repo.unfinished():
            self.log.info("возобновление перемещения: задача %d, сериал %d",
                          task["id"], task["series_id"])
            self._tasks.append(asyncio.create_task(self._execute(task)))

    def _check_allowed(self, path: str) -> None:
        if self._allowed_roots is None:
            return
        if not any(path == root or path.startswith(root.rstrip("/") + "/")
                   for root in self._allowed_roots):
            raise LibraryError(f"доступ к пути запрещён: {path}")

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
        if not os.path.isdir(path):
            raise LibraryError(f"не каталог или не существует: {path}")
        try:
            items = await asyncio.to_thread(self._list_dirs_sync, path)
        except PermissionError as exc:
            raise LibraryError(f"нет доступа к каталогу: {path}") from exc
        return {"path": path, "items": items}

    # --- перемещение (Р-17) ----------------------------------------------------------

    async def on_relocation_active(self, env: Envelope) -> list[dict]:
        return await self.repo.active_for_series(env.payload["series_id"])

    async def on_relocate(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        new_path = env.payload["new_path"]
        series = await self.request("catalog.series.get",
                                    {"series_id": series_id})
        old_path = series["save_path"]
        if new_path == old_path:
            raise LibraryError("путь не изменился")
        self._check_allowed(os.path.normpath(new_path))
        await asyncio.to_thread(self._check_same_device, old_path, new_path)
        task_id = await self.repo.create(series_id, new_path)
        if task_id is None:
            raise LibraryError("активная задача на перемещение уже "
                               "выполняется")
        task = {"id": task_id, "series_id": series_id, "new_path": new_path}
        self._tasks.append(asyncio.create_task(self._execute(task)))
        return {"task_id": task_id}

    @staticmethod
    def _check_same_device(old_path: str, new_path: str) -> None:
        """Проверка оригинала: перемещение между дисками не
        поддерживается (os.rename через границы ФС не работает)."""
        new_parent = os.path.dirname(new_path)
        if os.path.exists(old_path) and os.path.exists(new_parent):
            if os.stat(old_path).st_dev != os.stat(new_parent).st_dev:
                raise LibraryError(
                    "перемещение между разными дисками не поддерживается")

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
            if series["source_type"] == "vk_video":
                await self._move_vk_files(series_id, old_base, new_base)
            else:
                await self._move_torrents(series_id, new_base)
            await self.request("catalog.series.set_save_path", {
                "series_id": series_id, "save_path": new_base})
            await self.repo.delete(task["id"])
            self.log.info("сериал %d перемещён: %s -> %s", series_id,
                          old_base, new_base)
            await self._reprocess_names(series_id)
            self.publish_event("library.relocation.finished", {
                "series_id": series_id, "success": True,
                "message": "Сериал успешно перемещен."})
        except Exception as exc:  # noqa: BLE001 — ошибка остаётся носителем
            self.log.exception("перемещение сериала %d не удалось",
                               series_id)
            await self.repo.set_status(task["id"], "error", str(exc))
            self.publish_event("library.relocation.finished", {
                "series_id": series_id, "success": False,
                "message": str(exc)})
        finally:
            # busy — только активная работа (находка 36)
            self._busy(series_id, False)

    async def _move_vk_files(self, series_id: int, old_base: str,
                             new_base: str) -> None:
        items = await self.request("scan.media.list",
                                   {"series_id": series_id})
        paths = [i["final_filename"] for i in items if i.get("final_filename")]
        for child in await self._sliced_files(series_id):
            paths.append(child["file_path"])
        for rel in paths:
            await asyncio.to_thread(self._move_one, old_base, new_base, rel)

    async def _sliced_files(self, series_id: int) -> list[dict]:
        try:
            return await self.request("slicing.files.list",
                                      {"series_id": series_id})
        except BusRequestError as exc:
            if _NO_HANDLER in str(exc):
                return []
            raise

    @staticmethod
    def _move_one(old_base: str, new_base: str, rel: str) -> None:
        old_abs = os.path.join(old_base, rel)
        new_abs = os.path.join(new_base, rel)
        if not os.path.exists(old_abs):
            return  # уже перемещён (идемпотентность resume)
        os.makedirs(os.path.dirname(new_abs), exist_ok=True)
        os.rename(old_abs, new_abs)

    async def _move_torrents(self, series_id: int, new_base: str) -> None:
        for torrent in await self.request("torrents.db.active",
                                          {"series_id": series_id}):
            if torrent.get("qb_hash"):
                await self.request("torrents.set_location", {
                    "hash": torrent["qb_hash"], "location": new_base})

    async def _reprocess_names(self, series_id: int) -> None:
        """Цепочка оригинала: после перемещения — переименование."""
        try:
            await self.request("renaming.reprocess",
                               {"series_id": series_id}, timeout=600)
        except BusRequestError as exc:
            if _NO_HANDLER in str(exc):
                self.log.warning("модуль renaming недоступен — "
                                 "переименование после перемещения "
                                 "пропущено")
            else:
                self.log.error("переименование после перемещения: %s", exc)

    def _busy(self, series_id: int, busy: bool) -> None:
        self.publish_event("series.busy.contribution", {
            "source": "library", "series_id": series_id, "busy": busy})

    async def on_series_deleted(self, env):
        """Каскад Р-19: владелец чистит relocation_tasks."""
        await self.repo.delete_for_series(env.payload["series_id"])
