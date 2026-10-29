"""Path translation between this server and the alignment daemon, which mounts the same storage elsewhere."""
from __future__ import annotations

from core.reasons import reason

DEFAULT_PATH_MAP = {"/nas": "/mnt/meganas/media"}


def translate(path: str, mapping: dict[str, str]) -> str:
    """`path` under the longest matching prefix of `mapping`, rewritten to its counterpart."""
    for prefix in sorted(mapping, key=len, reverse=True):
        if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
            return mapping[prefix].rstrip("/") + path[len(prefix.rstrip("/")):]
    raise ValueError(reason("path_outside_share", path=path))


def reverse(mapping: dict[str, str]) -> dict[str, str]:
    return {daemon: local for local, daemon in mapping.items()}
