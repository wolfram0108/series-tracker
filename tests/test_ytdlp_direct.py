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
if [ -f "$FAKE_DIR/stall" ]; then sleep 30; fi
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
    assert "Referer: https://kodikplayer.com/" in (state / "ffmpeg_args").read_text()


@pytest.mark.asyncio
async def test_a_local_manifest_gets_no_network_options(fake, tmp_path):
    state, _ = fake
    target = str(tmp_path / "e.mp4")
    ok, _ = await ytdlp._download_direct_ffmpeg("file:/tmp/m.m3u8", {}, target, noop)
    assert ok and "-reconnect" not in (state / "ffmpeg_args").read_text()


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
async def test_a_download_just_above_the_threshold_passes(fake, tmp_path):
    _, set_durations = fake
    set_durations(1200, 1200 * ytdlp.MIN_COMPLETE_FRACTION + 1)
    ok, _ = await ytdlp._download_direct_ffmpeg("https://cdn.test/v.m3u8", {"Referer": "x"},
                                                str(tmp_path / "e.mp4"), noop)
    assert ok


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
