"""Форматирование имён файлов медиатеки — чистые функции (Р-15).

Имя файла — итоговый артефакт системы; поведение воспроизводит старые
FilenameFormatter + build_final_metadata + сезонную логику
renaming_processor 1:1 (дифф-верификация на реальных именах фикстуры —
tests/test_rules_format_diff.py).

Формат: "Name_EN sNNeMM [озвучка] качество 1080p.ext"
"""
from __future__ import annotations

import os
import re


def sanitize(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "", str(name))
    return re.sub(r"\s+", " ", name).strip()


def build_metadata(series: dict, media_item: dict, extracted: dict) -> dict:
    """Иерархия: овер-райды сериала > данные правил > факты БД."""
    metadata = {
        "season": media_item.get("season"),
        "episode": (media_item.get("episode_start")
                    if not media_item.get("episode_end") else None),
        "start": (media_item.get("episode_start")
                  if media_item.get("episode_end") else None),
        "end": media_item.get("episode_end"),
        "resolution": media_item.get("resolution"),
        "voiceover": media_item.get("voiceover_tag"),
    }
    for key, value in (extracted or {}).items():
        if value is not None:
            metadata[key] = value
    if series.get("quality_override"):
        metadata["quality"] = series["quality_override"]
    if series.get("resolution_override"):
        metadata["resolution"] = series["resolution_override"]
    if series.get("season"):  # односезонник: сезон сериала бьёт всё
        metadata["season"] = series["season"]
    return metadata


def _season_number(series: dict, metadata: dict) -> int:
    series_season = series.get("season")
    if series_season:
        m = re.search(r"\d+", str(series_season))
        return int(m.group(0)) if m else 1
    season = metadata.get("season", 1)
    if isinstance(season, str):
        m = re.search(r"\d+", season)
        return int(m.group(0)) if m else 1
    return season if season is not None else 1


def format_name(series: dict, metadata: dict,
                original_filename: str | None = None,
                target_directory: str | None = None) -> str:
    """Полное воспроизведение старого FilenameFormatter.format_filename."""
    name_en = sanitize(series.get("name_en") or "Unknown Series")
    season_part = f"s{str(_season_number(series, metadata)).zfill(2)}"

    episode = metadata.get("episode")
    start, end = metadata.get("start"), metadata.get("end")
    episode_part = ""
    if episode is not None:
        episode_part = f"e{str(episode).zfill(2)}"
    elif start is not None and end is not None:
        episode_part = f"e{str(start).zfill(2)}-e{str(end).zfill(2)}"
    elif start is not None:
        episode_part = f"e{str(start).zfill(2)}"

    voiceover = metadata.get("voiceover")
    quality = series.get("quality_override") or metadata.get("quality")
    resolution = (series.get("resolution_override")
                  or metadata.get("resolution"))

    parts = [name_en, f"{season_part}{episode_part}"]
    if voiceover:
        parts.append(f"[{sanitize(voiceover)}]")
    if quality:
        parts.append(sanitize(quality))
    if resolution:
        resolution_str = str(resolution)
        parts.append(f"{resolution_str}p" if resolution_str.isdigit()
                     else sanitize(resolution_str))

    extension = ".mkv"
    if series.get("source_type") == "vk_video":
        extension = ".mp4"
    elif original_filename:
        ext = os.path.splitext(original_filename)[1]
        if ext:
            extension = ext.lower()

    basename = " ".join(filter(None, parts)) + extension

    if target_directory:
        return os.path.join(target_directory, basename).replace("\\", "/")
    if original_filename:
        original_dir = os.path.split(original_filename)[0]
        if original_dir:
            return os.path.join(original_dir, basename).replace("\\", "/")
    return basename


def torrent_season_folder(series: dict, extracted: dict,
                          original_path: str) -> int | None:
    """Сезонная логика renaming_processor: номер сезона для папки
    'Season NN' или None («сезон не определён, файл пропустить» —
    возможно только у односезонника с нечитаемым series.season)."""
    season_number = (extracted or {}).get("season")
    if series.get("season"):  # односезонник: сезон из свойств сериала
        m = re.search(r"\d+", str(series["season"]).strip())
        if m:
            return int(m.group())
        return season_number  # как в оригинале: не разобрали — extracted
    if season_number is None:  # многосезонник без сезона в имени
        if "specials" in os.path.dirname(original_path).lower():
            return 0
        return 1
    return season_number
