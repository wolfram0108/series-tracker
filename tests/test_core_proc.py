"""Short external programs: results, the deadline and the cancellation both stop the process."""
import asyncio
import os
import sys
from unittest.mock import patch

import pytest

from core import proc
from proc_helpers import Spy, alive, stalled_downloader
from settle import path_free

@pytest.mark.asyncio
async def test_the_duration_query_of_a_stalled_downloader_ends_with_unknown_and_stops_it(tmp_path):
    from modules.downloads import ytdlp
    spy = Spy()
    with patch("core.proc.asyncio.create_subprocess_exec", spy), \
            patch.object(ytdlp, "program", return_value=stalled_downloader(tmp_path)), \
            patch.object(ytdlp, "PROBE_TIMEOUT_S", 0.3):
        assert await asyncio.wait_for(ytdlp._probe_duration("https://example.test/v"), 10) == 0.0
    assert not alive(spy.started[0].pid)


# --- the end of a program is known whoever reaps it --------------------------------------------------------------------
# The service runs on uvloop: its loop learns of the end of a child only from its own wait, so a child reaped by another
# wait is never reported, and `proc.wait()` does not end. `exited` takes the end from the kernel as well.

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
