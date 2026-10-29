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
        await asyncio.sleep(0.3)
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
    with patch("core.ffprobe.shutil.which", return_value=None):
        assert await duration_of("/any/file.mp4") == 0.0


@pytest.mark.asyncio
async def test_the_duration_query_of_a_stalled_downloader_ends_with_unknown_and_stops_it(tmp_path):
    from modules.downloads import ytdlp
    spy = Spy()
    with patch("core.proc.asyncio.create_subprocess_exec", spy), \
            patch.object(ytdlp.shutil, "which", return_value=stalled_downloader(tmp_path)), \
            patch.object(ytdlp, "PROBE_TIMEOUT_S", 0.3):
        assert await asyncio.wait_for(ytdlp._probe_duration("https://example.test/v"), 10) == 0.0
    assert not alive(spy.started[0].pid)


@pytest.mark.asyncio
async def test_the_chapters_query_of_a_stalled_downloader_fails_and_stops_it(tmp_path):
    from modules.slicing import chapters
    spy = Spy()
    with patch("core.proc.asyncio.create_subprocess_exec", spy), \
            patch.object(chapters.shutil, "which", return_value=stalled_downloader(tmp_path)), \
            patch.object(chapters, "YTDLP_TIMEOUT", 0.3):
        with pytest.raises(RuntimeError):
            await chapters.fetch_chapters("https://example.test/v")
    assert not alive(spy.started[0].pid)
