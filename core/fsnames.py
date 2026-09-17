"""Filesystem-safe names: the single rule for every string that becomes a
path component (file name, directory, save path).

The forbidden set is the Windows/SMB one: the library is served to clients
that cannot open names with ':' or '?' even though XFS itself allows them.
"""
from __future__ import annotations

import re

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_SPACES = re.compile(r"\s+")


def sanitize_name(name: str) -> str:
    """Single component: forbidden characters dropped, whitespace collapsed
    and trimmed. Whitespace is collapsed before the drop so tabs and
    newlines still count as separators instead of vanishing."""
    text = _SPACES.sub(" ", str(name))
    return _SPACES.sub(" ", _FORBIDDEN.sub("", text)).strip()


def sanitize_path(path: str) -> str:
    """Slash-separated path: backslashes become slashes, every component
    goes through sanitize_name. Empty components (leading or trailing
    slash) are kept so the path shape is preserved."""
    parts = str(path).replace("\\", "/").split("/")
    return "/".join(sanitize_name(part) for part in parts)
