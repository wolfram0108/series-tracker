"""Pure computation from async code.

Work of tenths of a second — the rules of a profile over the thousands of lines of a page — runs in its own small pool,
so the loop that serves every module and window does not wait for it. It touches neither the file system (that is
core.storage, whose waits are bounded) nor the network: a computation always ends, and its error comes back to the caller.
"""
from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

# The heavy counts (core.native) let the interpreter go while they run, so they count side by side on as many cores; the
# interpreter itself runs one thread of Python at a time.
POOL_SIZE = 4

_pool = ThreadPoolExecutor(max_workers=POOL_SIZE, thread_name_prefix="compute")


async def run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """`fn(*args, **kwargs)` in the pool of computation; its result or its error."""
    return await asyncio.get_running_loop().run_in_executor(_pool, functools.partial(fn, *args, **kwargs))
