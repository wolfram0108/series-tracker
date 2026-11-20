"""External programs started from async code.

Starting a program waits for the kernel, and the loop that serves every module and window must not wait with it: the
loop of the service (uvloop) starts a child by copying the whole service (fork), tens to hundreds of milliseconds that
grow with its memory, and the group of memory of a program is made and removed in the file system of control groups.
All of it runs in a small pool of its own (`proc`): the group, the start (`subprocess.Popen`, whose vfork suspends only
the thread that calls it), and at the end the status of the program and the removal of its group. The loop only connects
the pipes of the program as asyncio streams.

A program is started through `spawn`, so that the storage holds of the task last until it has ended. Its end is the word
of the kernel: a pidfd of the process turns readable when the process ends, and its status is read through that pidfd.
No wait of the loop or of the standard library knows of the child, so its number stays its own until its status is read;
a status that a wait of somebody else took is never known (LOST). A program is stopped through `stop`, which signals it by
its pidfd, so a number the kernel gave to another process is never signalled.

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
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

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
# The code of a program whose end is known but whose status is not.
LOST = 255
# A start takes a thread for milliseconds; a few threads keep one start that waits for the kernel from holding the rest.
POOL_SIZE = 4
READ_LIMIT = 2 ** 16  # what a pipe of the program buffers before its reading pauses, as asyncio's own subprocess streams

_log = get_logger("proc")
_pool = ThreadPoolExecutor(max_workers=POOL_SIZE, thread_name_prefix="proc")


class Program:
    """A started program: its number, its code once it has ended (`returncode`; negative: the signal that ended it), and
    the pipes asked for as asyncio streams (`stdin` a StreamWriter, `stdout` and `stderr` StreamReaders), None otherwise."""

    def __init__(self, popen: subprocess.Popen, pidfd: int, group: str | None, name: str, limit: int) -> None:
        self.pid = popen.pid
        self.returncode: int | None = None
        self.stdin: asyncio.StreamWriter | None = None
        self.stdout: asyncio.StreamReader | None = None
        self.stderr: asyncio.StreamReader | None = None
        self._popen, self._pidfd, self._group, self._name, self._limit = popen, pidfd, group, name, limit
        self._end: asyncio.Future[int] | None = None


class _PipeReader(asyncio.StreamReaderProtocol):
    """A pipe of a program read into a stream; the pipe closes at its end of data."""

    def eof_received(self) -> bool:
        super().eof_received()
        return False  # uvloop keeps a transport open at the end when its protocol asks for it, as this base class does


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


def _remove_group(group: str | None) -> None:
    if group is not None:
        with contextlib.suppress(OSError):
            os.rmdir(group)


def _start(cmd: tuple[str, ...], memory_max: int, kwargs: dict) -> Program:
    """In the pool: the group of the program, the program in it (the shell puts itself there, then becomes the program,
    so the program never runs outside), and a pidfd of it."""
    group = _group_for(memory_max)
    # Each step stands alone: a program that could not enter its group still runs, first in line for the kernel.
    enter = 'echo $$ 2>/dev/null > "$0/cgroup.procs"; ' if group is not None else ""
    try:
        popen = subprocess.Popen((SHELL, "-c", enter + _BECOME, group or "program", *cmd), bufsize=0, **kwargs)
    except BaseException:
        _remove_group(group)
        raise
    try:
        pidfd = os.pidfd_open(popen.pid)  # the child is not reaped yet, so the number is still its own
    except BaseException:
        popen.kill()
        popen.wait()
        for pipe in (popen.stdin, popen.stdout, popen.stderr):
            if pipe is not None:
                pipe.close()
        _remove_group(group)
        raise
    return Program(popen, pidfd, group, os.path.basename(cmd[0]) if cmd else "?", memory_max)


async def spawn(*cmd: str, memory_max: int = PROGRAM_MEMORY_MAX, **kwargs) -> Program:
    """Start a program; the holds of this task last until the program has ended, however its owner leaves. The program
    goes into its own group of memory (`isolate`) and is the first the kernel takes when the machine runs out of memory."""
    started = asyncio.get_running_loop().run_in_executor(_pool, _start, cmd, memory_max, kwargs)
    try:
        started_program = await asyncio.shield(started)
    except asyncio.CancelledError:
        # The start goes on in its thread: the program it gives is stopped, and the holds last until it has ended.
        abandoned = asyncio.ensure_future(_abandoned(started))
        storage.attach_to_holds(lambda: abandoned)
        raise
    started_program._end = asyncio.ensure_future(_ending(started_program))
    storage.attach_to_holds(lambda: started_program._end)
    try:
        await _connect(started_program)
    except BaseException:
        asyncio.ensure_future(stop(started_program))
        raise
    return started_program


async def _connect(started: Program) -> None:
    """The pipes of the program as asyncio streams; on a failure, the ones not connected yet are closed."""
    loop = asyncio.get_running_loop()
    popen = started._popen
    pending = [pipe for pipe in (popen.stdin, popen.stdout, popen.stderr) if pipe is not None]
    try:
        if popen.stdin is not None:
            transport, protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, popen.stdin)
            pending.remove(popen.stdin)
            started.stdin = asyncio.StreamWriter(transport, protocol, None, loop)
        for name in ("stdout", "stderr"):
            pipe = getattr(popen, name)
            if pipe is not None:
                reader = asyncio.StreamReader(limit=READ_LIMIT, loop=loop)
                await loop.connect_read_pipe(lambda reader=reader: _PipeReader(reader, loop=loop), pipe)
                pending.remove(pipe)
                setattr(started, name, reader)
    except BaseException:
        for pipe in pending:  # a connected pipe belongs to its transport, which closes it
            pipe.close()
        raise


async def _abandoned(started: asyncio.Future) -> None:
    """A program whose start its owner left: stopped once it has started; its pipes have nobody to read them."""
    try:
        left = await started
    except Exception:
        return  # nothing started
    for pipe in (left._popen.stdin, left._popen.stdout, left._popen.stderr):
        if pipe is not None:
            pipe.close()
    left._end = asyncio.ensure_future(_ending(left))
    await stop(left)


async def _ending(started: Program) -> int:
    """The end of a program: its pidfd turns readable; then its status and its group are taken in the pool."""
    loop = asyncio.get_running_loop()
    readable = loop.create_future()
    loop.add_reader(started._pidfd, lambda: readable.done() or readable.set_result(None))
    try:
        await readable
    finally:
        loop.remove_reader(started._pidfd)
    finished = loop.run_in_executor(_pool, _finish, started)
    # The pidfd is closed in the loop's own thread, the one `stop` signals from, so no number of it is ever reused under it.
    finished.add_done_callback(lambda _: _close_pidfd(started))
    started.returncode = await asyncio.shield(finished)
    if started.stdin is not None and not started.stdin.is_closing():  # an ended program reads no more
        started.stdin.close()
    return started.returncode


def _close_pidfd(started: Program) -> None:
    pidfd, started._pidfd = started._pidfd, -1
    with contextlib.suppress(OSError):
        os.close(pidfd)


def _code(info: os.waitid_result) -> int:
    return info.si_status if info.si_code == os.CLD_EXITED else -info.si_status


def _finish(started: Program) -> int:
    """In the pool, once the program has ended: its status (the child is reaped), then its group goes, a kill for memory
    there said in the journal with its program."""
    try:
        code = _code(os.waitid(os.P_PIDFD, started._pidfd, os.WEXITED))
    except ChildProcessError:
        _log.warning("программа pid %d завершилась, но её статус забрало чужое ожидание: код выхода неизвестен", started.pid)
        code = LOST
    # A record of the standard library left without a code is taken for a child still to be waited for, and its next
    # start of any program would wait for that number, which by then may be another process's.
    started._popen.returncode = code
    group, started._group = started._group, None
    if group is not None:
        with contextlib.suppress(OSError, ValueError):
            with open(os.path.join(group, "memory.events"), encoding="utf-8") as stream:
                events = dict(line.split() for line in stream if line.strip())
            if int(events.get("oom_kill", 0)):
                _log.warning("программа %s убита: не хватило памяти (предел её группы %d МБ)",
                             started._name, started._limit >> 20)
        _remove_group(group)
    return code


async def exited(started: Program) -> int:
    """The exit code of the program once it has ended (negative: the signal that ended it; LOST: ended, status unknown)."""
    return await asyncio.shield(started._end)


async def stop(started: Program) -> int:
    """Kill the program unless it has ended, and return its code once it has."""
    if started.returncode is None and started._pidfd >= 0:
        with contextlib.suppress(ProcessLookupError, OSError):
            signal.pidfd_send_signal(started._pidfd, signal.SIGKILL)
    return await exited(started)


async def run_capture(cmd: list[str], *, timeout: float, data: bytes | None = None) -> tuple[int, bytes, bytes]:
    """(return code, stdout, stderr) of a finished program; raises TimeoutError at the deadline. `data` is given to it
    as its standard input."""
    proc = await spawn(*cmd, stdin=subprocess.PIPE if data is not None else None,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

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
    proc = await spawn(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    async def lines() -> None:
        while line := await proc.stdout.readline():
            await on_line(line.decode("utf-8", errors="replace").strip())

    try:
        _, err, code = await asyncio.wait_for(asyncio.gather(lines(), proc.stderr.read(), exited(proc)), timeout)
    except BaseException:
        await _reap(proc)
        raise
    return code, err


async def _reap(proc: Program) -> None:
    """Stop a program whose wait was given up; its end is waited for a short while only, the caller is leaving."""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(stop(proc)), REAP_TIMEOUT_S)
