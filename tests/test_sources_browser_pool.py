"""The browser of the module (modules/sources/browser_pool.py): one start whoever asks, a stop after the last user, a
context that frees it however its block ends."""
from __future__ import annotations

import asyncio
import logging

import pytest

from modules.sources.browser_pool import BrowserPool


class Driver:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class Context:
    def __init__(self, browser: "Browser") -> None:
        self.browser, self.closed, self.routed = browser, 0, []

    async def route(self, pattern: str, handler) -> None:
        self.routed.append(pattern)

    async def close(self) -> None:
        self.closed += 1
        self.browser.open -= 1


class Browser:
    def __init__(self) -> None:
        self.open, self.closed, self.options, self.refuses = 0, False, [], False

    async def new_context(self, **options) -> Context:
        if self.refuses:
            raise RuntimeError("the browser crashed")
        self.options.append(options)
        self.open += 1
        return Context(self)

    async def close(self) -> None:
        self.closed = True


class Starts:
    """Every start of a browser, each slow enough for another ask to come while it lasts."""

    def __init__(self) -> None:
        self.made: list[tuple[Driver, Browser]] = []
        self.gate = asyncio.Event()
        self.gate.set()

    async def __call__(self) -> tuple[Driver, Browser]:
        await self.gate.wait()
        self.made.append((Driver(), Browser()))
        return self.made[-1]


@pytest.fixture
def pool():
    starts = Starts()
    return BrowserPool(logging.getLogger("test"), launch=starts), starts


async def test_a_context_that_was_not_made_and_a_cancelled_user_free_the_browser(pool):
    browsers, starts = pool
    leased = await browsers.browser()
    kept = await leased.new_context()
    starts.made[0][1].refuses = True
    with pytest.raises(RuntimeError):
        await leased.new_context()
    await kept.close()
    assert not browsers.running

    async def read() -> None:
        async with browsers.held():
            await leased.new_context()
            await asyncio.Event().wait()

    task = asyncio.create_task(read())
    while len(starts.made) < 2 or starts.made[1][1].open == 0:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The read is gone; its context was never closed by it, so the browser still has that user — and the stop of the
    # module takes the browser whoever still holds it.
    assert browsers.running
    await browsers.shutdown()
    assert not browsers.running and starts.made[1][1].closed and starts.made[1][0].stopped
