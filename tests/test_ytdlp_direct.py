"""Direct ffmpeg download of a web player's stream: reconnects, completeness, stalls."""
import asyncio
import os
import stat
from unittest.mock import patch

import pytest

from modules.downloads import ytdlp
from modules.downloads.module import _transient_download_error
from proc_helpers import Spy, alive, stalled_downloader

FFMPEG = """#!/bin/bash
echo "$@" > "$FAKE_DIR/ffmpeg_args"
out="${@: -1}"
# The stalled writer must be one process, as the real ffmpeg is: a child left by a killed shell keeps the pipe open.
if [ -f "$FAKE_DIR/stall" ]; then exec sleep 30; fi
# The line ffmpeg prints at a refusal of an HLS segment (also of one the next look took), followed by more warnings than the
# tail of its output keeps.
if [ -f "$FAKE_DIR/refuse" ]; then
  echo "[hls @ 0x55] Failed to open segment 2 of playlist 0" >&2
  for i in $(seq 1 80); do echo "[h264 @ 0x56] Packet corrupt ($i)" >&2; done
fi
echo "progress=end"
echo data > "$out"
exit $(cat "$FAKE_DIR/ffmpeg_rc" 2>/dev/null || echo 0)
"""

# The stream announces `remote_duration`; the downloaded file measures `local_duration`.
FFPROBE = """#!/bin/bash
last="${@: -1}"
case "$last" in
  http*) cat "$FAKE_DIR/remote_duration" ;;
  *) cat "$FAKE_DIR/local_duration" ;;
esac
"""


@pytest.fixture
def fake(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("ffmpeg", FFMPEG), ("ffprobe", FFPROBE)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("FAKE_DIR", str(state))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(ytdlp, "PROBE_RETRY_S", 0.0)

    def set_durations(remote, local):
        (state / "remote_duration").write_text(str(remote))
        (state / "local_duration").write_text(str(local))

    set_durations(1200, 1200)
    return state, set_durations


async def noop(_data):
    pass


@pytest.mark.asyncio
async def test_a_network_stream_is_read_with_reconnects_and_the_headers(fake, tmp_path):
    state, _ = fake
    target = str(tmp_path / "out" / "e.mp4")
    os.makedirs(os.path.dirname(target))
    ok, error = await ytdlp._download_direct_ffmpeg(
        "https://cdn.test/v/720.m3u8", {"Referer": "https://kodikplayer.com/"}, target, noop)
    assert (ok, error) == (True, "") and os.path.isfile(target)
    args = (state / "ffmpeg_args").read_text().split()
    assert args[args.index("-i") - 1] != "-reconnect" and "-reconnect" in args
    assert args.index("-reconnect") < args.index("-i")
    for flag in ("-reconnect_streamed", "-reconnect_on_network_error", "-reconnect_delay_max"):
        assert args.index(flag) < args.index("-i")
    assert args[args.index("-seg_max_retry") + 1] == str(ytdlp.SEGMENT_RETRIES) and args.index("-seg_max_retry") < args.index("-i")
    assert "Referer: https://kodikplayer.com/" in (state / "ffmpeg_args").read_text()


@pytest.mark.asyncio
async def test_a_local_manifest_gets_no_network_options_but_asks_for_its_segments_again(fake, tmp_path):
    state, _ = fake
    target = str(tmp_path / "e.mp4")
    ok, _ = await ytdlp._download_direct_ffmpeg("file:/tmp/m.m3u8", {}, target, noop)
    args = (state / "ffmpeg_args").read_text()
    assert ok and "-reconnect" not in args and "-seg_max_retry" in args


@pytest.mark.asyncio
async def test_a_progressive_file_gets_no_option_of_the_hls_demuxer(fake, tmp_path):
    state, _ = fake
    ok, _ = await ytdlp._download_direct_ffmpeg("https://cdn.test/v/720.mp4?sign=1", {"Referer": "x"}, str(tmp_path / "e.mp4"), noop)
    assert ok and "-seg_max_retry" not in (state / "ffmpeg_args").read_text()


@pytest.mark.asyncio
async def test_a_broken_off_stream_is_not_taken_for_a_finished_episode(fake, tmp_path):
    _, set_durations = fake
    set_durations(1200, 300)  # ffmpeg exits 0, yet only a quarter of the stream arrived
    target = str(tmp_path / "e.mp4")
    ok, error = await ytdlp._download_direct_ffmpeg("https://cdn.test/v.m3u8", {"Referer": "x"}, target, noop)
    assert not ok and "обрыв" in error and "300" in error and "1200" in error
    assert not os.path.exists(target) and not os.path.exists(target + ".download.mp4")
    assert _transient_download_error(error)


@pytest.mark.asyncio
async def test_a_refusal_of_a_segment_that_the_next_look_took_leaves_a_whole_download_whole(fake, tmp_path):
    state, _ = fake
    (state / "refuse").write_text("1")  # ffmpeg said it failed to open segment 2, and the file holds the whole announced length
    target = str(tmp_path / "e.mp4")
    ok, error = await ytdlp._download_direct_ffmpeg("https://cdn.test/v.m3u8", {"Referer": "x"}, target, noop)
    assert (ok, error) == (True, "") and os.path.isfile(target)


@pytest.mark.asyncio
async def test_a_file_short_of_the_announced_length_by_a_segment_is_incomplete_and_names_the_refused_segment(fake, tmp_path):
    state, set_durations = fake
    (state / "refuse").write_text("1")
    set_durations(1281.78, 1267.6)  # the length of a copy the service refused as a foreign video, against the whole one
    target = str(tmp_path / "e.mp4")
    ok, error = await ytdlp._download_direct_ffmpeg("https://cdn.test/v.m3u8", {"Referer": "x"}, target, noop)
    assert not ok and "обрыв" in error and "№2" in error and "1268" in error and "1282" in error
    assert not os.path.exists(target) and not os.path.exists(target + ".download.mp4")
    assert _transient_download_error(error)


@pytest.mark.asyncio
async def test_a_download_is_complete_up_to_the_pause_that_names_a_lost_piece(fake, tmp_path):
    _, set_durations = fake
    set_durations(1200, 1200 - ytdlp.HOLE_S)
    target = str(tmp_path / "e.mp4")
    ok, _ = await ytdlp._download_direct_ffmpeg("https://cdn.test/v.m3u8", {"Referer": "x"}, target, noop)
    assert ok
    set_durations(1200, 1200 - ytdlp.HOLE_S - 0.5)
    other = str(tmp_path / "f.mp4")
    ok, error = await ytdlp._download_direct_ffmpeg("https://cdn.test/v.m3u8", {"Referer": "x"}, other, noop)
    assert not ok and "обрыв" in error and not os.path.exists(other)


@pytest.mark.asyncio
async def test_an_unreadable_result_fails_even_when_the_stream_length_is_unknown(fake, tmp_path):
    _, set_durations = fake
    set_durations(0, 0)
    target = str(tmp_path / "e.mp4")
    ok, error = await ytdlp._download_direct_ffmpeg("https://cdn.test/v.m3u8", {"Referer": "x"}, target, noop)
    assert not ok and "обрыв" in error and not os.path.exists(target)


@pytest.mark.asyncio
async def test_without_a_known_stream_length_a_readable_file_is_accepted(fake, tmp_path):
    _, set_durations = fake
    set_durations(0, 600)
    ok, _ = await ytdlp._download_direct_ffmpeg("https://cdn.test/v.m3u8", {"Referer": "x"},
                                                str(tmp_path / "e.mp4"), noop)
    assert ok


@pytest.mark.asyncio
async def test_a_stalled_ffmpeg_is_stopped_and_counted_as_a_transient_failure(fake, tmp_path, monkeypatch):
    state, _ = fake
    (state / "stall").write_text("1")
    monkeypatch.setattr(ytdlp, "STALL_TIMEOUT_S", 0.3)
    target = str(tmp_path / "e.mp4")
    ok, error = await ytdlp._download_direct_ffmpeg("https://cdn.test/v.m3u8", {"Referer": "x"}, target, noop)
    assert not ok and "нет прогресса" in error and _transient_download_error(error)
    assert not os.path.exists(target + ".download.mp4")


@pytest.mark.asyncio
async def test_the_stream_length_is_probed_again_after_a_failed_look(fake, tmp_path, monkeypatch):
    state, set_durations = fake
    calls = []
    real = ytdlp.asyncio.create_subprocess_exec

    async def flaky(*cmd, **kw):
        if "ffprobe" in cmd[0] and cmd[-1].startswith("http") and len(calls) < 2:
            calls.append(1)
            return await real("bash", "-c", "exit 1", **kw)
        return await real(*cmd, **kw)

    monkeypatch.setattr(ytdlp.asyncio, "create_subprocess_exec", flaky)
    set_durations(1200, 300)
    ok, error = await ytdlp._download_direct_ffmpeg("https://cdn.test/v.m3u8", {"Referer": "x"},
                                                    str(tmp_path / "e.mp4"), noop)
    assert len(calls) == 2 and not ok and "обрыв" in error  # the third look saw the real length


@pytest.mark.asyncio
async def test_a_silent_downloader_is_stopped_and_counted_as_a_transient_failure(tmp_path, monkeypatch):
    spy = Spy()
    monkeypatch.setattr(ytdlp, "STALL_TIMEOUT_S", 0.3)
    with patch("modules.downloads.ytdlp.asyncio.create_subprocess_exec", spy), \
            patch.object(ytdlp.shutil, "which", return_value=stalled_downloader(tmp_path)):
        result = await asyncio.wait_for(
            ytdlp._phase_download("https://vk.test/v", str(tmp_path / "e.mp4"), noop, 1, ytdlp.HLS_FORMAT), 10)
    assert result[0] is False and "нет прогресса" in result[1] and _transient_download_error(result[1])
    assert not alive(spy.started[0].pid)


@pytest.mark.asyncio
async def test_a_silent_remux_is_stopped_and_leaves_no_partial_file(tmp_path, monkeypatch):
    spy = Spy()
    monkeypatch.setattr(ytdlp, "STALL_TIMEOUT_S", 0.3)
    target = str(tmp_path / "e.mp4")
    with patch("modules.downloads.ytdlp.asyncio.create_subprocess_exec", spy), \
            patch.object(ytdlp.shutil, "which", return_value=stalled_downloader(tmp_path, "ffmpeg")):
        ok, error = await asyncio.wait_for(ytdlp._phase_remux(str(tmp_path / "raw.ts"), target, noop, 10.0), 10)
    assert not ok and "нет прогресса" in error and _transient_download_error(error)
    assert not alive(spy.started[0].pid) and not os.path.exists(target + ".remux.mp4")


@pytest.mark.asyncio
async def test_a_download_does_not_start_on_a_name_an_unfinished_removal_still_owns(tmp_path):
    import threading
    from core import storage

    release = threading.Event()
    target = str(tmp_path / "e.mp4")
    with pytest.raises(storage.StorageTimeout):
        await storage.call(lambda: release.wait(5), timeout=0.05, owns=target + ".remux.mp4")
    ok, error = await ytdlp.download("https://cdn.test/v.m3u8", target, noop, headers={"Referer": "x"})
    assert not ok and "занят" in error and _transient_download_error(error)
    release.set()


@pytest.mark.asyncio
async def test_a_download_owns_its_name_from_its_first_step_to_the_publication(fake, tmp_path, monkeypatch):
    from core import storage
    state, _ = fake
    target = str(tmp_path / "out" / "episode.mp4")
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def probe(*args):
        entered.set()
        await proceed.wait()
        return 1200

    monkeypatch.setattr(ytdlp, "_probe_duration_ffprobe", probe)
    download = asyncio.create_task(ytdlp.download("https://cdn.test/e.mp4", target, noop))
    await asyncio.wait_for(entered.wait(), 3)
    assert storage.busy(target) and storage.busy(target + ".download.mp4")
    with pytest.raises(storage.StorageBusy):
        await storage.call(lambda: None, owns=os.path.dirname(target))
    proceed.set()
    assert await asyncio.wait_for(download, 10) == (True, "")
    assert os.path.isfile(target) and not storage.busy(target)


@pytest.mark.asyncio
async def test_a_download_does_not_start_a_program_where_an_operation_has_not_ended(fake, tmp_path):
    import threading
    from core import storage
    state, _ = fake
    release = threading.Event()
    target = str(tmp_path / "out" / "episode.mp4")
    with pytest.raises(storage.StorageTimeout):
        await storage.call(lambda: release.wait(5), owns=os.path.dirname(target), timeout=0.02)
    try:
        ok, error = await ytdlp.download("https://cdn.test/e.mp4", target, noop)
        assert not ok and _transient_download_error(error)
        assert not (state / "ffmpeg_args").exists(), "no program was started"
    finally:
        release.set()


class StartedSpy(Spy):
    """A Spy that says when the writer has been started: ffprobe runs before it, and is not the one to be waited for."""

    def __init__(self):
        super().__init__()
        self.spawned = asyncio.Event()

    async def __call__(self, *args, **kwargs):
        process = await super().__call__(*args, **kwargs)
        if os.path.basename(args[0]) == "ffmpeg":
            self.spawned.set()
        return process


@pytest.mark.asyncio
async def test_a_cancelled_download_keeps_its_name_until_the_program_is_dead(fake, tmp_path):
    from core import storage
    state, _ = fake
    (state / "stall").write_text("1")
    spy = StartedSpy()
    target = str(tmp_path / "out" / "episode.mp4")
    with patch("modules.downloads.ytdlp.asyncio.create_subprocess_exec", spy):
        download = asyncio.create_task(ytdlp.download("https://cdn.test/e.mp4", target, noop))
        await asyncio.wait_for(spy.spawned.wait(), 5)
        writer = spy.started[-1]
        assert alive(writer.pid) and storage.busy(target)
        download.cancel()
        with pytest.raises(asyncio.CancelledError):
            await download
    assert not alive(writer.pid)
    # The hold ends after the program has ended, not with the cancelled wait: the storage says when the name is free.
    free = asyncio.Event()
    storage.when_free(target, free.set)
    await asyncio.wait_for(free.wait(), 5)
    assert not storage.busy(target)
