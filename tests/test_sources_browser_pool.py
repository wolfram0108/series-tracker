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


async def test_asks_at_once_start_one_browser_and_nothing_is_started_before_a_page_is_asked_for(pool):
    browsers, starts = pool
    leased = await browsers.browser()
    assert starts.made == [] and not browsers.running
    starts.gate.clear()
    asks = [asyncio.create_task(leased.new_context(locale="ru-RU")) for _ in range(5)]
    await asyncio.sleep(0)
    starts.gate.set()
    contexts = await asyncio.gather(*asks)
    assert len(starts.made) == 1 and starts.made[0][1].options == [{"locale": "ru-RU"}] * 5
    for context in contexts:
        await context.close()


async def test_the_browser_is_stopped_with_its_driver_when_its_last_context_is_closed_and_started_anew_at_the_next(pool):
    browsers, starts = pool
    leased = await browsers.browser()
    first, second = await leased.new_context(), await leased.new_context()
    await first.route("**/*", None)
    assert first.routed == ["**/*"]  # everything but the close is the context's own
    await first.close()
    assert browsers.running and not starts.made[0][1].closed
    await second.close()
    await second.close()  # a context closed twice frees the browser once
    driver, browser = starts.made[0]
    assert not browsers.running and browser.closed and driver.stopped and browser.open == 0
    again = await leased.new_context()
    assert len(starts.made) == 2 and browsers.running
    await again.close()


async def test_a_read_holds_the_browser_between_its_pages_and_starts_none_itself(pool):
    browsers, starts = pool
    async with browsers.held():
        assert starts.made == []
        leased = await browsers.browser()
        for _row in range(3):
            await (await leased.new_context()).close()
        assert len(starts.made) == 1 and browsers.running
    assert not browsers.running and starts.made[0][1].closed
    async with browsers.held():
        pass
    assert len(starts.made) == 1  # a read that needed no page started no browser


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
