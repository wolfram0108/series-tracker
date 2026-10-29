"""The form in which a finished voice-over reaches the library, behind one contract.

The module decides when the artifact of a voice-over is made, moved after its video, removed or recovered
after an interruption, and writes the intent down first; an `Output` decides what that means for the files.
The only form today is a track next to the video (`SidecarOutput`); another form is another class with the
same four operations. The artifact of one voice-over is the unit: a form that joins the video and all its
tracks in one container needs a contract of the episode, which no stage asks for yet.
"""
from __future__ import annotations

import os
from typing import Collection, Protocol

from .placement import move, sidecar_path


class Output(Protocol):
    def destination(self, reference: str, label: str, source: str, taken: Collection[str]) -> str:
        """Where the artifact of a voice-over lives for this video; `source` is the file it is made from."""

    def exists(self, artifact: str) -> bool: ...

    def settle(self, source: str, destination: str) -> bool:
        """Make the artifact at `destination` from `source` (an aligned track, or the artifact of the video
        before it was renamed); a reader never sees it half made, doing it twice is doing it once, and False
        means the artifact is in neither place."""

    def remove(self, artifact: str) -> None: ...


class SidecarOutput:
    """An external audio track next to the video, named the way Jellyfin reads it."""

    def destination(self, reference: str, label: str, source: str, taken: Collection[str]) -> str:
        return sidecar_path(reference, label, os.path.splitext(source)[1] or ".flac", set(taken))

    def exists(self, artifact: str) -> bool:
        return os.path.isfile(artifact)

    def settle(self, source: str, destination: str) -> bool:
        if os.path.isfile(source):
            move(source, destination)
            return True
        return os.path.isfile(destination)

    def remove(self, artifact: str) -> None:
        os.remove(artifact)
