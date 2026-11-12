"""JD4K's playlist accepts ordinary requests; its Chaoxing segments require the agent of Juzi's own player."""
from http import server as http_server
import json
import shutil
import subprocess
import threading
from types import SimpleNamespace

import httpx
import pytest

from modules.sources.apps import AppStream
from modules.sources.players.app_lines import AppPlayer
from modules.sources.players.contract import Kind, PlayerRef
from modules.downloads import ytdlp

EXO = 'ExoPlayerLib/2.19.1 (Linux; Android 11)'
PLAYLIST = '#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nhttps://p.ananas.chaoxing.com/segment.ts\n#EXT-X-ENDLIST\n'


@pytest.fixture
def segment():
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('needs ffmpeg and ffprobe')
    result = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=320x240:rate=25',
        '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '2', '-c:v', 'mpeg2video', '-threads', '1',
        '-c:a', 'mp2', '-f', 'mpegts', 'pipe:1'], capture_output=True, check=True, timeout=20)
    return result.stdout


@pytest.mark.asyncio
async def test_player_selects_headers_and_downloader_uses_them_for_playlist_and_segments(segment, tmp_path):
    def respond(request):
        if request.url.host == 'playlist.test':
            return httpx.Response(200, text=PLAYLIST)
        assert request.url.host == 'p.ananas.chaoxing.com'
        if request.headers.get('User-Agent') != EXO or 'Referer' in request.headers:
            return httpx.Response(403)
        return httpx.Response(200, content=segment)
    async def resolve(*args):
        return AppStream('https://playlist.test/index.m3u8', {})
    player = AppPlayer(SimpleNamespace(profile=SimpleNamespace(name='juzi', label='Juzi', settings={'player_agent': EXO}), resolve=resolve))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        answer = await player.fetch(PlayerRef('line:JD4K', 'app://juzi/1#line=JD4K&item=t'), http, '')
        assert player.read(answer).kind is Kind.PLAYABLE and player.read(answer).resolutions == (240,)
        stream = await player.stream('app://juzi/1#line=JD4K&item=t', http, None, None)
    assert stream['headers'] == {'User-Agent': EXO} and stream['playlist'] and not stream['progressive']

    # Exercise the real HLS downloader separately on a local origin with the same header requirement.
    asked = []
    class Handler(http_server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            asked.append((self.path, self.headers.get('User-Agent'), self.headers.get('Referer')))
            if self.headers.get('User-Agent') != EXO or self.headers.get('Referer'):
                self.send_error(403)
                return
            body = PLAYLIST.replace('https://p.ananas.chaoxing.com/segment.ts', 'segment.ts').encode() if self.path.endswith('m3u8') else segment
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = http_server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    async def progress(_):
        pass
    target = tmp_path / 'movie.mp4'
    try:
        ok, error = await ytdlp.download(f'http://127.0.0.1:{server.server_address[1]}/index.m3u8', str(target),
                                        progress, headers=stream['headers'], playlist=True, threads=1)
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


@pytest.mark.parametrize('given', [{'User-Agent': 'given'}, {'user-agent': 'given'}])
def test_an_agent_the_application_gave_with_the_stream_is_kept(given):
    player = AppPlayer(SimpleNamespace(profile=SimpleNamespace(name='juzi', label='Juzi', settings={'player_agent': EXO})))
    assert player.playback(given) == given
    assert player.playback({'Referer': 'https://given/'}) == {'Referer': 'https://given/', 'User-Agent': EXO}


def test_an_application_without_its_player_agent_keeps_the_headers_it_gave():
    player = AppPlayer(SimpleNamespace(profile=SimpleNamespace(name='appmn', label='AppMN', settings={})))
    assert player.playback({'Referer': 'https://given/'}) == {'Referer': 'https://given/'}


@pytest.mark.asyncio
async def test_a_missing_segment_stays_missing_with_the_selected_agent():
    def respond(request):
        if request.url.host == 'playlist.test':
            return httpx.Response(200, text=PLAYLIST)
        assert request.headers['User-Agent'] == EXO
        return httpx.Response(404)
    async def resolve(*args):
        return AppStream('https://playlist.test/index.m3u8', {})
    player = AppPlayer(SimpleNamespace(profile=SimpleNamespace(name='juzi', label='Juzi', settings={'player_agent': EXO}),
                                       resolve=resolve))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        answer = await player.fetch(PlayerRef('line:JD4K', 'app://juzi/1#line=JD4K&item=t'), http, '')
    assert player.read(answer).kind is Kind.NOT_FOUND and answer.status == 404
