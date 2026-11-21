"""What the module `unpack` tells of a torrent page: its layout changed, how far a release is read."""
from __future__ import annotations

from .frozen import Frozen


class PageChanged(Frozen):
    """The events `unpack.table.changed` and `unpack.editor.changed`: what the layout of the page holds changed (a read ended,
    a profile of its tabs changed, a placement was made), so a window that shows the page asks for it again."""

    url: str


class ReleaseReadProgress(Frozen):
    """The event `unpack.read.progress`: how many files of the release of the page are read (the tracks of a file are read from the
    file itself, a read of hours for a big release)."""

    url: str
    release: str
    done: int
    total: int
