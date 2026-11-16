"""Модуль renaming — приведение имён файлов к строгому формату (Р-15).

Queries:
  renaming.reprocess {series_id} — массовая переобработка имён по виду
      сериала `kind` из catalog (контракт Р-12: сканер вызывает перед
      каждым сканом; тот же запрос — «сохранили свойства сериала в UI»,
      этап 5): video → имена файлов даёт план сериала (modules/planner); torrent →
      ревизия имён файлов всех активных раздач в qBit по профилю
      торрентного источника (без профиля — пропуск).
  renaming.process_torrent {series_id, qb_hash, dry_run?} —
      переименование файлов одной раздачи (контракт Р-14: стадия
      'renaming' конвейера). dry_run — только разбор состава правилами
      и запись в torrent_files (статус pending_rename), qBit не
      трогается: состав нужен до закачки, чтобы отобрать серии.

Имя готового файла видео-сериала — функция строки плана, но файлы переименовываются только в синхронизации: скан вызывает
renaming.reprocess после пересчёта плана, в том же проходе. Пересчёт плана по событию (правка правил, выключение серии)
файлы не трогает.

Событие renaming.finished {series_id} — контракт SSE renaming_complete.

Имена вычисляет rules (rules.format_filename / format_torrent_file —
Р-15, дифф 349/349 с реальными именами). Файлы видео-сериала принадлежат
downloads: здесь считаются имена всех готовых файлов, а переносит их владелец
(downloads.items.rename: обязательство записывается до переноса, кольцо
из обмениваемых имён разрывается, чужой файл не затирается, новое имя
объявляется после переноса). Rename в qBit — здесь. Чужие колонки — командами
владельцам: torrents.db.files.upsert.

Ресьюмабельность: запись renaming_tasks живёт на время работы
(и держит is_busy-семантику); незавершённые при старте перевыполняются
(операция идемпотентна: уже переименованное пропускается).
"""
from __future__ import annotations

import asyncio
import os
import re

from core import BaseModule, BusRequestError, storage, torrent_profile_id
from core.contracts import SeriesKind
from core.db import Database
from core.envelope import Envelope

from .repository import RenamingRepository

# What became of a file the renaming was to move.
MOVED, ABSENT, BLOCKED = "moved", "absent", "blocked"
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
        self.handle("series.deleted", self.on_series_deleted)
        self.handle("system.started", self.on_system_started)

    async def on_system_started(self, env: Envelope) -> None:
        """Every module is up: a переобработка, прерванная рестартом, выполняется заново — ей нужны соседи, которые на
        старте этого модуля ещё не отвечают."""
        for task in await self.repo.unfinished():
            self.log.info("перевыполнение незавершённой переобработки "
                          "(задача %d, сериал %d)", task["id"],
                          task["series_id"])
            self.spawn(self._safe_reprocess(task["series_id"], resume_of=task["id"]))

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
            video = series["kind"] == SeriesKind.VIDEO
            # Video names come from the numbers of the rows; only a torrent file is named by a profile.
            if not video and torrent_profile_id(series) is None:
                self.log.warning("переобработка %d пропущена: профиль "
                                 "правил не назначен", series_id)
                return {"renamed": 0, "skipped": True}

            await self.repo.delete_errors(series_id)
            task_type = ("mass_vk_reprocess" if video
                         else "mass_torrent_reprocess")
            task_id = await self.repo.create(series_id, task_type)
            self._busy(series_id, True)
            try:
                if video:
                    renamed = 0  # the files of a video series take their names from its plan (modules/planner)
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

    async def _rename_on_disk(self, base: str, old_rel: str,
                              new_rel: str) -> str:
        """MOVED: the file lies under the new name now; ABSENT: there is no file to move; BLOCKED: the new name is taken
        by another file, which is left alone, and the file keeps its name."""
        old_abs = os.path.join(base, old_rel)
        new_abs = os.path.join(base, new_rel)
        if await storage.call(os.path.exists, new_abs):
            self.log.warning("файл с новым именем уже существует: %s",
                             new_abs)
            return BLOCKED
        if not await storage.call(os.path.exists, old_abs):
            self.log.error("исходный файл не найден: %s", old_abs)
            return ABSENT

        def _do() -> None:
            os.makedirs(os.path.dirname(new_abs), exist_ok=True)
            os.rename(old_abs, new_abs)

        await storage.call(_do, owns=(old_abs, new_abs))
        self.log.info("переименован: %s -> %s", old_rel, new_rel)
        return MOVED


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
        profile_id = torrent_profile_id(series)
        video_items = [it for it in all_items if it["is_video"]]
        for v in video_items:
            reply = await self.request("rules.format_torrent_file", {
                "series": series,
                "profile_id": profile_id,
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
        self.publish_event("series.purged",
                           {"series_id": env.payload["series_id"],
                            "owner": self.name})
