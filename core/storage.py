"""Calls into the storage from async code.

A network share can stall for a long time, and a thread inside a stalled call cannot be stopped. The calls
therefore run in their own small pool and the caller waits for a limited time. A wait that ended is not a
call that ended: what the call changes stays owned by it until its thread returns, nobody may start a
conflicting change on that path meanwhile, and whoever needs the path can ask to be told when it is free.
"""
from __future__ import annotations

import asyncio
import functools
import itertools
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

# Wait limits by the weight of the call: a metadata call, the removal of a tree, a move across directories.
DEFAULT_TIMEOUT_S = 30.0
REMOVE_TIMEOUT_S = 300.0
MOVE_TIMEOUT_S = 900.0
POOL_SIZE = 8

_pool = ThreadPoolExecutor(max_workers=POOL_SIZE, thread_name_prefix="storage")
_tokens = itertools.count(1)
_running: dict[int, tuple[str, ...]] = {}
_waiting: list[tuple[str, Callable[[], None]]] = []


class StorageTimeout(TimeoutError):
    """The wait ended, the call did not: its outcome is unknown and its paths stay owned until it ends."""


class StorageBusy(OSError):
    """A call that has not ended yet owns the path: the change is refused, not queued."""


def _related(path: str, owned: str) -> bool:
    """The same name, a name inside the other, or a sibling artifact of it (`name.download.mp4` of `name`)."""
    for one, other in ((path, owned), (owned, path)):
        if one == other or one.startswith(other.rstrip(os.sep) + os.sep) or one.startswith(other + "."):
            return True
    return False


def busy(path: str) -> bool:
    """Whether a call that has not ended yet owns this path, a path inside it or a path it lies in."""
    path = os.path.normpath(path)
    return any(_related(path, owned) for paths in _running.values() for owned in paths)


def when_free(path: str, callback: Callable[[], None]) -> None:
    """Run `callback` once no running call owns the path (at once when none does)."""
    if busy(path):
        _waiting.append((os.path.normpath(path), callback))
    else:
        callback()


def _ended(token: int, future: asyncio.Future) -> None:
    _running.pop(token, None)
    if not future.cancelled():
        future.exception()  # an error nobody waits for any more is not an unhandled one
    for entry in [entry for entry in _waiting if not busy(entry[0])]:
        _waiting.remove(entry)
        entry[1]()


async def call(fn: Callable[..., Any], *args: Any, timeout: float = DEFAULT_TIMEOUT_S,
               owns: str | tuple[str, ...] = (), **kwargs: Any) -> Any:
    """Run `fn` in the storage pool and wait up to `timeout`; `owns` names what a changing call changes."""
    paths = tuple(os.path.normpath(path) for path in ((owns,) if isinstance(owns, str) else owns) if path)
    for path in paths:
        if busy(path):
            raise StorageBusy(f"путь занят незавершённой файловой операцией: {path}")
    token = next(_tokens)
    if paths:
        _running[token] = paths
    future = asyncio.get_running_loop().run_in_executor(_pool, functools.partial(fn, *args, **kwargs))
    future.add_done_callback(functools.partial(_ended, token))
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout)
    except asyncio.TimeoutError:
        raise StorageTimeout(f"хранилище не ответило за {timeout:.0f} с: {getattr(fn, '__name__', fn)}") from None
