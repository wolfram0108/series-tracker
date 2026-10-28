"""Path translation between this server and the alignment daemon, which mounts the same storage elsewhere."""
from __future__ import annotations

DEFAULT_PATH_MAP = {"/nas": "/mnt/meganas/media"}


def translate(path: str, mapping: dict[str, str]) -> str:
    """`path` under the longest matching prefix of `mapping`, rewritten to its counterpart."""
    for prefix in sorted(mapping, key=len, reverse=True):
        if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
            return mapping[prefix].rstrip("/") + path[len(prefix.rstrip("/")):]
    raise ValueError(f"путь {path} вне общего хранилища демона")


def reverse(mapping: dict[str, str]) -> dict[str, str]:
    return {daemon: local for local, daemon in mapping.items()}
