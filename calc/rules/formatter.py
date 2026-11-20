"""Форматирование имён файлов медиатеки — чистые функции (Р-15).

Numbers arrive ready-made (from the constructor via the layout, or from the torrent card);
the name never applies the rules a second time.

Формат: "Name_EN sNNeMM [озвучка] качество 1080p.ext"
"""
from __future__ import annotations

import json
import os
import re

from core.fsnames import sanitize_name as sanitize

from . import season as season_rule


def merge_series_defaults(series: dict, metadata: dict,
                          extracted: dict | None = None) -> dict:
    """Карточка сериала — дефолт: правило, сработавшее на этом файле, важнее.

    Тот же принцип, что у сезона (calc/rules/season.py): извлечённое побеждает,
    значение карточки используется, только если правило ничего не дало.
    """
    extracted = extracted or {}
    for key, override in (("quality", series.get("quality_override")),
                          ("resolution", series.get("resolution_override"))):
        if override and key not in extracted:
            metadata[key] = override
    metadata["season"] = season_rule.resolve(series.get("season"),
                                             metadata.get("season"))
    return metadata


def build_metadata(series: dict, media_item: dict) -> dict:
    """Numbers and facts of the stored item, then the series card as a default."""
    first, last = media_item.get("episode_start"), media_item.get("episode_end")
    # The layout stores a single episode with end == start, other rows keep NULL: both mean no range.
    if last is not None and first is not None and last <= first:
        last = None
    metadata = {
        "season": media_item.get("season"),
        "episode": first if last is None else None,
        "start": first if last is not None else None,
        "end": last,
        "part": media_item.get("part"),
        "resolution": media_item.get("resolution"),
        "voiceover": media_item.get("voiceover_tag"),
    }
    # A video cut out of a compilation (stage 12) is named by the compilation: its voice-over and its resolution.
    cut = media_item.get("cut")
    if isinstance(cut, str) and cut:
        try:
            cut = json.loads(cut)
        except ValueError:
            cut = None
    if isinstance(cut, dict):
        metadata["resolution"] = cut.get("height") or metadata["resolution"]
        metadata["voiceover"] = cut.get("voice_group") or metadata["voiceover"]
    return merge_series_defaults(series, metadata)


def _season_number(series: dict, metadata: dict) -> int:
    season = season_rule.resolve(series.get("season"), metadata.get("season"))
    if isinstance(season, str):
        m = re.search(r"\d+", season)
        return int(m.group(0)) if m else 1
    return season if season is not None else 1


def format_name(series: dict, metadata: dict,
                original_filename: str | None = None,
                target_directory: str | None = None) -> str:
    """Полное воспроизведение старого FilenameFormatter.format_filename.

    metadata — сводные метаданные файла; значения карточки сериала уже
    подставлены в них (merge_series_defaults), здесь только форматирование.
    """
    name_en = sanitize(series.get("name_en") or "Unknown Series")
    season_part = f"s{str(_season_number(series, metadata)).zfill(2)}"

    episode = metadata.get("episode")
    start, end = metadata.get("start"), metadata.get("end")
    part = metadata.get("part")
    part_suffix = f" Part {part}" if part is not None else ""

    episode_part = ""
    if episode is not None:
        episode_part = f"e{str(episode).zfill(2)}{part_suffix}"
    elif start is not None and end is not None and end > start:
        episode_part = f"e{str(start).zfill(2)}-e{str(end).zfill(2)}{part_suffix}"
    elif start is not None:
        episode_part = f"e{str(start).zfill(2)}{part_suffix}"

    voiceover = metadata.get("voiceover")
    quality = metadata.get("quality")
    resolution = metadata.get("resolution")

    parts = [name_en, f"{season_part}{episode_part}"]
    if voiceover:
        parts.append(f"[{sanitize(voiceover)}]")
    if quality:
        parts.append(sanitize(quality))
    if resolution:
        resolution_str = str(resolution)
        parts.append(f"{resolution_str}p" if resolution_str.isdigit()
                     else sanitize(resolution_str))

    # A file keeps its own extension; a name of no file yet is the one of the container of an episode.
    extension = (os.path.splitext(original_filename)[1].lower() if original_filename else "") or ".mkv"

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
    season_number = season_rule.resolve(series.get("season"),
                                        (extracted or {}).get("season"))
    if season_number is not None:
        return season_number
    if series.get("season"):  # односезонник с нечитаемым номером в карточке
        return None
    if "specials" in os.path.dirname(original_path).lower():
        return 0
    return 1
