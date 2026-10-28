"""Storage calls from async code wait for a limited time and never take the process's default pool."""
import asyncio
import time

import pytest

from core import storage


@pytest.mark.asyncio
async def test_a_call_returns_its_result():
    assert await storage.call(lambda a, b: a + b, 2, 3, timeout=5) == 5


@pytest.mark.asyncio
async def test_a_stalled_call_ends_the_wait_with_a_timeout_while_others_still_run():
    started = time.monotonic()
    stalled = asyncio.create_task(storage.call(time.sleep, 1.5, timeout=0.2))
    assert await storage.call(lambda: "fast", timeout=5) == "fast"
    with pytest.raises(TimeoutError, match="хранилище не ответило"):
        await stalled
    assert time.monotonic() - started < 1.0


@pytest.mark.asyncio
async def test_the_errors_of_the_call_reach_the_caller_as_they_are():
    def refuse():
        raise PermissionError("read-only")

    with pytest.raises(PermissionError):
        await storage.call(refuse, timeout=5)
