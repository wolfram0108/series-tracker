"""Модуль library — файловая система медиатеки.

Этап 2: листинг каталогов для DirectoryPicker фронта. Relocation,
верификация файлов и синхронизация ФС↔БД переезжают сюда на этапе 4.

По разбору старого routes/filebrowser.py (находка №9): список
allowed_roots содержал '/', что делало проверку фиктивной — ЛЮБОЙ
абсолютный путь начинается с '/'. Честное решение: корни задаются
конструктором; по умолчанию вся ФС (фактическое поведение старой
системы в однопользовательском LAN-сервисе), но теперь это явная
настройка, а не иллюзия защиты. Нормализация пути — ДО проверок.

Дисковые операции — в потоке (to_thread): listdir на сетевом диске
может висеть, событийный цикл ждать не обязан.
"""
from __future__ import annotations

import asyncio
import os

from core import BaseModule
from core.envelope import Envelope


class LibraryError(RuntimeError):
    pass


class LibraryModule(BaseModule):
    name = "library"

    def __init__(self, bus, *, allowed_roots: list[str] | None = None) -> None:
        # None = вся ФС (поведение старой системы); список — явные корни.
        self._allowed_roots = allowed_roots
        super().__init__(bus)

    def register(self) -> None:
        self.handle("library.directories.list", self.on_list_directories)

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
