"""External programs started from async code.

A program is started through `spawn`, so that the storage holds of the task last until it has ended. Its end is taken
from `exited`: the report of the event loop or the word of the kernel (a pidfd of the process turns readable when the
process ends), whichever comes first. The loop learns of an end only from its own wait for the child, and a child that
another wait reaped is never reported by it; a wait for the loop alone would then last for ever, and so would every
hold attached to the program. A program is stopped through `stop`, which signals it by its pidfd, so a number the
kernel gave to another process meanwhile is never signalled.

The short programs of `run_capture` never outlive the wait for them: whichever way the wait ends (a result, the
deadline, a cancellation), a process that still runs is stopped.

A program is a compartment of its own (bulkhead): in the service (`isolate`) each lives in its own group of control with a
limit of memory, so one that runs out of memory is killed alone and its step fails, the service goes on; everywhere it is
the first the kernel takes when the machine runs out of memory (its oom_score_adj is the highest).
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import os
import shutil
import signal
import sys

from . import storage
from .logging import get_logger

REAP_TIMEOUT_S = 5.0
CGROUP_ROOT = "/sys/fs/cgroup"
GROUP_PREFIX = "program-"
# A program of the service (ffmpeg, ffprobe, yt-dlp) needs tens to hundreds of megabytes; one that needs more is a defect
# of its use (the muxer held a whole file once), and it must not take the memory of the machine with it.
PROGRAM_MEMORY_MAX = 2 << 30
_groups: str | None = None  # the service's own group, given over to it (`isolate`); None: the programs share its group
_numbers = itertools.count(1)
SHELL = "/bin/sh"
_BECOME = 'echo 1000 2>/dev/null > /proc/self/oom_score_adj; exec "$@"'  # first the kernel takes; then the program
# The code of a program whose end is known but whose status is not (asyncio reports a lost child the same way).
LOST = 255

_log = get_logger("proc")


def program(name: str) -> str | None:
    """The path of an external program; None when there is none. A program the project installs as its own dependency
    (yt-dlp, requirements.txt) lies beside the interpreter and is found there whatever PATH says; the rest come from PATH."""
    beside = os.path.join(os.path.dirname(sys.executable), name)
    return beside if os.access(beside, os.X_OK) and os.path.isfile(beside) else shutil.which(name)


def isolate(root: str = CGROUP_ROOT) -> bool:
    """Every program started from now on lives in a group of control of its own, under the service's group, with a limit
    of memory: a program that runs out of it is killed alone and its step fails; the service and the other programs go on.
    It takes a group the service manager gave over to the service (the unit: `Delegate=memory pids`); the service itself
    moves into a leaf of it, since a group that holds processes gives no controllers to the groups under it. Only the
    service calls it: a script or a test leaves the groups it runs in as they are. Whether the programs are isolated."""
    global _groups
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as stream:
            own = root + stream.read().strip().split("::", 1)[1]
        os.makedirs(os.path.join(own, "main"), exist_ok=True)
        with open(os.path.join(own, "main", "cgroup.procs"), "w") as stream:
            stream.write(str(os.getpid()))
        with open(os.path.join(own, "cgroup.subtree_control"), "w") as stream:
            stream.write("+memory +pids")
    except (OSError, IndexError) as exc:
        _log.warning("программы запускаются без своих групп памяти: группа сервиса ему не передана (%s)", exc)
        return False
    for name in os.listdir(own):
        if name.startswith(GROUP_PREFIX):
            with contextlib.suppress(OSError):
                os.rmdir(os.path.join(own, name))  # the group of a program of an earlier start, if it is empty
    _groups = own
    _log.info("программы запускаются каждая в своей группе памяти под %s (предел %d МБ)", own, PROGRAM_MEMORY_MAX >> 20)
    return True


def _group_for(memory_max: int) -> str | None:
    """A group of its own for a program about to start, with its limit; None when the programs are not isolated."""
    if _groups is None:
        return None
    group = os.path.join(_groups, f"{GROUP_PREFIX}{next(_numbers)}")
    try:
        os.mkdir(group)
        for name, value in (("memory.max", memory_max), ("memory.swap.max", 0), ("memory.oom.group", 1)):
            with contextlib.suppress(OSError):  # a controller the kernel lacks (no swap) leaves its file out
                with open(os.path.join(group, name), "w") as stream:
                    stream.write(str(value))
    except OSError as exc:
        _log.warning("группа памяти для программы не создана (%s): программа идёт в группе сервиса", exc)
        return None
    return group


def _released(proc: asyncio.subprocess.Process) -> None:
    """The group of an ended program goes; a kill for memory there is said in the journal with its program."""
    group = getattr(proc, "_st_group", None)
    if group is None:
        return
    proc._st_group = None
    with contextlib.suppress(OSError, ValueError):
        with open(os.path.join(group, "memory.events"), encoding="utf-8") as stream:
            events = dict(line.split() for line in stream if line.strip())
        if int(events.get("oom_kill", 0)):
            _log.warning("программа %s убита: не хватило памяти (предел её группы %d МБ)",
                         getattr(proc, "_st_name", "?"), getattr(proc, "_st_limit", 0) >> 20)
    with contextlib.suppress(OSError):
        os.rmdir(group)


def program_argv(argv) -> tuple[str, ...]:
    """The command of the program itself in what `spawn` starts (the shell that puts it into its group comes first)."""
    argv = tuple(argv)
    return argv[4:] if len(argv) > 4 and argv[:2] == (SHELL, "-c") and argv[2].endswith(_BECOME) else argv


async def spawn(*cmd: str, memory_max: int = PROGRAM_MEMORY_MAX, **kwargs) -> asyncio.subprocess.Process:
    """Start a program; the holds of this task last until the program has ended, however its owner leaves. The program
    goes into its own group of memory (`isolate`) and is the first the kernel takes when the machine runs out of memory:
    the shell that starts it puts itself there, then becomes the program (`exec`), so the program never runs outside."""
    group = _group_for(memory_max)
    # Each step stands alone: a program that could not enter its group still runs, first in line for the kernel.
    enter = 'echo $$ 2>/dev/null > "$0/cgroup.procs"; ' if group is not None else ""
    wrapped = (SHELL, "-c", enter + _BECOME, group or "program", *cmd)
    proc = await asyncio.create_subprocess_exec(*wrapped, **kwargs)
    proc._st_group, proc._st_name, proc._st_limit = group, os.path.basename(cmd[0]) if cmd else "?", memory_max
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
    _released(proc)
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
        # The child is gone already: the loop reaped it and reports it at once (its report is a callback still to run),
        # or another wait took it, of which the loop is never told.
        try:
            return await asyncio.wait_for(asyncio.shield(reported), REAP_TIMEOUT_S)
        except asyncio.TimeoutError:
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


async def run_capture(cmd: list[str], *, timeout: float, data: bytes | None = None) -> tuple[int, bytes, bytes]:
    """(return code, stdout, stderr) of a finished program; raises TimeoutError at the deadline. `data` is given to it
    as its standard input."""
    proc = await spawn(*cmd, stdin=asyncio.subprocess.PIPE if data is not None else None,
                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    async def feed() -> None:
        if data is None:
            return
        # A program may stop reading once it has what it needs (ffprobe with the head of a stream): that is no failure.
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            proc.stdin.write(data)
            await proc.stdin.drain()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            proc.stdin.close()

    try:
        _, out, err, code = await asyncio.wait_for(
            asyncio.gather(feed(), proc.stdout.read(), proc.stderr.read(), exited(proc)), timeout)
    except BaseException:
        await _reap(proc)
        raise
    return code, out, err


async def run_lines(cmd: list[str], *, timeout: float, on_line) -> tuple[int, bytes]:
    """(return code, stderr) of a finished program whose standard output is told line by line as it comes (`on_line`, an
    async callable of the decoded line); raises TimeoutError at the deadline. Its errors are read aside, so a full pipe
    never stops it."""
    proc = await spawn(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    async def lines() -> None:
        while line := await proc.stdout.readline():
            await on_line(line.decode("utf-8", errors="replace").strip())

    try:
        _, err, code = await asyncio.wait_for(asyncio.gather(lines(), proc.stderr.read(), exited(proc)), timeout)
    except BaseException:
        await _reap(proc)
        raise
    return code, err


async def _reap(proc: asyncio.subprocess.Process) -> None:
    """Stop a program whose wait was given up; its end is waited for a short while only, the caller is leaving."""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(stop(proc)), REAP_TIMEOUT_S)
