"""The clock of what keeps time: a window that tells how long a work has been going is told anew every second while the
work goes, and a client of an external protocol says what that protocol wants said at its period (a player that says it
plays every 30 seconds). Time is the subject of such a beat, so a beat of time is its signal; it is never a way to wait
for another component (that is a signal of readiness, core/module) nor a pause between attempts (core/retry)."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

BEAT_S = 1.0  # a display of minutes and seconds changes once a second


async def beat_while(going: Callable[[], bool], tell: Callable[[], None]) -> None:
    """Tell every BEAT_S while `going` says the work goes; once it stops, tell once more and return."""
    while going():
        tell()
        await asyncio.sleep(BEAT_S)
    tell()


async def every(period_s: float, act: Callable[[], Awaitable[None]]) -> None:
    """`act` every `period_s`, the first after one period, until the caller cancels the beat: the period an external
    protocol prescribes."""
    while True:
        await asyncio.sleep(period_s)
        await act()
