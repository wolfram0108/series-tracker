"""The one place that decides whether to ask a player again: which answers are unsure, and the pace of the requests.

Players only fetch once and read the answer into a kind; nothing else in `modules/sources` repeats a request or pauses
between attempts on its own account. How many times and how long to wait is the budget of `core.retry`.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from core.retry import Budget, retrying

from .players.contract import Answer, Kind, Player, PlayerRef, Reading

# A refusal that can pass: the host is asked again, unlike a final "no".
TRANSIENT_STATUSES = frozenset({403, 408, 425, 429})
UNSURE_KINDS = frozenset({Kind.NOT_PLAYER, Kind.NO_FILE, Kind.NO_ANSWER})


def status_is_transient(status: int) -> bool:
    return status in TRANSIENT_STATUSES or status >= 500


def is_retryable(reading: Reading) -> bool:
    if reading.kind is Kind.HTTP_ERROR:
        return status_is_transient(reading.status)
    return reading.kind in UNSURE_KINDS


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


class AdaptivePacer(Pacer):
    """A pacer that goes slower when the host asks for it: every request after a refusal waits the gap longer, and
    everybody waits the pause the host named."""

    def __init__(self, interval_s: float, *, ceiling_s: float = 1.0, factor: float = 1.25,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        super().__init__(lambda: self.interval, clock=clock, sleep=sleep)
        self.interval, self._ceiling, self._factor = interval_s, max(ceiling_s, interval_s), factor

    def slow_down(self, hold_s: float = 0.0) -> None:
        self.interval = min(self.interval * self._factor, self._ceiling)
        if hold_s > 0:
            self._next_at = max(self._next_at, self._clock() + hold_s)


async def settle(player: Player, ref: PlayerRef, http: httpx.AsyncClient, page_url: str,
                 budget: Budget | None = None, *, pacer: Pacer | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> Verdict:
    """Ask a player until its answer is final or the budget is spent."""
    async def ask() -> tuple[Answer, Reading]:
        if pacer is not None:
            await pacer.wait()
        answer = await player.fetch(ref, http, page_url)
        return answer, player.read(answer)

    (_answer, reading), attempts, exhausted = await retrying(
        ask, final=lambda asked: not is_retryable(asked[1]), budget=budget or DEFAULT,
        retry_after=lambda asked: asked[0].retry_after, clock=clock, sleep=sleep)
    return Verdict(reading, attempts, exhausted)
