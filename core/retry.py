"""Asking again, in one place: how many times, how long to wait between asks, and when to stop.

A caller says what makes an answer final; the budget and the loop here decide the rest. No module repeats a request or
pauses between attempts on its own account.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Budget:
    """How many times one thing is asked, the pauses between asks, and the time after which it is left unobtained."""
    attempts: int = 3
    pauses: tuple[float, ...] = (2.0, 6.0)
    limit_s: float = 30.0

    def pause(self, attempt: int, retry_after: float | None = None) -> float:
        """The wait after the given attempt: the scheduled one, or what the host asked for when that is longer."""
        scheduled = self.pauses[min(attempt - 1, len(self.pauses) - 1)] if self.pauses else 0.0
        return max(scheduled, retry_after or 0.0)

    def allows(self, attempt: int) -> bool:
        """Whether another attempt may follow the given one; for a caller whose attempts live outside a loop."""
        return attempt < self.attempts


# A budget with no time limit: the attempts alone bound it.
UNTIMED = math.inf


async def retrying(call: Callable[[], Awaitable[T]], *, final: Callable[[T], bool], budget: Budget,
                   retry_after: Callable[[T], float | None] = lambda result: None,
                   on_retry: Callable[[T, int, float], None] | None = None,
                   clock: Callable[[], float] = time.monotonic,
                   sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> tuple[T, int, bool]:
    """Call until `final` accepts a result or the budget is spent; (the last result, the attempts made, whether it was
    still unsure at the end)."""
    started, attempt = clock(), 0
    while True:
        attempt += 1
        result = await call()
        if final(result):
            return result, attempt, False
        pause = budget.pause(attempt, retry_after(result))
        if attempt >= budget.attempts or clock() - started + pause > budget.limit_s:
            return result, attempt, True
        if on_retry is not None:
            on_retry(result, attempt, pause)
        await sleep(pause)
