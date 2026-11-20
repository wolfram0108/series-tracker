"""Direct ffmpeg download of a web player's stream: reconnects, completeness, stalls."""
import asyncio
import os
import stat
from unittest.mock import patch

import pytest

from core.proc import program
from core.reasons import code_of, parse
from modules.downloads import ytdlp
from modules.downloads import failure
from modules.downloads.failure import Failed, kind_of
from proc_helpers import Spy, alive, stalled_downloader

# The codes of a download that ended short or unreadable (modules/downloads/ytdlp.py).
CUT_CODES = {"cut_short", "cut_short_refused", "cut_unreadable", "length_unstated", "file_unreadable"}

FFMPEG = """#!/bin/bash
echo "$@" > "$FAKE_DIR/ffmpeg_args"
out="${@: -1}"
# The stalled writer must be one process, as the real ffmpeg is: a child left by a killed shell keeps the pipe open.
if [ -f "$FAKE_DIR/stall" ]; then exec sleep 30; fi
# Stopped by a signal: as the real ffmpeg says it at a stop of the service, or killed outright.
if [ -f "$FAKE_DIR/signalled" ]; then echo "Exiting normally, received signal 15." >&2; exit 255; fi
if [ -f "$FAKE_DIR/killed" ]; then kill -TERM $$; fi
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

# The stream announces `remote_duration`; the downloaded file measures `local_duration`, its header and its data alike: each
# question answered in the shape the real ffprobe gives (the length of the header, the packets of the tail).
FFPROBE = """#!/bin/bash
last="${@: -1}"
case "$last" in
  http*) cat "$FAKE_DIR/remote_duration"; exit 0 ;;
esac
# A file that is not there is no file of any length, as the real ffprobe says.
if [ ! -e "$last" ]; then echo "$last: No such file or directory" >&2; exit 1; fi
local=$(cat "$FAKE_DIR/local_duration")
case "$*" in
  *format=format_name,duration,start_time*) printf '{"format": {"duration": "%s", "start_time": "0.000000"}, "streams": [{"index": 0, "codec_type": "video", "start_time": "0.000000", "duration": "%s"}]}\\n' "$local" "$local" ;;
  *-read_intervals*) [ "$local" != "0" ] && printf '0,%s,0.000000\\n' "$local" ;;
  *) echo "$local" ;;
esac
exit 0
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
async def test_a_file_short_of_the_announced_length_by_a_segment_is_incomplete_and_names_the_refused_segment(fake, tmp_path):
    state, set_durations = fake
    (state / "refuse").write_text("1")
    set_durations(1281.78, 1267.6)  # the length of a copy the service refused as a foreign video, against the whole one
    target = str(tmp_path / "e.mp4")
    ok, error = await ytdlp._download_direct_ffmpeg("https://cdn.test/v.m3u8", {"Referer": "x"}, target, noop)
    assert not ok and parse(error)["code"] == "cut_short_refused"
    facts = parse(error)["params"]
    assert (facts["first"], facts["got"], facts["expected"]) == (2, "1268", "1282")
    assert not os.path.exists(target) and not os.path.exists(target + ".download.mp4")
    assert kind_of(error) is Failed.BROKEN


@pytest.mark.asyncio
async def test_a_stalled_ffmpeg_is_stopped_and_counted_as_a_transient_failure(fake, tmp_path, monkeypatch):
    state, _ = fake
    (state / "stall").write_text("1")
    monkeypatch.setattr(failure, "SILENCE_S", 0.3)
    target = str(tmp_path / "e.mp4")
    ok, error = await ytdlp._download_direct_ffmpeg("https://cdn.test/v.m3u8", {"Referer": "x"}, target, noop)
    assert not ok and code_of(error) == "program_silent" and kind_of(error) is Failed.BROKEN
    assert not os.path.exists(target + ".download.mp4")


@pytest.mark.asyncio
async def test_a_silent_downloader_is_stopped_and_counted_as_a_transient_failure(tmp_path, monkeypatch):
    spy = Spy()
    monkeypatch.setattr(failure, "SILENCE_S", 0.3)
    with patch("modules.downloads.ytdlp.asyncio.create_subprocess_exec", spy), \
            patch.object(ytdlp, "program", return_value=stalled_downloader(tmp_path)):
        result = await asyncio.wait_for(
            ytdlp._phase_download("https://vk.test/v", str(tmp_path / "e.mp4"), noop, 1, ytdlp.HLS_FORMAT), 10)
    assert result[0] is False and code_of(result[1]) == "program_silent" and kind_of(result[1]) is Failed.BROKEN
    assert not alive(spy.started[0].pid)


@pytest.mark.asyncio
async def test_a_silent_remux_is_stopped_and_leaves_no_partial_file(tmp_path, monkeypatch):
    spy = Spy()
    monkeypatch.setattr(failure, "SILENCE_S", 0.3)
    target = str(tmp_path / "e.mp4")
    with patch("modules.downloads.ytdlp.asyncio.create_subprocess_exec", spy), \
            patch.object(ytdlp, "program", return_value=stalled_downloader(tmp_path, "ffmpeg")):
        ok, error = await asyncio.wait_for(ytdlp._phase_remux(str(tmp_path / "raw.ts"), target, noop, 10.0), 10)
    assert not ok and code_of(error) == "program_silent" and kind_of(error) is Failed.BROKEN
    assert not alive(spy.started[0].pid) and not os.path.exists(target + ".remux.mp4")


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
        assert not ok and kind_of(error) is Failed.UNASKED
        assert not (state / "ffmpeg_args").exists(), "no program was started"
    finally:
        release.set()


# The formats of a page as yt-dlp reads them (VK gives HLS at every height and progressive files at some).
PAGE_FORMATS = [
    {"format_id": f"{kind}-{height}", "url": f"https://cdn.test/{height}.{'m3u8' if kind == 'hls' else 'mp4'}",
     "protocol": "m3u8_native" if kind == "hls" else "https", "ext": "mp4", "height": height, "vcodec": "avc1",
     "acodec": "mp4a"}
    for kind, heights in (("hls", (360, 720, 1080, 2160)), ("url", (480, 1080))) for height in heights]


def picked(tmp_path, selector: str, formats=PAGE_FORMATS) -> str:
    """The format the real yt-dlp picks by the selector from the formats of a page, with no network."""
    import json
    import subprocess
    info = tmp_path / "info.json"
    info.write_text(json.dumps({"id": "x", "title": "x", "extractor": "generic", "extractor_key": "Generic",
                                "webpage_url": "https://vk.test/v", "formats": formats}))
    out = subprocess.run([program("yt-dlp"), "--no-warnings", "--load-info-json", str(info), "-f", selector,
                          "--simulate", "--print", "format_id"], capture_output=True, text=True, timeout=60)
    return out.stdout.strip()
