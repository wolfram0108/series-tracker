"""The verdict on a stream copy is the file's: the real ffmpeg on a local HLS server that refuses segments.

ffmpeg prints «Failed to open segment» at every refusal, also at one its next look took, and a segment refused for good ends
the stream there with exit code 0; only the length of the file tells the two apart."""
import http.server
import os
import shutil
import subprocess
import threading

import pytest

from core.reasons import parse
from modules.downloads import ytdlp

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")

SECONDS = 8
BAD = "v3.ts"


@pytest.fixture(scope="module")
def stream(tmp_path_factory):
    folder = tmp_path_factory.mktemp("hls")
    made = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", str(SECONDS),
         "-c:v", "libx264", "-g", "10", "-sc_threshold", "0", "-f", "hls", "-hls_time", "1", "-hls_playlist_type", "vod",
         str(folder / "v.m3u8")], capture_output=True)
    if made.returncode != 0:
        pytest.skip("this ffmpeg has no libx264 to make a test stream")
    assert BAD in os.listdir(folder)
    return folder


@pytest.fixture
def serve(stream):
    """A server of the stream whose segment BAD is refused with HTTP 500 for its first `refusals` asks."""
    servers = []

    def start(refusals: int) -> str:
        asked = {"n": 0}

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(stream), **kwargs)

            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path.strip("/") == BAD:
                    asked["n"] += 1
                    if asked["n"] <= refusals:
                        self.send_error(500)
                        return
                super().do_GET()

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}/v.m3u8"

    yield start
    for server in servers:
        server.shutdown()


async def noop(_data):
    pass


@pytest.mark.asyncio
async def test_a_segment_refused_fewer_times_than_ffmpeg_looks_leaves_a_whole_file(serve, tmp_path):
    target = str(tmp_path / "e.mp4")
    ok, error = await ytdlp._download_direct_ffmpeg(serve(ytdlp.SEGMENT_RETRIES), {"Referer": "x"}, target, noop)
    assert (ok, error) == (True, "")
    assert await ytdlp._local_duration(target) == pytest.approx(SECONDS, abs=ytdlp.HOLE_S)


@pytest.mark.asyncio
async def test_a_segment_refused_for_good_ends_the_stream_short_and_the_download_is_incomplete(serve, tmp_path):
    target = str(tmp_path / "e.mp4")
    ok, error = await ytdlp._download_direct_ffmpeg(serve(10 ** 6), {"Referer": "x"}, target, noop)
    assert not ok and parse(error)["code"] == "cut_short_refused" and parse(error)["params"]["first"] == 3
    assert not os.path.exists(target) and not os.path.exists(target + ".download.mp4")
