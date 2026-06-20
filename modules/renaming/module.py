"""Модуль renaming — приведение имён файлов к строгому формату (Р-15).

Queries:
  renaming.reprocess {series_id} — массовая переобработка имён по типу
      сериала (контракт Р-12: сканер вызывает перед каждым сканом; тот
      же запрос — «сохранили свойства сериала в UI», этап 5):
      vk_video → переименование скачанных файлов и нарезанных детей на
      диске; torrent → ревизия имён файлов всех активных раздач в qBit.
  renaming.process_torrent {series_id, qb_hash} — переименование файлов
      одной раздачи (контракт Р-14: стадия 'renaming' конвейера).

Событие renaming.finished {series_id} — контракт SSE renaming_complete.

Имена вычисляет rules (rules.format_filename / format_torrent_file —
Р-15, дифф 349/349 с реальными именами); физика (os.rename, rename в
qBit) — здесь. Чужие колонки — командами владельцам:
downloads.item.set_filename, slicing.file.set_path (slicing — фейк до
своего разбора), torrents.db.files.upsert.

Ресьюмабельность: запись renaming_tasks живёт на время работы
(и держит is_busy-семантику); незавершённые при старте перевыполняются
(операция идемпотентна: уже переименованное пропускается).
"""
from __future__ import annotations

import asyncio
import os

from core import BaseModule, BusRequestError
from core.db import Database
from core.envelope import Envelope

from .repository import RenamingRepository

_NO_HANDLER = "нет обработчика"
_VIDEO_EXT = (".mkv", ".avi", ".mp4", ".mov", ".wmv", ".webm")


class RenamingModule(BaseModule):
    name = "renaming"

    def __init__(self, bus, db: Database) -> None:
        self.repo = RenamingRepository(db)
        self._locks: dict[int, asyncio.Lock] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle("renaming.reprocess", self.on_reprocess, concurrent=True)
        self.handle("renaming.process_torrent", self.on_process_torrent,
                    concurrent=True)
        self.handle("renaming.tasks.active", self.on_tasks_active)
        self.handle("renaming.preview", self.on_preview, concurrent=True)
        self.handle("series.deleted", self.on_series_deleted)

    async def on_start(self) -> None:
        for task in await self.repo.unfinished():
            self.log.info("перевыполнение незавершённой переобработки "
                          "(задача %d, сериал %d)", task["id"],
                          task["series_id"])
            self._tasks.append(asyncio.create_task(
                self._safe_reprocess(task["series_id"], resume_of=task["id"])))

    async def on_tasks_active(self, env: Envelope) -> list[dict]:
        """Для is_busy (этап 5 / library)."""
        return await self.repo.active_for_series(env.payload["series_id"])

    async def on_preview(self, env: Envelope) -> dict:
        """Контракт GET rename_preview (Р-21): тот же обход, что
        у переобработки VK, но без касания диска и БД."""
        series_id = env.payload["series_id"]
        series = await self.request("catalog.series.get",
                                    {"series_id": series_id})
        if not series.get("parser_profile_id"):
            return {"preview": [], "needs_rename_count": 0}
        items = await self.request("scan.media.list",
                                   {"series_id": series_id})
        items_map = {i["unique_id"]: i for i in items}
        preview = []
        needs = 0
        for item in items:
            if not item.get("source_title"):
                continue
            current = item.get("final_filename")
            reply = await self.request("rules.format_filename", {
                "series": series, "media_item": item,
                "original_filename": current})
            new_name = reply["filename"]
            if current and current != new_name:
                needs += 1
            preview.append({"unique_id": item["unique_id"],
                            "type": "media_item",
                            "current_filename": current,
                            "new_filename_preview": new_name})
        for child in await self._sliced_files(series_id):
            parent = items_map.get(child["source_media_item_unique_id"])
            if not parent:
                continue
            current = child.get("file_path")
            reply = await self.request("rules.format_filename", {
                "series": series, "media_item": parent,
                "episode_override": child["episode_number"],
                "original_filename": current})
            new_name = reply["filename"]
            if current and current != new_name:
                needs += 1
            preview.append({"unique_id": f"sliced-{child['id']}",
                            "type": "sliced_file",
                            "current_filename": current,
                            "new_filename_preview": new_name})
        return {"preview": preview, "needs_rename_count": needs}

    # --- массовая переобработка ------------------------------------------------------

    async def on_reprocess(self, env: Envelope) -> dict:
        return await self._reprocess(env.payload["series_id"])

    async def _safe_reprocess(self, series_id: int,
                              resume_of: int | None = None) -> None:
        try:
            if resume_of is not None:
                await self.repo.delete(resume_of)
            await self._reprocess(series_id)
        except Exception:  # noqa: BLE001
            self.log.exception("перевыполнение переобработки сериала %d "
                               "не удалось", series_id)

    async def _reprocess(self, series_id: int) -> dict:
        lock = self._locks.setdefault(series_id, asyncio.Lock())
        async with lock:  # повторный запрос дожидается и выполняется следом
            series = await self.request("catalog.series.get",
                                        {"series_id": series_id})
            if not series.get("parser_profile_id"):
                self.log.warning("переобработка %d пропущена: профиль "
                                 "правил не назначен", series_id)
                return {"renamed": 0, "skipped": True}

            await self.repo.delete_errors(series_id)
            task_type = ("mass_vk_reprocess"
                         if series["source_type"] == "vk_video"
                         else "mass_torrent_reprocess")
            task_id = await self.repo.create(series_id, task_type)
            self._busy(series_id, True)
            try:
                if series["source_type"] == "vk_video":
                    renamed = await self._reprocess_vk(series)
                else:
                    renamed = await self._reprocess_torrents(series)
                await self.repo.delete(task_id)
            except Exception as exc:
                await self.repo.set_error(task_id, str(exc))
                raise
            finally:
                # busy — только активная работа: ошибка не блокирует
                # карточку навечно (Р-17, находка 36)
                self._busy(series_id, False)
            self.publish_event("renaming.finished", {"series_id": series_id})
            return {"renamed": renamed}

    def _busy(self, series_id: int, busy: bool) -> None:
        self.publish_event("series.busy.contribution", {
            "source": "renaming", "series_id": series_id, "busy": busy})

    # --- VK: файлы на диске --------------------------------------------------------------

    async def _reprocess_vk(self, series: dict) -> int:
        series_id, base = series["id"], series["save_path"]
        items = await self.request("scan.media.list",
                                   {"series_id": series_id})
        items_map = {i["unique_id"]: i for i in items}
        renamed = 0

        for item in items:
            if item.get("status") != "completed" \
                    or not item.get("final_filename") \
                    or not item.get("source_title"):
                continue
            old_rel = item["final_filename"]
            reply = await self.request("rules.format_filename", {
                "series": series, "media_item": item,
                "original_filename": old_rel})
            new_rel = reply["filename"]
            if new_rel == old_rel:
                continue
            if await self._rename_on_disk(base, old_rel, new_rel):
                renamed += 1
            # имя в БД обновляется в любом случае (поведение оригинала)
            self.send_command("downloads.item.set_filename", {
                "unique_id": item["unique_id"], "filename": new_rel})

        for child in await self._sliced_files(series_id):
            parent = items_map.get(child["source_media_item_unique_id"])
            if not parent:
                continue
            old_rel = child["file_path"]
            reply = await self.request("rules.format_filename", {
                "series": series, "media_item": parent,
                "episode_override": child["episode_number"],
                "original_filename": old_rel})
            new_rel = reply["filename"]
            if new_rel == old_rel:
                continue
            if await self._rename_on_disk(base, old_rel, new_rel):
                renamed += 1
            self.send_command("slicing.file.set_path", {
                "id": child["id"], "path": new_rel})
        return renamed

    async def _sliced_files(self, series_id: int) -> list[dict]:
        try:
            return await self.request("slicing.files.list",
                                      {"series_id": series_id})
        except BusRequestError as exc:
            if _NO_HANDLER in str(exc):
                self.log.warning("модуль slicing ещё не подключён — "
                                 "нарезанные файлы пропущены")
                return []
            raise

    async def _rename_on_disk(self, base: str, old_rel: str,
                              new_rel: str) -> bool:
        old_abs = os.path.join(base, old_rel)
        new_abs = os.path.join(base, new_rel)
        if await asyncio.to_thread(os.path.exists, new_abs):
            self.log.warning("файл с новым именем уже существует: %s",
                             new_abs)
            return False
        if not await asyncio.to_thread(os.path.exists, old_abs):
            self.log.error("исходный файл не найден: %s", old_abs)
            return False

        def _do() -> None:
            os.makedirs(os.path.dirname(new_abs), exist_ok=True)
            os.rename(old_abs, new_abs)

        await asyncio.to_thread(_do)
        self.log.info("переименован: %s -> %s", old_rel, new_rel)
        return True

    # --- торренты: файлы внутри qBittorrent ------------------------------------------------

    async def _reprocess_torrents(self, series: dict) -> int:
        renamed = 0
        for torrent in await self.request("torrents.db.active",
                                          {"series_id": series["id"]}):
            if torrent.get("qb_hash"):
                renamed += await self._process_torrent(series,
                                                       torrent["qb_hash"])
        return renamed

    async def on_process_torrent(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        series = await self.request("catalog.series.get",
                                    {"series_id": series_id})
        # C9: тот же per-series лок, что и у reprocess — иначе save во время
        # стадии RENAMING запускает ВТОРОЕ переименование тех же файлов
        # параллельно (гонка, кривые имена). Сериализуем.
        lock = self._locks.setdefault(series_id, asyncio.Lock())
        async with lock:
            renamed = await self._process_torrent(series,
                                                  env.payload["qb_hash"])
        return {"renamed": renamed}

    async def _process_torrent(self, series: dict, qb_hash: str) -> int:
        """Логика старого renaming_processor: правила по имени файла →
        'Season NN/строгое имя' → rename внутри qBittorrent."""
        files = await self.request("torrents.files.get", {"hash": qb_hash})
        if not files:
            self.log.warning("файлы раздачи %s не получены", qb_hash[:8])
            return 0
        db_files = await self.request("torrents.db.files.list",
                                      {"qb_hash": qb_hash})
        by_current = {(f.get("renamed_path") or f["original_path"]): f
                      for f in db_files}

        records, renamed = [], 0
        assigned: dict[str, str] = {}  # new_path -> original (детект коллизий)
        for f in files:
            current_path = f["name"] if isinstance(f, dict) else f
            if not current_path.lower().endswith(_VIDEO_EXT):
                continue
            db_record = by_current.get(current_path)
            original = (db_record["original_path"] if db_record
                        else current_path)
            reply = await self.request("rules.format_torrent_file", {
                "series": series,
                "file_basename": os.path.basename(original),
                "original_path": original})
            new_path = reply["filename"]
            if new_path is None:
                self.log.warning("сезон не определён для '%s' — файл "
                                 "пропущен", current_path)
                continue
            # Консистентность (находка 63): два файла, получивших ОДНО имя
            # (правила дали неуникальный результат) — это коллизия. qBit
            # может принять переименование в уже занятый путь и слить
            # файлы, вернув успех; БД тогда соврала бы «renamed». Не
            # сливаем — помечаем ошибкой явно, чтобы БД отражала реальность.
            prev = assigned.get(new_path)
            if prev is not None and prev != original:
                self.log.error("коллизия имён: '%s' и '%s' дают одно имя "
                               "'%s' — переименование пропущено (правила "
                               "неуникальны)", prev, original, new_path)
                records.append({"original_path": original,
                                "renamed_path": None,
                                "status": "rename_error",
                                "extracted_metadata": reply["extracted"]})
                continue
            assigned[new_path] = original
            status = "renamed"
            if new_path != current_path:
                try:
                    await self.request("torrents.rename_file", {
                        "hash": qb_hash, "old_path": current_path,
                        "new_path": new_path})
                    renamed += 1
                except BusRequestError as exc:
                    self.log.error("rename в qBit не удался ('%s' -> "
                                   "'%s'): %s", current_path, new_path, exc)
                    status, new_path = "rename_error", None
            records.append({"original_path": original,
                            "renamed_path": new_path, "status": status,
                            "extracted_metadata": reply["extracted"]})
        if records:
            await self.request("torrents.db.files.upsert",
                               {"qb_hash": qb_hash, "files": records})
        return renamed

    async def on_series_deleted(self, env):
        """Каскад Р-19: владелец чистит renaming_tasks."""
        await self.repo.delete_for_series(env.payload["series_id"])
