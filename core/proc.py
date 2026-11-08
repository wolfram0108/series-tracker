"""External programs started from async code.

A program is started through `spawn`, so that the storage holds of the task last until it has ended. Its end is taken
from `exited`: the report of the event loop or the word of the kernel (a pidfd of the process turns readable when the
process ends), whichever comes first. The loop learns of an end only from its own wait for the child, and a child that
another wait reaped is never reported by it; a wait for the loop alone would then last for ever, and so would every
hold attached to the program. A program is stopped through `stop`, which signals it by its pidfd, so a number the
kernel gave to another process meanwhile is never signalled.

The short programs of `run_capture` never outlive the wait for them: whichever way the wait ends (a result, the
deadline, a cancellation), a process that still runs is stopped.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal

from . import storage
from .logging import get_logger

REAP_TIMEOUT_S = 5.0
# The code of a program whose end is known but whose status is not (asyncio reports a lost child the same way).
LOST = 255

_log = get_logger("proc")


async def spawn(*cmd: str, **kwargs) -> asyncio.subprocess.Process:
    """Start a program; the holds of this task last until the program has ended, however its owner leaves."""
    proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
    storage.attach_to_holds(lambda: asyncio.ensure_future(exited(proc)))
    return proc


def _code(info: os.waitid_result) -> int:
    return info.si_status if info.si_code == os.CLD_EXITED else -info.si_status


async def exited(proc: asyncio.subprocess.Process) -> int:
    """The exit code of the program once it has ended (negative: the signal that ended it; LOST: ended, status unknown).

    The code is written into the program's own record (the `subprocess.Popen` behind it): a record left without one when
    it is dropped is taken by the standard library for a child still to be waited for, and its next start of any program
    waits for that number — which the kernel may have given to another program of ours by then, whose status it takes
    (the loop then reports LOST for it)."""
    code = await _ended(proc)
    _settle(proc, code)
    return code


def _settle(proc: asyncio.subprocess.Process, code: int) -> None:
    transport = getattr(proc, "_transport", None)
    record = transport.get_extra_info("subprocess") if transport is not None else None
    if record is not None and record.returncode is None:
        record.returncode = code


async def _ended(proc: asyncio.subprocess.Process) -> int:
    if proc.returncode is not None:
        return proc.returncode
    reported = asyncio.ensure_future(proc.wait())
    try:
        pidfd = os.pidfd_open(proc.pid)
    except ProcessLookupError:
        if proc.returncode is not None:
            reported.cancel()
            return proc.returncode
        reported.cancel()
        _log.warning("программа pid %d завершилась, но цикл событий не сообщил об этом: код выхода неизвестен", proc.pid)
        return LOST
    except OSError:  # a kernel without pidfd: the loop is the only witness
        return await reported
    loop = asyncio.get_running_loop()
    ended = loop.create_future()
    loop.add_reader(pidfd, lambda: ended.done() or ended.set_result(None))
    try:
        await asyncio.wait({reported, ended}, return_when=asyncio.FIRST_COMPLETED)
        if reported.done():
            return reported.result()
        if proc.returncode is not None:
            return proc.returncode
        # The status is read without reaping the child: reaping stays the loop's.
        try:
            return _code(os.waitid(os.P_PIDFD, pidfd, os.WEXITED | os.WNOHANG | os.WNOWAIT))
        except ChildProcessError:
            pass
        # Reaped already: by the loop, which reports it at once, or by another wait, of which the loop is never told.
        try:
            return await asyncio.wait_for(asyncio.shield(reported), REAP_TIMEOUT_S)
        except asyncio.TimeoutError:
            _log.warning("программа pid %d завершилась, но цикл событий не сообщил об этом: код выхода неизвестен", proc.pid)
            return LOST
    finally:
        loop.remove_reader(pidfd)
        os.close(pidfd)
        if not reported.done():
            reported.cancel()


async def stop(proc: asyncio.subprocess.Process) -> int:
    """Kill the program unless it has ended, and return its code once it has."""
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError, OSError):
            pidfd = os.pidfd_open(proc.pid)
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            finally:
                os.close(pidfd)
    return await exited(proc)


async def run_capture(cmd: list[str], *, timeout: float) -> tuple[int, bytes, bytes]:
    """(return code, stdout, stderr) of a finished program; raises TimeoutError at the deadline."""
    proc = await spawn(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err, code = await asyncio.wait_for(
            asyncio.gather(proc.stdout.read(), proc.stderr.read(), exited(proc)), timeout)
    except BaseException:
        await _reap(proc)
        raise
    return code, out, err


async def _reap(proc: asyncio.subprocess.Process) -> None:
    """Stop a program whose wait was given up; its end is waited for a short while only, the caller is leaving."""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(stop(proc)), REAP_TIMEOUT_S)
