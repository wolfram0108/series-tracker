"""Cold application reads against synthetic APIs and local video, without a service or production database."""
import asyncio
import json
import shutil
import subprocess
from collections import Counter
from dataclasses import replace

import httpx
import pytest

from core.retry import Budget
from modules.sources.apps import profiles_of
from modules.sources.apps.appdrama import AppDramaClient
from modules.sources.apps.lines import LineRegistry
from modules.sources.players import hls
from modules.sources.players.app_lines import AppLines, AppPlayer
from modules.sources.players.contract import Answer, Kind, PlayerRef, Reading
from modules.sources.policy import AdaptivePacer, settle
from modules.sources.sites import ReadContext
from modules.sources.sites.apps import AppSite
from appdrama_model import DramaModel, HOST, NAVIGATOR, Video


@pytest.mark.parametrize('multiple', [False, True])
async def test_prototype_site_profile_discovers_api_instead_of_using_obsolete_fallback(multiple):
    model = DramaModel()
    settings = dict(model.profile().settings, host='', site=['https://bad.model/nav', NAVIGATOR] if multiple else NAVIGATOR,
                    fallback='https://obsolete.model')
    profile = profiles_of({'ST_APP_HUAYI': json.dumps(dict(settings, protocol='appdrama', label='华谊', latin='Huayi'))})[0]
    requested = []

    def handle(request):
        requested.append(request)
        if request.url.host == 'bad.model':
            return httpx.Response(200, json={'domain': 'file:///invalid'})
        assert request.url.host != 'obsolete.model'
        return model.handle(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        await AppDramaClient(profile, lambda: http).search_page('title', 1)
    assert model.zones == 1
    assert any(str(r.url) == NAVIGATOR for r in requested)
    assert all(r.extensions['timeout']['connect'] < r.extensions['timeout']['read'] for r in requested)


async def test_prototype_fixed_host_does_not_get_treated_as_navigator():
    model = DramaModel()
    profile = replace(model.profile(), settings=dict(model.profile().settings, site=NAVIGATOR, host=HOST))

    def handle(request):
        assert request.method == 'POST'
        return model.handle(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        await AppDramaClient(profile, lambda: http).search_page('title', 1)
    assert model.zones == 1


async def test_cold_cards_share_load_limit_and_get_every_frame_on_first_attempt(monkeypatch):
    from modules.sources.policy import Pacer

    async def no_wait(self):
        pass

    monkeypatch.setattr(Pacer, 'wait', no_wait)
    model = DramaModel()
    for card in (1, 2):
        videos = [Video('L', 'Line', str(n), f'{card}-{n}') for n in range(12)]
        model.cards[card] = ('Title', 2026, videos)
        for video in videos:
            model.plays[('L', video.path)] = (f'https://cdn.model/{video.path}.m3u8', {})
    active, peak, refused = 0, 0, 0

    async def handle(request):
        nonlocal active, peak, refused
        if request.url.path.endswith('videoUsableUrl'):
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.01)
                if active > 8:
                    refused += 1
                    return httpx.Response(429, headers={'Retry-After': '0'})
                return model.handle(request)
            finally:
                active -= 1
        if request.url.host == 'cdn.model':
            return httpx.Response(200, text='#EXTM3U\n#EXTINF:6,\nsegment.ts\n#EXT-X-ENDLIST\n')
        return model.handle(request)

    async def measure(*args):
        await asyncio.sleep(0.01)
        return 1080

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        player = AppPlayer(AppDramaClient(model.profile(), lambda: http), measure,
                           registry=LineRegistry(frozenset({'L'})))
        site = AppSite(player, AppLines([player]))
        drafts = await asyncio.gather(*(site.read(f'app://huayi/{card}', ReadContext(http, lambda *_: None))
                                        for card in (1, 2)))
    assert all(row.heights == [1080] for draft in drafts for row in draft.rows)
    assert sum(len(draft.rows) for draft in drafts) == 24
    assert 1 < peak <= 8 and refused == 0
    assert Counter(q['playUrl'] for path, q in model.calls if path.endswith('videoUsableUrl')) == {
        f'{card}-{n}': 1 for card in (1, 2) for n in range(12)}
    assert model.zones == 1


class Clock:
    now = 0.0

    def time(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


async def test_a_slow_app_timeout_still_gets_another_attempt():
    clock = Clock()
    model = DramaModel()
    player = AppPlayer(AppDramaClient(model.profile(), lambda: None))
    site = AppSite(player, AppLines([player]))

    class SlowPlayer:
        name = 'slow'
        calls = 0

        async def fetch(self, *args):
            self.calls += 1
            clock.now += 60
            return Answer(200, reading=Reading(Kind.NO_ANSWER if self.calls == 1 else Kind.PLAYABLE, 200, (1080,)))

        def read(self, answer):
            return answer.reading

    slow = SlowPlayer()
    verdict = await settle(slow, PlayerRef('slow', 'app://huayi/1'), None, '', budget=getattr(site, "budget", None),
                           clock=clock.time, sleep=clock.sleep)
    assert verdict.reading.kind is Kind.PLAYABLE and verdict.attempts == 2


async def test_retry_after_from_app_api_reaches_the_shared_pacer():
    model = DramaModel()

    def handle(request):
        if request.url.path.endswith('videoUsableUrl'):
            return httpx.Response(429, headers={'Retry-After': '7'})
        return model.handle(request)

    clock = Clock()
    pacer = AdaptivePacer(0.5, clock=clock.time, sleep=clock.sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        player = AppPlayer(AppDramaClient(model.profile(), lambda: http))
        ref = PlayerRef('huayi', player.link_of('app://huayi/1', 'L', 'token'))
        verdict = await settle(player, ref, http, '', budget=Budget(attempts=1), pacer=pacer,
                               clock=clock.time, sleep=clock.sleep)
    assert verdict.reading.kind is Kind.HTTP_ERROR and verdict.reading.status == 429
    assert pacer.interval > 0.5
    await pacer.wait()  # a different row must respect the same cooldown
    assert clock.now >= 7


class Chunks(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data, self.read, self.closed = data, 0, False

    async def __aiter__(self):
        for pos in range(0, len(self.data), 4096):
            self.read += len(self.data[pos:pos + 4096])
            yield self.data[pos:pos + 4096]

    async def aclose(self):
        self.closed = True


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='ffmpeg is needed')
async def test_video_headers_beyond_first_prefix_are_measured_without_downloading_whole_segment():
    video = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=320x240:rate=25', '-t', '1',
                            '-c:v', 'mpeg2video', '-f', 'mpegts', 'pipe:1'], capture_output=True, check=True, timeout=30).stdout
    # Valid transport packets carrying no video precede the actual program headers.
    data = (b'\x47\x1f\xff\x10' + b'\xff' * 184) * 2000 + video + bytes(2 * hls.MAX_HEAD_BYTES)
    requests, streams = [], []

    def handle(request):
        requests.append(request)
        stream = Chunks(data)
        streams.append(stream)
        return httpx.Response(200, stream=stream)  # CDN ignores Range

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        height = await hls.measure_head(http, hls.Head('https://cdn.model/segment.ts', 0), '', {'X-Stream': 'test'})
    assert height == 240
    assert [r.headers['range'] for r in requests] == [f'bytes=0-{hls.HEAD_BYTES - 1}', f'bytes=0-{hls.MAX_HEAD_BYTES - 1}']
    assert all(r.headers['x-stream'] == 'test' for r in requests)
    assert all(s.closed and s.read <= hls.MAX_HEAD_BYTES for s in streams)


async def test_probe_timeout_is_not_proof_that_the_episode_is_missing(monkeypatch):
    async def timeout(*args, **kwargs):
        return None, 'ffprobe gave no answer in 30 s'

    from core import ffprobe
    monkeypatch.setattr(ffprobe, 'probe', timeout)
    monkeypatch.setattr(hls, 'probe', timeout)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'\x47' * 1000))) as http:
        with pytest.raises(hls.MeasureError) as caught:
            await hls.measure_head(http, hls.Head('https://cdn.model/segment.ts', 0), '')
    assert caught.value.answer.status is None
    assert 'ffprobe' in caught.value.answer.error


async def test_cooldown_extended_during_sleep_is_observed_by_waiting_rows():
    clock = Clock()
    asleep, resume = asyncio.Event(), asyncio.Event()
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 1:
            asleep.set()
            await resume.wait()
        clock.now += seconds

    pacer = AdaptivePacer(0.5, clock=clock.time, sleep=sleep)
    await pacer.wait()
    waiter = asyncio.create_task(pacer.wait())
    await asleep.wait()
    pacer.slow_down(7)
    resume.set()
    await waiter
    assert clock.now == 7 and len(sleeps) == 2
    for _ in range(10):
        pacer.recovered()
    assert pacer.interval == 0.5


async def test_whole_file_probe_failure_keeps_its_cause_and_remains_retryable(monkeypatch):
    async def failed(*args, **kwargs):
        return [], 'Connection reset by peer'

    monkeypatch.setattr(hls, 'probe', failed)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=b'\0\0\0\x18ftypisom' + bytes(100)))) as http:
        with pytest.raises(hls.MeasureError) as caught:
            await hls.measure_head(http, hls.Head('https://cdn.model/film.mp4', 0), '')
    assert caught.value.answer.reading.kind is Kind.NO_FILE
    assert caught.value.answer.reading.message == 'Connection reset by peer'
