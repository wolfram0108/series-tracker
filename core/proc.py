"""Short external programs with a bounded life.

The process never outlives the wait for it: whichever way the wait ends (a result, the deadline, a
cancellation), a process that still runs is killed and reaped.
"""
from __future__ import annotations

import asyncio

REAP_TIMEOUT_S = 5.0


async def run_capture(cmd: list[str], *, timeout: float) -> tuple[int, bytes, bytes]:
    """(return code, stdout, stderr) of a finished program; raises TimeoutError at the deadline."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
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
