"""CO4K's on-demand DASH: measured video, signed track addresses, and a real MP4 with audio."""
import http.server
import json
import shutil
import subprocess
import threading
from types import SimpleNamespace

import httpx
import pytest

from core.envelope import Envelope
from modules.downloads import ytdlp
from modules.sources.apps import AppStream
from modules.sources.players.app_lines import AppPlayer, served_as
from modules.sources.players.contract import Kind, PlayerRef
from modules.sources.players.dash import UnsupportedDash, tracks
from modules.sources.module import SourcesModule


def mpd(video='video.mp4?a=1&amp;b=2', audio='audio.mp4?a=3&amp;b=4', extra=''):
    return f'''<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">
    <Period><AdaptationSet mimeType="video/mp4"><Representation id="video_120">
    <BaseURL>{video}</BaseURL><SegmentBase indexRange="944-3887"><Initialization range="0-943"/>
    </SegmentBase>{extra}</Representation></AdaptationSet><AdaptationSet mimeType="audio/mp4">
    <Representation id="audio_30257"><BaseURL>{audio}</BaseURL><SegmentBase indexRange="956-3879"/>
    </Representation></AdaptationSet></Period></MPD>'''


def test_relative_signed_addresses_and_namespaced_xml_are_preserved():
    text = mpd().replace('<Period>', '<BaseURL>../media/</BaseURL><Period><BaseURL>episode/</BaseURL>')
    found = tracks(text, 'https://cdn.test/manifests/show.mpd')
    assert found.videos == ('https://cdn.test/media/episode/video.mp4?a=1&b=2',)
    assert found.audio == 'https://cdn.test/media/episode/audio.mp4?a=3&b=4'


@pytest.mark.parametrize('text', [mpd(extra='<SegmentTemplate media="$Number$.m4s"/>'),
    mpd(extra='<ContentProtection schemeIdUri="drm"/>'), mpd().replace('static', 'dynamic'),
    mpd().replace('</MPD>', '<Period/></MPD>'), mpd(audio='file:///tmp/audio.mp4'),
    mpd(extra='<SegmentBase><Initialization sourceURL="init.mp4"/></SegmentBase>'),
    '<!DOCTYPE MPD [<!ENTITY x "hello">]>' + mpd(), mpd()[:100],
    mpd().replace('</Period>', '<AdaptationSet mimeType="audio/mp4"><Representation><BaseURL>other.mp4</BaseURL>'
                  '</Representation></AdaptationSet></Period>')])
def test_other_presentations_are_refused_explicitly(text):
    with pytest.raises(UnsupportedDash, match='DASH:'):
        tracks(text, 'https://cdn.test/x')


@pytest.mark.asyncio
async def test_a_disguised_manifest_larger_than_a_file_header_is_read_whole():
    text = mpd(extra='<!--' + 'x' * 100_000 + '-->')
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
            200, content=text.encode(), headers={'Content-Type': 'image/png'}))) as http:
        served = await served_as(http, 'https://cdn.test/image', {})
    assert served.kind == 'dash' and served.answer.text == text


@pytest.fixture(scope='module')
def media(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('needs ffmpeg and ffprobe')
    folder = tmp_path_factory.mktemp('dash')
    for name, source, opts in [
        ('video.mp4', 'color=size=640x360:rate=10', ['-c:v', 'libx264', '-threads', '1']),
        ('high.mp4', 'color=size=1280x720:rate=10', ['-c:v', 'libx264', '-threads', '1']),
        ('audio.mp4', 'sine=frequency=440:sample_rate=48000', ['-c:a', 'aac'])]:
        made = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', source, '-t', '3',
                               *opts, '-movflags', '+faststart', str(folder / name)], capture_output=True, timeout=30)
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
            if self.path == '/manifest':
                body = mpd().replace('</AdaptationSet>', '<Representation><BaseURL>high.mp4</BaseURL>'
                                    '</Representation></AdaptationSet>', 1).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_address[1]}', asked
    server.shutdown()
    server.server_close()
    thread.join()


async def noop(_):
    pass


@pytest.mark.asyncio
async def test_determined_resolution_capped_stream_and_real_download_with_audio(serve, tmp_path, monkeypatch):
    base, asked = serve
    headers = {'Referer': 'https://player.test/'}
    async def resolve(*args):
        return AppStream(base + '/manifest', headers)
    client = SimpleNamespace(profile=SimpleNamespace(name='banri', label='Banri', settings={}), resolve=resolve)
    player = AppPlayer(client)
    async with httpx.AsyncClient() as http:
        answer = await player._stream_answer(http, base + '/manifest', headers, True)
        assert answer.reading.kind is Kind.PLAYABLE
        assert answer.reading.resolutions == (360, 720)
        stream = await player.stream('app://banri/1#line=CO4K&item=token', http, 480, None)
    assert stream == {'stream_url': base + '/video.mp4?a=1&b=2', 'audio_url': base + '/audio.mp4?a=3&b=4',
                      'headers': headers, 'progressive': False, 'playlist': False}
    # The source module must not drop the separate audio address on its way to downloads.
    async def resolved(*args):
        return stream
    monkeypatch.setattr('modules.sources.module.resolve_stream', resolved)
    reply = await SourcesModule.on_video_stream(SimpleNamespace(players=None, _http=None),
                                                SimpleNamespace(payload={'url': 'app://banri/1'}))
    assert reply['audio_url'] == stream['audio_url']
    target = tmp_path / 'output.mp4'
    ok, error = await ytdlp.download(stream['stream_url'], str(target), noop,
                                    headers=headers, audio_url=stream['audio_url'])
    assert (ok, error) == (True, '')
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(target)],
                            capture_output=True, text=True, timeout=20)
    streams = json.loads(result.stdout)['streams']
    assert {s['codec_type'] for s in streams} == {'video', 'audio'}
    assert next(s['height'] for s in streams if s['codec_type'] == 'video') == 360
    assert min(float(s['duration']) for s in streams) >= 2.9
    assert all(referer == headers['Referer'] for _, referer in asked)
    assert any(path == '/audio.mp4?a=3&b=4' for path, _ in asked)


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
    # Model an audio origin that announces six seconds, but sends only the first three.
    async def source_length(url, headers):
        return 6.0 if 'audio' in url else 3.0
    monkeypatch.setattr(ytdlp, '_probe_duration_ffprobe', source_length)
    target = tmp_path / 'cut-audio.mp4'
    ok, error = await ytdlp.download(base + '/video.mp4', str(target), noop,
                                    headers={'Referer': 'https://player.test/'}, audio_url=base + '/audio.mp4')
    assert not ok and error and not target.exists()
    assert not (tmp_path / 'cut-audio.mp4.download.mp4').exists()


@pytest.mark.asyncio
async def test_a_cdn_refusal_is_preserved_without_claiming_a_playable_dash():
    asked = []
    def respond(request):
        asked.append(request.url.path)
        return httpx.Response(200, text=mpd()) if request.url.path == '/manifest' else httpx.Response(403)
    player = AppPlayer(SimpleNamespace(profile=SimpleNamespace(name='banri', label='Banri', settings={})))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        answer = await player._stream_answer(http, 'https://cdn.test/manifest', {}, True)
    assert player.read(answer).kind is Kind.HTTP_ERROR and answer.status == 403
    assert asked == ['/manifest', '/video.mp4']


@pytest.mark.asyncio
async def test_a_server_ignoring_range_cannot_supply_an_unbounded_manifest(monkeypatch):
    from modules.sources.players import app_lines
    monkeypatch.setattr(app_lines, 'PLAYLIST_BYTES', 8192)
    text = mpd(extra='<!--' + 'x' * 12000 + '-->')
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=text))) as http:
        served = await served_as(http, 'https://cdn.test/manifest', {})
    assert not served.kind and served.answer.reading.kind is Kind.NO_FILE
    assert 'size limit' in served.answer.reading.message


@pytest.mark.asyncio
@pytest.mark.parametrize('audio_status', [200, 403])
async def test_tv_tracks_are_asked_with_the_applications_own_player_agent_and_need_their_audio(media, audio_status):
    # The real CDN returns 403 for a browser agent or a Referer on android_tv_yst tracks; Banri's player is okhttp.
    base = 'https://upos-sz-mirrorhw.bilivideo.com/'
    video = base + 'video.mp4?platform=android_tv_yst'
    audio = base + 'audio.mp4?platform=android_tv_yst'
    asked = []
    def respond(request):
        if request.url.host == 'resolver.test':
            return httpx.Response(200, text=mpd(video, audio))
        asked.append((request.url.path, request.headers.get('User-Agent'), request.headers.get('Referer')))
        if request.headers.get('User-Agent') != 'okhttp/3.14.9' or request.headers.get('Referer'):
            return httpx.Response(403)
        if request.url.path == '/audio.mp4' and audio_status == 403:
            return httpx.Response(403)
        return httpx.Response(200, content=(media / request.url.path.lstrip('/')).read_bytes())
    async def resolve(*args):
        return AppStream('https://resolver.test/manifest', {})
    player = AppPlayer(SimpleNamespace(profile=SimpleNamespace(name='banri', label='Banri',
                                                               settings={'player_agent': 'okhttp/3.14.9'}), resolve=resolve))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        answer = await player.fetch(PlayerRef('line:CO4K', 'app://banri/1#line=CO4K&item=t'), http, '')
        reading = player.read(answer)
        if audio_status == 403:
            assert reading.kind is Kind.HTTP_ERROR and reading.status == 403
            assert reading.message.startswith('DASH audio:')
        else:
            assert reading.kind is Kind.PLAYABLE and reading.resolutions == (360,)
            stream = await player.stream('app://banri/1#line=CO4K&item=t', http, None, None)
            assert stream['headers'] == {'User-Agent': 'okhttp/3.14.9'}
            assert stream['stream_url'] == video and stream['audio_url'] == audio
    assert asked and all(agent == 'okhttp/3.14.9' and referer is None for _, agent, referer in asked)


@pytest.mark.asyncio
async def test_a_refused_audio_has_the_application_parse_the_episode_anew(media):
    """A stale audio signature is answered by the application itself: the next ask gets a fresh manifest from it."""
    manifests = [mpd('https://cdn.test/video.mp4', 'https://cdn.test/stale.mp4'),
                 mpd('https://cdn.test/video.mp4', 'https://cdn.test/audio.mp4')]
    resolved = []
    def respond(request):
        if request.url.path == '/stale.mp4':
            return httpx.Response(403)
        return httpx.Response(200, content=(media / request.url.path.lstrip('/')).read_bytes())
    async def resolve(*args):
        resolved.append(args)
        return AppStream(f'https://resolver.test/{len(resolved)}', {})
    def answer_of(request):
        return httpx.Response(200, text=manifests[int(request.url.path.strip('/')) - 1])
    def route(request):
        return answer_of(request) if request.url.host == 'resolver.test' else respond(request)
    player = AppPlayer(SimpleNamespace(profile=SimpleNamespace(name='banri', label='Banri', settings={}), resolve=resolve))
    ref = PlayerRef('line:CO4K', 'app://banri/1#line=CO4K&item=t')
    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as http:
        first = player.read(await player.fetch(ref, http, ''))
        second = player.read(await player.fetch(ref, http, ''))
    assert first.kind is Kind.HTTP_ERROR and second.kind is Kind.PLAYABLE and len(resolved) == 2
