"""Модуль renaming — приведение имён файлов к строгому формату (Р-15).

Queries:
  renaming.reprocess {series_id} — массовая переобработка имён по типу
      сериала (контракт Р-12: сканер вызывает перед каждым сканом; тот
      же запрос — «сохранили свойства сериала в UI», этап 5):
      vk_video → переименование скачанных файлов и нарезанных детей на
      диске; torrent → ревизия имён файлов всех активных раздач в qBit.
  renaming.process_torrent {series_id, qb_hash, dry_run?} —
      переименование файлов одной раздачи (контракт Р-14: стадия
      'renaming' конвейера). dry_run — только разбор состава правилами
      и запись в torrent_files (статус pending_rename), qBit не
      трогается: состав нужен до закачки, чтобы отобрать серии.

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
import re

from core import BaseModule, BusRequestError
from core.db import Database
from core.envelope import Envelope

from .repository import RenamingRepository

_NO_HANDLER = "нет обработчика"
_VIDEO_EXT = (".mkv", ".avi", ".mp4", ".mov", ".wmv", ".webm", ".ts", ".m4v")
_AUDIO_EXT = (".mka", ".ac3", ".dts", ".aac", ".flac", ".wav", ".ogg", ".eac3", ".m4a")
_SUB_EXT = (".ass", ".srt", ".vtt", ".sub")


def _extract_torrent_root(paths: list[str]) -> str | None:
    """Если все файлы лежат внутри одной общей директории верхнего уровня
    (стандартная папка многофайлового торрента), возвращает её имя.
    Иначе None."""
    roots = set()
    for p in paths:
        parts = p.replace("\\", "/").strip("/").split("/")
        if len(parts) > 1:
            roots.add(parts[0])
        else:
            return None
    if len(roots) == 1:
        return next(iter(roots))
    return None


def _folder_tag(rel_dir: str) -> str:
    """Нормализует путь подкаталога в тег для имени дорожки:
    'RUS Sound/AniLiberty' -> 'RUS_Sound_AniLiberty'
    '/rus/AniLiberty/' -> 'rus_AniLiberty'
    """
    cleaned = rel_dir.replace("\\", "/").strip("/")
    if not cleaned:
        return ""
    tag = re.sub(r"[/\\ ]+", "_", cleaned)
    return re.sub(r"_+", "_", tag).strip("_")


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
                         if series["source_type"] in ("vk_video", "web_video")
                         else "mass_torrent_reprocess")
            task_id = await self.repo.create(series_id, task_type)
            self._busy(series_id, True)
            try:
                if series["source_type"] in ("vk_video", "web_video"):
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
            renamed = await self._process_torrent(
                series, env.payload["qb_hash"],
                dry_run=bool(env.payload.get("dry_run")))
        return {"renamed": renamed}

    async def _process_torrent(self, series: dict, qb_hash: str, *,
                               dry_run: bool = False) -> int:
        """Логика старого renaming_processor: правила по имени файла →
        'Season NN/строгое имя' → rename внутри qBittorrent.

        dry_run — тот же обход и те же правила, но без касания qBit:
        состав раздачи попадает в БД до закачки, чтобы пользователь мог
        отобрать серии, пока торрент стоит на паузе."""
        files = await self.request("torrents.files.get", {"hash": qb_hash})
        if not files:
            self.log.warning("файлы раздачи %s не получены", qb_hash[:8])
            return 0
        db_files = await self.request("torrents.db.files.list",
                                      {"qb_hash": qb_hash})
        by_current = {(f.get("renamed_path") or f["original_path"]): f
                      for f in db_files}

        all_items = []
        for position, f in enumerate(files):
            current_path = f["name"] if isinstance(f, dict) else f
            file_index = (f.get("index", position) if isinstance(f, dict)
                          else position)
            db_record = by_current.get(current_path)
            original = (db_record["original_path"] if db_record
                        else current_path)
            all_items.append({
                "current_path": current_path,
                "file_index": file_index,
                "original_path": original,
                "basename": os.path.basename(original),
                "is_video": original.lower().endswith(_VIDEO_EXT),
            })

        torrent_root = _extract_torrent_root([it["original_path"] for it in all_items])
        for it in all_items:
            orig = it["original_path"].replace("\\", "/")
            if torrent_root and orig.startswith(torrent_root + "/"):
                it["rel_path"] = orig[len(torrent_root) + 1:]
            else:
                it["rel_path"] = orig
            it["rel_dir"] = os.path.dirname(it["rel_path"])

        records, renamed = [], 0
        assigned: dict[str, str] = {}  # new_path -> original (детект коллизий)
        matched_videos: list[dict] = []

        # 1. Сначала обрабатываем видеофайлы
        video_items = [it for it in all_items if it["is_video"]]
        for v in video_items:
            reply = await self.request("rules.format_torrent_file", {
                "series": series,
                "file_basename": v["basename"],
                "original_path": v["original_path"]})
            new_path = reply["filename"]
            if new_path is None:
                self.log.warning("сезон не определён для '%s' — файл "
                                 "пропущен", v["current_path"])
                continue

            prev = assigned.get(new_path)
            if prev is not None and prev != v["original_path"]:
                self.log.error("коллизия имён: '%s' и '%s' дают одно имя "
                               "'%s' — переименование пропущено (правила "
                               "неуникальны)", prev, v["original_path"], new_path)
                records.append({"original_path": v["original_path"],
                                "renamed_path": None,
                                "status": "rename_error",
                                "qb_file_index": v["file_index"],
                                "extracted_metadata": reply["extracted"]})
                continue

            assigned[new_path] = v["original_path"]
            meta = dict(reply["extracted"] or {})
            meta["media_type"] = "video"
            meta["target_path"] = new_path

            target_dir = os.path.dirname(new_path)
            target_stem = os.path.splitext(os.path.basename(new_path))[0]
            video_stem = os.path.splitext(v["basename"])[0]
            matched_videos.append({
                "video_stem": video_stem,
                "target_dir": target_dir,
                "target_stem": target_stem,
                "extracted": reply["extracted"],
                "new_path": new_path,
            })

            status = "renamed"
            if dry_run:
                records.append({"original_path": v["original_path"],
                                "renamed_path": None,
                                "status": "pending_rename",
                                "qb_file_index": v["file_index"],
                                "extracted_metadata": meta})
                continue

            if new_path != v["current_path"]:
                try:
                    await self.request("torrents.rename_file", {
                        "hash": qb_hash, "old_path": v["current_path"],
                        "new_path": new_path})
                    renamed += 1
                except BusRequestError as exc:
                    self.log.error("rename в qBit не удался ('%s' -> "
                                   "'%s'): %s", v["current_path"], new_path, exc)
                    status, new_path = "rename_error", None
            records.append({"original_path": v["original_path"],
                            "renamed_path": new_path, "status": status,
                            "qb_file_index": v["file_index"],
                            "extracted_metadata": meta})

        # 2. Обрабатываем не-видео файлы (дорожки озвучки, субтитры, шрифты, каталоги экстра)
        non_video_items = [it for it in all_items if not it["is_video"]]
        sorted_videos = sorted(matched_videos, key=lambda x: len(x["video_stem"]), reverse=True)

        for item in non_video_items:
            base = item["basename"]
            matched_v = None
            for mv in sorted_videos:
                stem = mv["video_stem"]
                if base.startswith(stem):
                    rest = base[len(stem):]
                    if not rest or rest[0] in (".", "_", "-", " "):
                        matched_v = mv
                        break

            if matched_v is not None:
                tag = _folder_tag(item["rel_dir"])
                ext = os.path.splitext(base)[1]
                target_dir = matched_v["target_dir"]
                target_stem = matched_v["target_stem"]

                rest_tag = ""
                stem = matched_v["video_stem"]
                if len(base) > len(stem):
                    remainder = base[len(stem):]
                    if remainder.lower().endswith(ext.lower()):
                        remainder = remainder[:-len(ext)]
                    rest_tag = remainder.strip("._- ")

                if tag:
                    new_path = f"{target_dir}/{target_stem}.{tag}{ext}"
                elif rest_tag:
                    new_path = f"{target_dir}/{target_stem}.{rest_tag}{ext}"
                else:
                    new_path = f"{target_dir}/{target_stem}{ext}"

                if new_path in assigned and assigned[new_path] != item["original_path"]:
                    if rest_tag and tag and rest_tag not in tag:
                        new_path = f"{target_dir}/{target_stem}.{tag}_{rest_tag}{ext}"

                meta = dict(matched_v["extracted"] or {})
                ext_lower = ext.lower()
                if ext_lower in _AUDIO_EXT:
                    meta["media_type"] = "audio"
                elif ext_lower in _SUB_EXT:
                    meta["media_type"] = "subtitles"
                else:
                    meta["media_type"] = "extra"
                meta["target_path"] = new_path
            elif item["rel_dir"]:
                new_path = item["rel_path"]
                meta = {"is_extra": True, "media_type": "extra", "target_path": new_path}
            else:
                continue

            prev = assigned.get(new_path)
            if prev is not None and prev != item["original_path"]:
                self.log.error("коллизия имён: '%s' и '%s' дают одно имя "
                               "'%s' — переименование пропущено", prev,
                               item["original_path"], new_path)
                records.append({"original_path": item["original_path"],
                                "renamed_path": None,
                                "status": "rename_error",
                                "qb_file_index": item["file_index"],
                                "extracted_metadata": meta})
                continue

            assigned[new_path] = item["original_path"]
            status = "renamed"
            if dry_run:
                records.append({"original_path": item["original_path"],
                                "renamed_path": None,
                                "status": "pending_rename",
                                "qb_file_index": item["file_index"],
                                "extracted_metadata": meta})
                continue

            if new_path != item["current_path"]:
                try:
                    await self.request("torrents.rename_file", {
                        "hash": qb_hash, "old_path": item["current_path"],
                        "new_path": new_path})
                    renamed += 1
                except BusRequestError as exc:
                    self.log.error("rename в qBit не удался ('%s' -> "
                                   "'%s'): %s", item["current_path"], new_path, exc)
                    status, new_path = "rename_error", None
            records.append({"original_path": item["original_path"],
                            "renamed_path": new_path, "status": status,
                            "qb_file_index": item["file_index"],
                            "extracted_metadata": meta})

        if records:
            await self.request("torrents.db.files.upsert",
                               {"qb_hash": qb_hash, "files": records})
        return renamed

    async def on_series_deleted(self, env):
        """Каскад Р-19: владелец чистит renaming_tasks."""
        await self.repo.delete_for_series(env.payload["series_id"])
