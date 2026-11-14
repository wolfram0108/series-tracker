"""The headers of a stream a player resolved go to the playlist and to every segment: a host that asks for an agent refuses the
segments of a request without it."""
import json
import shutil
import subprocess
import threading
from http import server as http_server

import pytest

from modules.downloads import ytdlp

EXO = 'ExoPlayerLib/2.19.1 (Linux; Android 11)'
PLAYLIST = '#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nsegment.ts\n#EXT-X-ENDLIST\n'


async def noop(_):
    pass


def _ffmpeg() -> None:
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('needs ffmpeg and ffprobe')


@pytest.fixture
def segment():
    _ffmpeg()
    result = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=320x240:rate=25',
                             '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '2', '-c:v', 'mpeg2video', '-threads', '1',
                             '-c:a', 'mp2', '-f', 'mpegts', 'pipe:1'], capture_output=True, check=True, timeout=20)
    return result.stdout


@pytest.mark.asyncio
async def test_the_headers_of_a_stream_reach_the_playlist_and_every_segment(segment, tmp_path):
    stream = {'stream_url': None, 'headers': {'User-Agent': EXO}, 'playlist': True, 'progressive': False}
    asked = []

    class Handler(http_server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            asked.append((self.path, self.headers.get('User-Agent'), self.headers.get('Referer')))
            if self.headers.get('User-Agent') != EXO or self.headers.get('Referer'):
                self.send_error(403)
                return
            body = PLAYLIST.encode() if self.path.endswith('m3u8') else segment
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http_server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    target = tmp_path / 'movie.mp4'
    try:
        ok, error = await ytdlp.download(f'http://127.0.0.1:{server.server_address[1]}/index.m3u8', str(target),
                                         noop, headers=stream['headers'], playlist=stream['playlist'], threads=1)
        assert ok, error
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    data = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(target)],
                          capture_output=True, text=True, check=True, timeout=20)
    assert {s['codec_type'] for s in json.loads(data.stdout)['streams']} == {'video', 'audio'}
    assert any(path == '/segment.ts' for path, _, _ in asked)
    assert all(agent == EXO and referer is None for _, agent, referer in asked)
