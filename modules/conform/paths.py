"""Path translation between this server and the alignment daemon, which mounts the same storage elsewhere."""
from __future__ import annotations

import os

from core.reasons import reason

DEFAULT_PATH_MAP = {"/nas": "/mnt/meganas/media"}

# The folders the daemon is asked to write to and the one it leaves next to a reference video.
OUT_DIR = "_aligned"
CACHE_DIR = "_cache"
SCRATCH_DIR = "_tmp"


def workspace(dub_folder: str | None = None, reference_folder: str | None = None) -> dict[str, str]:
    """Where the daemon writes the tracks and keeps its cache for the voice-overs in `dub_folder`, and the
    scratch folder it leaves next to `reference_folder`; only what is asked for is answered."""
    found: dict[str, str] = {}
    if dub_folder:
        found["out_dir"] = os.path.join(dub_folder, OUT_DIR)
        found["cache_dir"] = os.path.join(dub_folder, CACHE_DIR)
    if reference_folder:
        found["scratch"] = os.path.join(reference_folder, SCRATCH_DIR)
    return found


def translate(path: str, mapping: dict[str, str]) -> str:
    """`path` under the longest matching prefix of `mapping`, rewritten to its counterpart."""
    for prefix in sorted(mapping, key=len, reverse=True):
        if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
            return mapping[prefix].rstrip("/") + path[len(prefix.rstrip("/")):]
    raise ValueError(reason("path_outside_share", path=path))


def reverse(mapping: dict[str, str]) -> dict[str, str]:
    return {daemon: local for local, daemon in mapping.items()}
