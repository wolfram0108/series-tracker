"""Path translation between this server and the alignment daemon, which mounts the same storage elsewhere."""
from __future__ import annotations

import os

from core.reasons import reason

# The two folders inside the work folder: voice-overs wait for alignment in the first, the service works in the second.
DOWNLOADS_DIR = "downloads"
CONFORM_DIR = "conform"
# The folders the daemon is asked to write to.
OUT_DIR = "_aligned"
CACHE_DIR = "_cache"


def roots(work_dir: str | None) -> dict[str, str]:
    """The two folders inside the work folder; both empty while the owner has not named it."""
    if not work_dir:
        return {"downloads": "", "conform": ""}
    return {"downloads": os.path.join(work_dir, DOWNLOADS_DIR), "conform": os.path.join(work_dir, CONFORM_DIR)}


def workspace(dub_folder: str) -> dict[str, str]:
    """Where the daemon writes the tracks and keeps its cache for the episode folder `dub_folder`."""
    return {"out_dir": os.path.join(dub_folder, OUT_DIR), "cache_dir": os.path.join(dub_folder, CACHE_DIR)}


def translate(path: str, mapping: dict[str, str]) -> str:
    """`path` under the longest matching prefix of `mapping`, rewritten to its counterpart."""
    for prefix in sorted(mapping, key=len, reverse=True):
        if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
            return mapping[prefix].rstrip("/") + path[len(prefix.rstrip("/")):]
    raise ValueError(reason("path_outside_share", path=path))


def reverse(mapping: dict[str, str]) -> dict[str, str]:
    return {daemon: local for local, daemon in mapping.items()}
