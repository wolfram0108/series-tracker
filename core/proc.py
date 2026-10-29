"""External programs started from async code.

A program is started through `spawn`, so that the storage holds of the task last until it has ended. The
short ones of `run_capture` never outlive the wait for them: whichever way the wait ends (a result, the
deadline, a cancellation), a process that still runs is killed and reaped.
"""
from __future__ import annotations

import asyncio

from . import storage

REAP_TIMEOUT_S = 5.0


async def spawn(*cmd: str, **kwargs) -> asyncio.subprocess.Process:
    """Start a program; the holds of this task last until the program has ended, however its owner leaves."""
    proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
    storage.attach_to_holds(lambda: asyncio.ensure_future(proc.wait()))
    return proc


async def run_capture(cmd: list[str], *, timeout: float) -> tuple[int, bytes, bytes]:
    """(return code, stdout, stderr) of a finished program; raises TimeoutError at the deadline."""
    proc = await spawn(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException:
        await _reap(proc)
        raise
    return proc.returncode, out, err


async def _reap(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            return
    try:
        await asyncio.wait_for(proc.wait(), REAP_TIMEOUT_S)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
