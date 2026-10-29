"""Calls into the storage from async code, and the ownership of the paths they change.

A network share can stall for a long time, and a thread inside a stalled call cannot be stopped. The calls
therefore run in their own small pool and the caller waits for a limited time. A wait that ended is not a
call that ended: what the call changes stays owned until its thread returns, nobody may start a conflicting
change on that path meanwhile, and whoever needs the path can ask to be told when it is free.

Ownership is a hold: taken in one step (no await between the check and the claim), kept for as long as anything
that writes under it lives, released when the last of them has ended. A thread of `call` and a program started
by core.proc.spawn inside a `hold` are attached to it, so a hold ends after the writing does, not after the wait.
Calls made in the task that holds a path are the holder's own and are not refused by it.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import itertools
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterator

# Wait limits by the weight of the call: a metadata call, the removal of a tree, a move across directories.
DEFAULT_TIMEOUT_S = 30.0
REMOVE_TIMEOUT_S = 300.0
MOVE_TIMEOUT_S = 900.0
POOL_SIZE = 8

_pool = ThreadPoolExecutor(max_workers=POOL_SIZE, thread_name_prefix="storage")
_tokens = itertools.count(1)
_holds: dict[int, "Hold"] = {}
_waiting: list[tuple[str, Callable[[], None]]] = []
_mine: contextvars.ContextVar[frozenset[int]] = contextvars.ContextVar("storage_holds", default=frozenset())


class StorageTimeout(TimeoutError):
    """The wait ended, the call did not: its outcome is unknown and its paths stay owned until it ends."""


class StorageBusy(OSError):
    """A hold that has not ended yet owns the path: the change is refused, not queued."""


class Hold:
    """The ownership of some paths; it lasts until it is released and everything attached to it has ended."""

    def __init__(self, token: int, paths: tuple[str, ...]) -> None:
        self.token = token
        self.paths = paths
        self._open = True
        self._attached = 0

    def attach(self, future: asyncio.Future) -> None:
        """The hold also lasts until `future` ends: a thread or a program that writes under it."""
        self._attached += 1
        future.add_done_callback(self._detached)

    def release(self) -> None:
        self._open = False
        self._settle()

    def _detached(self, future: asyncio.Future) -> None:
        if not future.cancelled():
            future.exception()  # an error nobody waits for any more is not an unhandled one
        self._attached -= 1
        self._settle()

    def _settle(self) -> None:
        if not self._open and not self._attached and _holds.pop(self.token, None) is not None:
            _wake()


def _related(path: str, owned: str) -> bool:
    """The same name, a name inside the other, or a sibling artifact of it (`name.download.mp4` of `name`)."""
    for one, other in ((path, owned), (owned, path)):
        if one == other or one.startswith(other.rstrip(os.sep) + os.sep) or one.startswith(other + "."):
            return True
    return False


def _taken(path: str, ignore: frozenset[int] = frozenset()) -> bool:
    return any(_related(path, owned) for token, held in _holds.items() if token not in ignore for owned in held.paths)


def busy(path: str) -> bool:
    """Whether a hold of someone else owns this path, a path inside it or a path it lies in."""
    return _taken(os.path.normpath(path), _mine.get())


def when_free(path: str, callback: Callable[[], None]) -> None:
    """Run `callback` once no hold owns the path (at once when none does)."""
    if _taken(os.path.normpath(path)):
        _waiting.append((os.path.normpath(path), callback))
    else:
        callback()


def _wake() -> None:
    for entry in [entry for entry in _waiting if not _taken(entry[0])]:
        _waiting.remove(entry)
        entry[1]()


def acquire(*paths: str) -> Hold:
    """Take the paths in one step or refuse them all: the check and the claim have no await between them."""
    names = tuple(os.path.normpath(path) for path in paths if path)
    ignore = _mine.get()
    for name in names:
        if _taken(name, ignore):
            raise StorageBusy(f"путь занят незавершённой файловой операцией: {name}")
    token = next(_tokens)
    hold = _holds[token] = Hold(token, names)
    return hold


@contextlib.contextmanager
def hold(*paths: str) -> Iterator[Hold]:
    """Own the paths for the whole block; what the block starts (calls, programs) is the owner's own."""
    taken = acquire(*paths)
    marker = _mine.set(_mine.get() | {taken.token})
    try:
        yield taken
    finally:
        _mine.reset(marker)
        taken.release()


def attach_to_holds(make_future: Callable[[], asyncio.Future]) -> None:
    """Make every hold of the current task last until the future has ended (made only when there is a hold)."""
    held = [_holds[token] for token in _mine.get() if token in _holds]
    if held:
        future = make_future()
        for each in held:
            each.attach(future)


async def call(fn: Callable[..., Any], *args: Any, timeout: float = DEFAULT_TIMEOUT_S,
               owns: str | tuple[str, ...] = (), **kwargs: Any) -> Any:
    """Run `fn` in the storage pool and wait up to `timeout`; `owns` names what a changing call changes."""
    taken = acquire(*((owns,) if isinstance(owns, str) else owns))
    future = asyncio.get_running_loop().run_in_executor(_pool, functools.partial(fn, *args, **kwargs))
    taken.attach(future)
    taken.release()
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout)
    except asyncio.TimeoutError:
        raise StorageTimeout(f"хранилище не ответило за {timeout:.0f} с: {getattr(fn, '__name__', fn)}") from None
