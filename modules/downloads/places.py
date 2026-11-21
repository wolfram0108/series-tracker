"""The places of the downloads under way, by the way their file comes (the limits are calc/downloads/limits.py).

A fetch takes its turn (`turn`) when it leaves the queue and keeps it through its attempts; it learns its way once it has asked its
stream, and takes a place of its way in the order of the turns: an earlier fetch still asking its stream, or waiting for the same way,
goes first. The place is given back at the end of the attempt; a change of the limits frees the waiting at once."""
from __future__ import annotations

import asyncio
from typing import Callable

from calc.downloads.limits import MULTI, PROVIDER, SINGLE, room, total
from core.contracts import DownloadLimits


class Places:
    def __init__(self, limits: DownloadLimits) -> None:
        self.limits = limits
        self._busy = {MULTI: 0, SINGLE: 0, PROVIDER: 0}
        self._turns = 0
        self._asking: dict[int, str | None] = {}  # turn -> its way, None while its stream is asked
        self._changed = asyncio.Condition()

    def room(self, way: str) -> int:
        return room(self.limits, way)

    def busy(self, way: str) -> int:
        return self._busy[way]

    @property
    def counts(self) -> dict[str, int]:
        """How many downloads of each way go."""
        return dict(self._busy)

    def turn(self) -> int:
        """The turn of a fetch that leaves the queue: from now on later fetches wait for it to learn its way."""
        self._turns += 1
        self._asking[self._turns] = None
        return self._turns

    def asking(self, turn: int) -> None:
        """An attempt of the fetch asks its stream: later fetches of any way wait for it to learn its own."""
        self._asking[turn] = None

    def _ready(self, way: str, turn: int) -> bool:
        return self._busy[way] < self.room(way) and not any(
            earlier < turn and asked in (None, way) for earlier, asked in self._asking.items())

    async def take(self, way: str, turn: int, on_wait: Callable[[], None] = lambda: None) -> bool:
        """A place of the way for the turn; True when it had to wait for it (`on_wait` is told then)."""
        async with self._changed:
            self._asking[turn] = way
            self._changed.notify_all()
            waited = not self._ready(way, turn)
            if waited:
                on_wait()
            try:
                await self._changed.wait_for(lambda: self._ready(way, turn))
            finally:
                self._asking.pop(turn, None)
                self._changed.notify_all()
            self._busy[way] += 1
            return waited

    async def leave(self, turn: int) -> None:
        """The attempt ended before it took a place (its stream refused, the fetch stopped)."""
        async with self._changed:
            if self._asking.pop(turn, "gone") != "gone":
                self._changed.notify_all()

    async def give(self, way: str) -> None:
        async with self._changed:
            self._busy[way] -= 1
            self._changed.notify_all()

    async def set(self, limits: DownloadLimits) -> None:
        async with self._changed:
            self.limits = limits
            self._changed.notify_all()

    @property
    def total(self) -> int:
        """Downloads that may go at once whatever their way: how many leave the queue to ask their stream."""
        return total(self.limits)
