"""The downloader of the service takes a stream of a provider as the provider gave it (contracts/provider_socket.md, `StreamReply`):
the headers of the stream go to the playlist and to every segment, and a separate audio address is downloaded with the picture —
a picture with no audio is no episode. The streams here are the replies a provider gives, made by hand: the downloader is
this repository's, the provider is not."""
import http.server
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


def mpd():
    return '''<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">
    <Period><AdaptationSet mimeType="video/mp4"><Representation id="video_120">
    <BaseURL>video.mp4?a=1&amp;b=2</BaseURL><SegmentBase indexRange="944-3887"><Initialization range="0-943"/>
    </SegmentBase></Representation></AdaptationSet><AdaptationSet mimeType="audio/mp4">
    <Representation id="audio_30257"><BaseURL>audio.mp4?a=3&amp;b=4</BaseURL><SegmentBase indexRange="956-3879"/>
    </Representation></AdaptationSet></Period></MPD>'''


@pytest.fixture(scope='module')
def media(tmp_path_factory):
    _ffmpeg()
    folder = tmp_path_factory.mktemp('dash')
    for name, source, opts in [('video.mp4', 'color=size=640x360:rate=10', ['-c:v', 'libx264', '-threads', '1']),
                               ('audio.mp4', 'sine=frequency=440:sample_rate=48000', ['-c:a', 'aac'])]:
        made = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', source, '-t', '3', *opts,
                               '-movflags', '+faststart', str(folder / name)], capture_output=True, timeout=30)
        assert made.returncode == 0, made.stderr.decode()
    return folder


@pytest.fixture
def serve(media):
    asked = []

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kw):
            super().__init__(*args, directory=str(media), **kw)

        def log_message(self, *args):
            pass

        def do_GET(self):
            asked.append((self.path, self.headers.get('Referer')))
            if self.headers.get('Referer') != 'https://player.test/':
                self.send_error(403)
                return
            super().do_GET()

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_address[1]}', asked
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.asyncio
async def test_a_stream_with_a_separate_audio_is_downloaded_with_it(serve, tmp_path):
    base, asked = serve
    headers = {'Referer': 'https://player.test/'}
    target = tmp_path / 'output.mp4'
    ok, error = await ytdlp.download(base + '/video.mp4?a=1&b=2', str(target), noop, headers=headers,
                                     audio_url=base + '/audio.mp4?a=3&b=4')
    assert (ok, error) == (True, '')
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(target)],
                            capture_output=True, text=True, timeout=20)
    streams = json.loads(result.stdout)['streams']
    assert {s['codec_type'] for s in streams} == {'video', 'audio'}
    assert next(s['height'] for s in streams if s['codec_type'] == 'video') == 360
    assert min(float(s['duration']) for s in streams) >= 2.9
    assert all(referer == headers['Referer'] for _, referer in asked)
    assert any(path.startswith('/audio.mp4') for path, _ in asked)


@pytest.mark.asyncio
async def test_missing_audio_cannot_succeed_as_a_silent_video(serve, tmp_path):
    base, _ = serve
    target = tmp_path / 'missing.mp4'
    ok, error = await ytdlp.download(base + '/video.mp4', str(target), noop,
                                     headers={'Referer': 'https://player.test/'}, audio_url=base + '/missing.mp4')
    assert not ok and error and not target.exists()


@pytest.mark.asyncio
async def test_a_longer_video_does_not_conceal_incomplete_audio(serve, tmp_path, monkeypatch):
    base, _ = serve

    # An audio origin that announces six seconds but sends only the first three.
    async def source_length(url, headers):
        return 6.0 if 'audio' in url else 3.0

    monkeypatch.setattr(ytdlp, '_probe_duration_ffprobe', source_length)
    target = tmp_path / 'cut-audio.mp4'
    ok, error = await ytdlp.download(base + '/video.mp4', str(target), noop,
                                     headers={'Referer': 'https://player.test/'}, audio_url=base + '/audio.mp4')
    assert not ok and error and not target.exists()
    assert not (tmp_path / 'cut-audio.mp4.download.mp4').exists()
