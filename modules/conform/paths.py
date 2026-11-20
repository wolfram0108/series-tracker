"""Path translation between this server and the alignment daemon, which mounts the same storage elsewhere.

The daemon may run on Windows (`Z:\\torrents\\x`, `\\\\nas\\media\\x`), and then it names the storage in its own style. A path of
the daemon is read by its own syntax, never by a setting: one place decides the style, and a location crosses the boundary in
both directions as the same location, whatever separators and letter case the daemon writes.
"""
from __future__ import annotations

import os
import re
from pathlib import PurePath, PurePosixPath, PureWindowsPath

from core.reasons import reason

# The two folders inside the work folder: voice-overs wait for alignment in the first, the service works in the second.
DOWNLOADS_DIR = "downloads"
CONFORM_DIR = "conform"
# The folders the daemon is asked to write to.
OUT_DIR = "_aligned"
CACHE_DIR = "_cache"

# A drive letter or a backslash share makes a path Windows; anything else is POSIX.
_WINDOWS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
# The top of a Windows storage without its closing separator: a drive (`Z:`) or a share (`\\server\share`).
_TOP = re.compile(r"(?:[A-Za-z]:|\\\\[^\\/]+[\\/][^\\/]+)")


def roots(work_dir: str | None) -> dict[str, str]:
    """The two folders inside the work folder; both empty while the owner has not named it."""
    if not work_dir:
        return {"downloads": "", "conform": ""}
    return {"downloads": os.path.join(work_dir, DOWNLOADS_DIR), "conform": os.path.join(work_dir, CONFORM_DIR)}


def workspace(dub_folder: str) -> dict[str, str]:
    """Where the daemon writes the tracks and keeps its cache for the episode folder `dub_folder`."""
    return {"out_dir": os.path.join(dub_folder, OUT_DIR), "cache_dir": os.path.join(dub_folder, CACHE_DIR)}


def _pure(path: str) -> PurePath:
    return PureWindowsPath(path) if _WINDOWS.match(path) else PurePosixPath(path)


def _sep(windows_path: str) -> str:
    """The separator the owner wrote the folder with: the paths made from it keep it."""
    return "\\" if "\\" in windows_path else "/"


def is_absolute(path: str) -> bool:
    """`path` is complete in its own style: `/x`, `Z:\\x` or `Z:/x`, `\\\\server\\share\\x`; `Z:x` and `x/y` are not."""
    return _pure(path).is_absolute()


def is_root(path: str) -> bool:
    """`path` is the top of a storage, nothing lies above it: `/`, `Z:\\`, `\\\\server\\share`."""
    return len(_pure(path).parts) <= 1


def folder(path: str) -> str:
    """A folder as it is stored: trimmed, trailing separators dropped, the top of a storage kept whole (`/`, `Z:\\`)."""
    text = path.strip()
    if _WINDOWS.match(text):
        bare = text.rstrip("\\/")
        return bare + _sep(text) if _TOP.fullmatch(bare) else bare
    return text.rstrip("/") or ("/" if text.startswith("/") else "")


def name_of(path: str) -> str:
    """The file name of a path of the daemon, whatever its style."""
    return _pure(path).name


def _below(path: str, prefix: str) -> tuple[str, ...] | None:
    """The parts of `path` below `prefix` (none when it is that very folder), None when it lies elsewhere. Both are read in
    the style of `prefix`: a Windows one ignores letter case and takes either separator."""
    style = type(_pure(prefix))
    try:
        return style(path).relative_to(style(prefix)).parts
    except ValueError:
        return None


def _join(root: str, parts: tuple[str, ...]) -> str:
    if not parts:
        return root
    if _WINDOWS.match(root):
        return root.rstrip("\\/") + _sep(root) + _sep(root).join(parts)
    return root.rstrip("/") + "/" + "/".join(parts)


def translate(path: str, mapping: dict[str, str]) -> str:
    """`path` under the longest matching prefix of `mapping`, rewritten to its counterpart."""
    for prefix in sorted(mapping, key=len, reverse=True):
        parts = _below(path, prefix)
        if parts is not None:
            return _join(mapping[prefix], parts)
    raise ValueError(reason("path_outside_share", path=path))


def reverse(mapping: dict[str, str]) -> dict[str, str]:
    return {daemon: local for local, daemon in mapping.items()}
