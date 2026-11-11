"""How many downloads go at once, by the way a file comes.

A download that takes its file by many connections at once (the segments of a playlist, the parts of a file, a page that
yt-dlp reads) takes `threads_per_file` of them; all such downloads together take no more than `threads_total` — with 32
and 16, two at once. A download by one connection (a stream ffmpeg reads) takes one place of `single_at_once`. The way is
known once the stream of a link is asked (`ytdlp.multithreaded`), so a download waits for its place after that.

The three numbers are settings (`download_threads_total`, `download_threads_per_file`, `download_single_at_once`); this
module is their one owner: it gives their defaults and refuses a value that is no whole number from 1 up.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass

# Parts of one file asked at once: measured on the streams of a video application (tools/collectors/download_speed.py),
# 6 gave 62–108 Mbit/s, 16 gave 168–196 Mbit/s, 32 less than 16.
THREADS_PER_FILE = 16

KEYS = {"threads_total": "download_threads_total", "threads_per_file": "download_threads_per_file",
        "single_at_once": "download_single_at_once"}


@dataclass(frozen=True)
class Limits:
    threads_total: int = 2 * THREADS_PER_FILE
    threads_per_file: int = THREADS_PER_FILE
    single_at_once: int = 4

    @property
    def multi_at_once(self) -> int:
        """Downloads by many connections at once: as many as their threads fit in the total, one at least."""
        return max(1, self.threads_total // self.threads_per_file)

    def as_dict(self) -> dict:
        return {**asdict(self), "multi_at_once": self.multi_at_once}


def whole(value: object) -> int | None:
    """A setting as a whole number from 1 up; None for anything else."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


class Places:
    """The places of the downloads under way, by their way: a download takes one before it fetches and gives it back at
    its end; a change of the limits frees the waiting at once."""

    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self._busy = {True: 0, False: 0}
        self._changed = asyncio.Condition()

    def room(self, multi: bool) -> int:
        return self.limits.multi_at_once if multi else self.limits.single_at_once

    def busy(self, multi: bool) -> int:
        return self._busy[multi]

    def free(self, multi: bool) -> bool:
        return self._busy[multi] < self.room(multi)

    async def take(self, multi: bool) -> None:
        async with self._changed:
            await self._changed.wait_for(lambda: self.free(multi))
            self._busy[multi] += 1

    async def give(self, multi: bool) -> None:
        async with self._changed:
            self._busy[multi] -= 1
            self._changed.notify_all()

    async def set(self, limits: Limits) -> None:
        async with self._changed:
            self.limits = limits
            self._changed.notify_all()

    @property
    def total(self) -> int:
        """Downloads that may go at once whatever their way: how many leave the queue to ask their stream."""
        return self.limits.multi_at_once + self.limits.single_at_once
