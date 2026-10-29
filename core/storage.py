"""Calls into the storage from async code.

A network share can stall for a long time, and a thread inside a stalled call cannot be stopped. The
calls therefore run in their own small pool, so that they never take the default pool of the process,
and the caller waits for a limited time and gets a TimeoutError it can handle.
"""
from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

# Wait limits by the weight of the call: a metadata call, the removal of a tree, a move across directories.
DEFAULT_TIMEOUT_S = 30.0
REMOVE_TIMEOUT_S = 300.0
MOVE_TIMEOUT_S = 900.0
POOL_SIZE = 8

_pool = ThreadPoolExecutor(max_workers=POOL_SIZE, thread_name_prefix="storage")


async def call(fn: Callable[..., Any], *args: Any, timeout: float = DEFAULT_TIMEOUT_S, **kwargs: Any) -> Any:
    future = asyncio.get_running_loop().run_in_executor(_pool, functools.partial(fn, *args, **kwargs))
    try:
        return await asyncio.wait_for(future, timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(f"хранилище не ответило за {timeout:.0f} с: {getattr(fn, '__name__', fn)}") from None
