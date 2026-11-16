"""Short external programs: results, the deadline and the cancellation both stop the process."""
import asyncio
import os
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

from core import proc
from core.ffprobe import duration_of
from proc_helpers import Spy, alive, stalled_downloader
from settle import path_free

SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]


@pytest.mark.asyncio
async def test_a_finished_program_gives_its_code_and_output():
    code, out, err = await proc.run_capture([sys.executable, "-c", "print('hi')"], timeout=10)
    assert (code, out.strip(), err) == (0, b"hi", b"")


@pytest.mark.asyncio
async def test_the_deadline_stops_the_program_it_waited_for():
    spy = Spy()
    with patch("core.proc.asyncio.create_subprocess_exec", spy):
        with pytest.raises(asyncio.TimeoutError):
            await proc.run_capture(SLEEP, timeout=0.3)
    assert not alive(spy.started[0].pid)


@pytest.mark.asyncio
async def test_a_cancelled_wait_stops_the_program_too():
    spy = Spy()
    with patch("core.proc.asyncio.create_subprocess_exec", spy):
        task = asyncio.create_task(proc.run_capture(SLEEP, timeout=60))
        await asyncio.wait_for(spy.spawned.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not alive(spy.started[0].pid)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg is needed")
@pytest.mark.asyncio
async def test_duration_of_a_real_file_and_of_what_cannot_be_read(tmp_path):
    sound = tmp_path / "one-second.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", str(sound)], check=True)
    assert 0.9 < await duration_of(str(sound)) < 1.2
    broken = tmp_path / "broken.flac"
    broken.write_bytes(b"not audio")
    assert await duration_of(str(broken)) == 0.0
    assert await duration_of(str(tmp_path / "missing.flac")) == 0.0


@pytest.mark.asyncio
async def test_without_ffprobe_the_duration_is_unknown_not_an_error():
    with patch("core.ffprobe.program", return_value=None):
        assert await duration_of("/any/file.mp4") == 0.0


@pytest.mark.asyncio
async def test_the_duration_query_of_a_stalled_downloader_ends_with_unknown_and_stops_it(tmp_path):
    from modules.downloads import ytdlp
    spy = Spy()
    with patch("core.proc.asyncio.create_subprocess_exec", spy), \
            patch.object(ytdlp, "program", return_value=stalled_downloader(tmp_path)), \
            patch.object(ytdlp, "PROBE_TIMEOUT_S", 0.3):
        assert await asyncio.wait_for(ytdlp._probe_duration("https://example.test/v"), 10) == 0.0
    assert not alive(spy.started[0].pid)


@pytest.mark.asyncio
async def test_a_program_started_inside_a_hold_keeps_the_hold_until_it_has_ended(tmp_path):
    from core import storage
    path = str(tmp_path / "e.mp4")
    with storage.hold(path):
        process = await proc.spawn(*SLEEP)
    try:
        assert storage.busy(path), "the owner left; the program may still write"
    finally:
        process.kill()
        await process.wait()
    await path_free(path)
    assert not storage.busy(path)


def test_programs_are_started_only_through_core_proc():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = [str(path.relative_to(root)) for folder in ("core", "modules") for path in (root / folder).rglob("*.py")
                 if path.name != "proc.py" and any(call in path.read_text(encoding="utf-8") for call in (
                     "create_subprocess_exec", "create_subprocess_shell", "subprocess.Popen", "subprocess.run"))]
    assert offenders == [], "a program that starts elsewhere escapes the holds of its task"


def test_programs_are_found_only_through_core_proc():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = [str(path.relative_to(root)) for folder in ("core", "modules") for path in (root / folder).rglob("*.py")
                 if path.name != "proc.py" and "shutil.which" in path.read_text(encoding="utf-8")]
    assert offenders == [], "a program looked up on PATH alone misses the one the project installs beside its interpreter"


def test_a_program_beside_the_interpreter_is_taken_before_the_one_on_path(tmp_path, monkeypatch):
    beside, elsewhere = tmp_path / "venv", tmp_path / "path"
    for folder in (beside, elsewhere):
        folder.mkdir()
        tool = folder / "tool"
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o755)
    monkeypatch.setattr(proc.sys, "executable", str(beside / "python"))
    monkeypatch.setenv("PATH", str(elsewhere))
    assert proc.program("tool") == str(beside / "tool")
    (beside / "tool").unlink()
    assert proc.program("tool") == str(elsewhere / "tool")
    assert proc.program("absent") is None


# --- the end of a program is known whoever reaps it --------------------------------------------------------------------
# The service runs on uvloop: its loop learns of the end of a child only from its own wait, so a child reaped by another
# wait is never reported, and `proc.wait()` does not end. `exited` takes the end from the kernel as well.

LOOP_SILENCE_S = 1.0  # how long the loop's silence is watched: the subject of the test, nothing waits for it to pass


def test_under_uvloop_the_end_of_a_child_reaped_elsewhere_is_known_to_exited_though_the_loop_never_tells():
    uvloop = pytest.importorskip("uvloop")

    async def scenario() -> tuple[bool, int]:
        process = await proc.spawn(sys.executable, "-c", "pass")
        os.waitpid(process.pid, 0)  # another wait takes the child before the loop looks
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), LOOP_SILENCE_S)
            told = True
        except asyncio.TimeoutError:
            told = False
        return told, await asyncio.wait_for(proc.exited(process), proc.REAP_TIMEOUT_S + 5)

    assert uvloop.run(scenario()) == (False, proc.LOST)


@pytest.mark.parametrize("loop", ["asyncio", "uvloop"])
def test_a_child_the_loop_reaped_before_its_report_ran_gives_its_code_not_an_unknown_one(loop):
    """The loop reaps a child and reports it by a callback still to run: in between, the number of the child is gone from
    the kernel. The code is the one the loop reports, not «unknown» — a program killed by a signal is not taken for one
    whose end said nothing."""
    runner = asyncio.run if loop == "asyncio" else pytest.importorskip("uvloop").run

    async def scenario() -> int:
        process = await proc.spawn(sys.executable, "-c", "import sys; sys.exit(3)")

        def gone(_pid):
            raise ProcessLookupError

        with patch.object(proc.os, "pidfd_open", gone):
            return await asyncio.wait_for(proc.exited(process), 10)

    assert runner(scenario()) == 3


def test_the_code_of_a_program_comes_from_the_kernel_when_the_wait_of_the_loop_does_not_end():
    uvloop = pytest.importorskip("uvloop")

    async def scenario() -> int:
        process = await proc.spawn(sys.executable, "-c", "import sys; sys.exit(3)")
        process.wait = lambda: asyncio.get_running_loop().create_future()  # a wait that never ends
        return await asyncio.wait_for(proc.exited(process), 5)

    assert uvloop.run(scenario()) == 3


@pytest.mark.asyncio
async def test_stop_kills_a_running_program_and_gives_the_signal_that_ended_it():
    process = await proc.spawn(*SLEEP)
    assert await asyncio.wait_for(proc.stop(process), 5) == -9 and not alive(process.pid)
    assert await proc.stop(process) == -9, "a program that has ended is not signalled again"


def test_a_hold_attached_to_a_program_ends_with_the_program_though_the_loop_never_reports_it(tmp_path):
    uvloop = pytest.importorskip("uvloop")
    from core import storage
    path = str(tmp_path / "e.mp4")

    async def scenario() -> bool:
        with storage.hold(path):
            process = await proc.spawn(sys.executable, "-c", "pass")
        os.waitpid(process.pid, 0)
        await path_free(path, seconds=proc.REAP_TIMEOUT_S + 5)
        return storage.busy(path)

    assert uvloop.run(scenario()) is False


@pytest.mark.asyncio
async def test_the_code_of_an_ended_program_is_written_into_its_record_so_no_later_start_waits_for_its_number():
    """A record of a program left with no code is waited for by the standard library at the start of any other program,
    by its number, which may belong to another program of ours by then: the end the loop knows goes into the record."""
    child = await proc.spawn("sh", "-c", "exit 3", stdout=asyncio.subprocess.PIPE)
    record = child._transport.get_extra_info("subprocess")
    assert await proc.exited(child) == 3 and record.returncode == 3
    await child.stdout.read()

    class Record:
        returncode = None

    class Transport:
        @staticmethod
        def get_extra_info(name):
            return lost if name == "subprocess" else None

    class Child:
        _transport = Transport()

    lost = Record()
    proc._settle(Child(), proc.LOST)
    assert lost.returncode == proc.LOST
