"""Module renaming — the names of the files of the releases of the old torrent pipeline inside qBittorrent (R-15; it
leaves with step 11 of stage 15).

Commands:
  renaming.process_torrent {series_id, qb_hash, dry_run?} -> {renamed} — the files of one release are renamed (contract R-14: the
      stage 'renaming' of the pipeline). dry_run only lays the release out by the rules and writes torrent_files
      (status pending_rename) without touching qBit: the files are needed before the download to pick the episodes.

The names come from rules (rules.format_torrent_file — R-15, 349/349 against the real names); the rename in qBit is
done here. The columns of others are commands to their owners: torrents.db.files.upsert. The module keeps no table of
its own; the operation is idempotent: what is renamed already is skipped.
"""
from __future__ import annotations

import asyncio
import os

from calc.renaming.names import AUDIO_EXT, SUB_EXT, VIDEO_EXT, extract_torrent_root, folder_tag
from core.contracts import SeriesCard
from core.module import BaseModule, BusRequestError
from core.source_types import torrent_profile_id
from core.topics import (CATALOG_SERIES_GET, RENAMING_PROCESS_TORRENT, RULES_FORMAT_TORRENT_FILE, TORRENTS_DB_FILES_LIST,
                         TORRENTS_DB_FILES_UPSERT, TORRENTS_FILES_GET, TORRENTS_RENAME_FILE)
from core.envelope import Envelope


class RenamingModule(BaseModule):
    name = "renaming"

    def __init__(self, bus) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle(RENAMING_PROCESS_TORRENT, self.on_process_torrent, concurrent=True)

    # --- torrents: the files inside qBittorrent ---------------------------------------------

    async def on_process_torrent(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
        # C9: one lock per series, so that two stages of the pipeline never rename the same files at once.
        lock = self._locks.setdefault(series_id, asyncio.Lock())
        async with lock:
            renamed = await self._process_torrent(
                series, env.payload["qb_hash"],
                dry_run=bool(env.payload.get("dry_run")))
        return {"renamed": renamed}

    async def _process_torrent(self, series: SeriesCard, qb_hash: str, *,
                               dry_run: bool = False) -> int:
        """The logic of the old renaming_processor: the rules by the name of a file ->
        'Season NN/strict name' -> rename inside qBittorrent.

        dry_run is the same walk by the same rules without touching qBit: the content of
        the torrent reaches the base before the download, so that the user can pick the
        episodes while the torrent is paused."""
        files = await self.request(TORRENTS_FILES_GET, {"hash": qb_hash})
        if not files:
            self.log.warning("файлы раздачи %s не получены", qb_hash[:8])
            return 0
        db_files = await self.request(TORRENTS_DB_FILES_LIST, {"qb_hash": qb_hash})
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
                "is_video": original.lower().endswith(VIDEO_EXT),
            })

        torrent_root = extract_torrent_root([it["original_path"] for it in all_items])
        for it in all_items:
            orig = it["original_path"].replace("\\", "/")
            if torrent_root and orig.startswith(torrent_root + "/"):
                it["rel_path"] = orig[len(torrent_root) + 1:]
            else:
                it["rel_path"] = orig
            it["rel_dir"] = os.path.dirname(it["rel_path"])

        records, renamed = [], 0
        assigned: dict[str, str] = {}  # new_path -> original (to detect collisions)
        matched_videos: list[dict] = []

        # 1. The video files first
        profile_id = torrent_profile_id(series)
        card = series.model_dump(mode="json")  # the rules read the card by key
        video_items = [it for it in all_items if it["is_video"]]
        for v in video_items:
            reply = await self.request(RULES_FORMAT_TORRENT_FILE, {
                "series": card,
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
                    await self.request(TORRENTS_RENAME_FILE, {
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

        # 2. Then the other files (voice-over tracks, subtitles, fonts, folders of extras)
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
                tag = folder_tag(item["rel_dir"])
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
                if ext_lower in AUDIO_EXT:
                    meta["media_type"] = "audio"
                elif ext_lower in SUB_EXT:
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
                    await self.request(TORRENTS_RENAME_FILE, {
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
            await self.request(TORRENTS_DB_FILES_UPSERT, {"qb_hash": qb_hash, "files": records})
        return renamed
