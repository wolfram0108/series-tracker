"""The browser of the module: one for every player and tracker page that needs a real one, started when a page is first
asked for and stopped when its last user has gone.

A browser left running holds what its pages left in it for as long as the service lives, and two asks at once that each
find none start two, one of which nobody ever stops. Here the start is one at a time, and the browser has users: a page
context (from `new_context` to its `close`) and a longer use that keeps the browser between its pages (`held`, a read of
a table: its rows are asked one after another). When the last user goes, the browser and its driver are stopped.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable

Launch = Callable[[], Awaitable[tuple[Any, Any]]]


async def firefox() -> tuple[Any, Any]:
    """The driver and a headless Firefox; the driver does not outlive a browser that did not start."""
    from playwright.async_api import async_playwright
    driver = await async_playwright().start()
    try:
        return driver, await driver.firefox.launch(headless=True)
    except BaseException:
        await driver.stop()
        raise


class _Context:
    """A page context of the browser that tells the pool when it is closed; everything else is the context's own."""

    def __init__(self, pool: "BrowserPool", context: Any) -> None:
        self._pool, self._context, self._open = pool, context, True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)

    async def close(self) -> None:
        if not self._open:
            return
        self._open = False
        try:
            await self._context.close()
        finally:
            await self._pool._gone()


class _Browser:
    """What a player is given as the browser: its contexts are users of the pool."""

    def __init__(self, pool: "BrowserPool") -> None:
        self._pool = pool

    async def new_context(self, **options: Any) -> _Context:
        return await self._pool._context(options)


class BrowserPool:
    def __init__(self, log: Any, launch: Launch = firefox) -> None:
        self._log, self._launch = log, launch
        self._changing = asyncio.Lock()  # a start or a stop of the browser is one at a time
        self._driver: Any = None
        self._browser: Any = None
        self._users = 0

    @property
    def running(self) -> bool:
        return self._browser is not None

    async def browser(self) -> _Browser:
        """The browser as a player asks for it (`browser_getter`): nothing is started until a context is asked for."""
        return _Browser(self)

    @asynccontextmanager
    async def held(self) -> AsyncIterator[None]:
        """A use that keeps the browser, once started, between its pages; it starts nothing itself."""
        self._users += 1
        try:
            yield
        finally:
            await self._gone()

    async def _context(self, options: dict) -> _Context:
        async with self._changing:
            if self._browser is None:
                self._driver, self._browser = await self._launch()
                self._log.info("браузерный пул запущен (firefox, headless)")
            self._users += 1
            browser = self._browser
        try:
            return _Context(self, await browser.new_context(**options))
        except BaseException:
            await self._gone()
            raise

    async def _gone(self) -> None:
        self._users -= 1
        if self._users == 0:
            # A stop is finished whatever happens to the one who left last (a cancelled read still frees the browser).
            await asyncio.shield(self._stop(only_unused=True))

    async def _stop(self, *, only_unused: bool) -> None:
        async with self._changing:
            if self._browser is None or (only_unused and self._users > 0):
                return
            driver, browser, self._driver, self._browser = self._driver, self._browser, None, None
            try:
                await browser.close()
            finally:
                await driver.stop()
            self._log.info("браузерный пул остановлен: пользователей не осталось")

    async def shutdown(self) -> None:
        """The stop of the module: the browser goes whoever still uses it."""
        await self._stop(only_unused=False)
