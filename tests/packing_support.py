"""A packer for the tests whose videos are not media (the file holds the address it was fetched from): a container is
the bytes of its video, and what was packed into it is kept by the file's inode, as the tags of a real one would be."""
from __future__ import annotations

import os
from typing import Sequence

from core.container import AudioIn, AudioOut, Contents


class FakePacker:
    def __init__(self) -> None:
        self.made: dict[int, Contents] = {}
        self.builds: list[tuple[str, str, list[str]]] = []
        self.keys: list[list[str]] = []  # the keys of the tracks of every build, in order

    async def build(self, output: str, video: str, video_identity: str, tracks: Sequence[AudioIn], *,
                    publication: str = "") -> str | None:
        with open(video, "rb") as source, open(output, "wb") as out:
            out.write(source.read())
        self.made[os.stat(output).st_ino] = Contents(
            video=video_identity, duration=1.0, has_video=True,
            audio=[AudioOut(index, track.label, track.aligned_to, index == 0, "rus", track.own, track.key)
                   for index, track in enumerate(tracks)], publication=publication)
        self.builds.append((os.path.basename(output), video_identity,
                            [(track.label, os.path.basename(track.path), track.own) for track in tracks]))
        self.keys.append([track.key for track in tracks])
        return None

    async def contents(self, path: str) -> Contents | None:
        if not os.path.isfile(path):
            return None
        return self.made.get(os.stat(path).st_ino) or Contents(
            video="", duration=1.0, has_video=True, audio=[AudioOut(0, "", "", True, "", False)])
