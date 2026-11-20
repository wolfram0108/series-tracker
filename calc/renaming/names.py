"""Pure naming helpers of the renaming of the files of a torrent (the old pipeline: it leaves with step 11 of stage 15)."""
from __future__ import annotations

import re

VIDEO_EXT = (".mkv", ".avi", ".mp4", ".mov", ".wmv", ".webm", ".ts", ".m4v")
AUDIO_EXT = (".mka", ".ac3", ".dts", ".aac", ".flac", ".wav", ".ogg", ".eac3", ".m4a")
SUB_EXT = (".ass", ".srt", ".vtt", ".sub")


def extract_torrent_root(paths: list[str]) -> str | None:
    """The name of the folder every file lies in when they all share one top-level folder (the standard folder of a
    multi-file torrent); otherwise none."""
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


def folder_tag(rel_dir: str) -> str:
    """The path of a subfolder as a tag for the name of a track:
    'RUS Sound/AniLiberty' -> 'RUS_Sound_AniLiberty'
    '/rus/AniLiberty/' -> 'rus_AniLiberty'
    """
    cleaned = rel_dir.replace("\\", "/").strip("/")
    if not cleaned:
        return ""
    tag = re.sub(r"[/\\ ]+", "_", cleaned)
    return re.sub(r"_+", "_", tag).strip("_")
