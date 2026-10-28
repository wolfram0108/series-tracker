"""Where an aligned voice-over goes: next to the reference video, named the way Jellyfin reads external audio."""
from __future__ import annotations

import os
import re
import shutil

SIDECAR_LANG = "ru"
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f.]+')


def track_title(label: str) -> str:
    """A voice-over title that survives as one name segment: no dots, no path characters."""
    return re.sub(r"\s+", " ", _FORBIDDEN.sub(" ", label)).strip() or "dub"


def sidecar_path(reference: str, label: str, extension: str, taken: frozenset[str] | set[str] = frozenset()) -> str:
    """Path of a track next to its video; a title that another track of the video already has gets a number."""
    stem, _ = os.path.splitext(reference)
    title, number = track_title(label), 1
    while True:
        path = f"{stem}.{title}{'' if number == 1 else f' {number}'}.{SIDECAR_LANG}{extension}"
        if path not in taken:
            return path
        number += 1


def move(source: str, destination: str) -> None:
    """Move a track after its video: a rename inside one directory, a copy across directories."""
    if os.path.dirname(source) == os.path.dirname(destination):
        os.replace(source, destination)
    else:
        place(source, destination)


def place(source: str, destination: str) -> None:
    """Move a file so that a reader never sees it half written."""
    partial = f"{destination}.part"
    try:
        shutil.copyfile(source, partial)
        os.replace(partial, destination)
    finally:
        if os.path.exists(partial):
            os.remove(partial)
    os.remove(source)
