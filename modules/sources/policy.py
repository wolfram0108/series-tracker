"""The one place that decides whether to ask a player again, how long to wait, and when to stop.

Players only fetch once and read the answer into a kind; nothing else in `modules/sources` repeats a request or pauses
between attempts on its own account.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from .players.contract import Kind, Player, PlayerRef, Reading

# A refusal that can pass: the host is asked again, unlike a final "no".
TRANSIENT_STATUSES = frozenset({403, 408, 425, 429})
UNSURE_KINDS = frozenset({Kind.NOT_PLAYER, Kind.NO_FILE, Kind.NO_ANSWER})


def status_is_transient(status: int) -> bool:
    return status in TRANSIENT_STATUSES or status >= 500


def is_retryable(reading: Reading) -> bool:
    if reading.kind is Kind.HTTP_ERROR:
        return status_is_transient(reading.status)
    return reading.kind in UNSURE_KINDS


@dataclass(frozen=True)
class Budget:
    """How many times one cell is asked, the pauses between asks, and the time after which it is left unobtained."""
    attempts: int = 3
    pauses: tuple[float, ...] = (2.0, 6.0)
    limit_s: float = 30.0

    def pause(self, attempt: int, retry_after: float | None = None) -> float:
        scheduled = self.pauses[min(attempt - 1, len(self.pauses) - 1)] if self.pauses else 0.0
        return max(scheduled, retry_after or 0.0)


DEFAULT = Budget()


@dataclass(frozen=True)
class Verdict:
    """The last reading of a cell; `exhausted` means it was still unsure when attempts or time ran out."""
    reading: Reading
    attempts: int
    exhausted: bool


class Pacer:
    """Spreads the requests to one host so that a burst is not taken for abuse."""

    def __init__(self, interval_s: float | Callable[[], float], *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._interval = interval_s if callable(interval_s) else (lambda: interval_s)
        self._clock, self._sleep = clock, sleep
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._next_at - self._clock()
            if delay > 0:
                await self._sleep(delay)
            self._next_at = self._clock() + self._interval()


async def settle(player: Player, ref: PlayerRef, http: httpx.AsyncClient, page_url: str,
                 budget: Budget | None = None, *, pacer: Pacer | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> Verdict:
    """Ask a player until its answer is final or the budget is spent."""
    budget = budget or DEFAULT
    started, attempt = clock(), 0
    while True:
        attempt += 1
        if pacer is not None:
            await pacer.wait()
        answer = await player.fetch(ref, http, page_url)
        reading = player.read(answer)
        if not is_retryable(reading):
            return Verdict(reading, attempt, False)
        pause = budget.pause(attempt, answer.retry_after)
        if attempt >= budget.attempts or clock() - started + pause > budget.limit_s:
            return Verdict(reading, attempt, True)
        await sleep(pause)
