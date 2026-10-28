"""The form in which a finished voice-over reaches the library, behind one contract.

The module decides when a voice-over is delivered, followed after its video, removed or recovered after an
interruption; an `Output` decides what that means for the files. The only form today is a track next to the
video (`SidecarOutput`); another form is another class with the same five operations.
"""
from __future__ import annotations

import os
from typing import Collection, Protocol

from .placement import move, sidecar_path


class Output(Protocol):
    def destination(self, reference: str, label: str, source: str, taken: Collection[str]) -> str:
        """Where the artifact of a voice-over lives for this video; `source` is the file it is made from."""

    def exists(self, artifact: str) -> bool: ...

    def deliver(self, source: str, destination: str) -> None:
        """Make the artifact from an aligned track; a reader never sees it half made."""

    def follow(self, artifact: str, destination: str) -> None:
        """Move the artifact after its video."""

    def settle(self, source: str, destination: str) -> bool:
        """Finish a delivery or a move that was interrupted; False when the artifact is lost."""

    def remove(self, artifact: str) -> None: ...


class SidecarOutput:
    """An external audio track next to the video, named the way Jellyfin reads it."""

    def destination(self, reference: str, label: str, source: str, taken: Collection[str]) -> str:
        return sidecar_path(reference, label, os.path.splitext(source)[1] or ".flac", set(taken))

    def exists(self, artifact: str) -> bool:
        return os.path.isfile(artifact)

    def deliver(self, source: str, destination: str) -> None:
        move(source, destination)

    def follow(self, artifact: str, destination: str) -> None:
        move(artifact, destination)

    def settle(self, source: str, destination: str) -> bool:
        if os.path.isfile(source):
            move(source, destination)
            return True
        return os.path.isfile(destination)

    def remove(self, artifact: str) -> None:
        os.remove(artifact)
