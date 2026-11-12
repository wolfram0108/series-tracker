"""The audit's sample and the shared refusal policy are verified without production data or external requests."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from core.reasons import reason
from modules.sources.apps import AppError, AppProfile, AppStream, daily_quota
from modules.sources.players.app_lines import AppPlayer
from modules.sources.players.contract import PlayerRef, Kind
from modules.sources.policy import settle
from modules.sources.apps.app99 import App99Client
from modules.sources.apps.appmn import AppMnClient
from modules.sources.apps.yunduo import YunDuoClient
from test_sources_appmn import SETTINGS as MN
from test_sources_yunduo import SETTINGS as YD
from app99_model import profile as banri_profile

spec = importlib.util.spec_from_file_location('audit_tool', Path(__file__).resolve().parents[1] / 'tools/checks/app_audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.mark.parametrize('size', [0, 1, 2, 24, 25, 26, 700, 1000])
def test_sparse_sample_covers_whole_line_without_duplicates(size):
    selected = audit.spaced(list(range(size)), 25)
    assert len(selected) == min(25, size)
    assert len(selected) == len(set(selected))
    if selected:
        assert selected[0] == 0 and selected[-1] == size - 1
        gaps = [b - a for a, b in zip(selected, selected[1:])]
        if gaps:
            assert max(gaps) - min(gaps) <= 1


@pytest.mark.parametrize('size', [9, 10, 11, 193, 700])
def test_a_sample_of_ten_has_both_ends_and_no_repeat(size):
    selected = audit.spaced(list(range(size)), 10)
    assert len(selected) == min(10, size) and len(selected) == len(set(selected))
    assert selected[0] == 0 and selected[-1] == size - 1


@pytest.mark.parametrize('size', [0, 1, 2, 47])
def test_a_sample_of_one_is_the_newest_episode(size):
    assert audit.spaced(list(range(size)), 1) == ([size - 1] if size else [])
    with pytest.raises(ValueError):
        audit.spaced(list(range(5)), 0)


CARDS = [{'id': 1, 'name': '万界独尊'}, {'id': 2, 'name': '万界独尊 第二季'}, {'id': 3, 'name': '万界独尊（动态漫）'},
         {'id': 4, 'name': '丹道至尊'}, {'id': 5, 'name': None}]


def test_cards_of_a_series_are_its_links_else_the_cards_named_exactly_or_containing_its_name():
    assert audit.chosen(CARDS, '万界独尊', ['9'], 'contains') == (['9'], 'production_link')
    assert audit.chosen(CARDS, '万界独尊', [], 'exact') == (['1'], 'exact_name')
    assert audit.chosen(CARDS, '万界独尊', [], 'contains') == (['1', '2', '3'], 'name_contains')
    assert audit.chosen(CARDS, '没有的名字', [], 'contains') == ([], 'unmatched')
    assert audit.chosen([], '万界独尊', [], 'exact') == ([], 'unmatched')


def test_the_audit_reads_every_protocol_the_service_reads():
    from modules.sources.module import APP_PROTOCOLS
    assert audit.APP_PROTOCOLS is APP_PROTOCOLS and {'appv7', 'gulu', 'appget', 'app99'} <= set(APP_PROTOCOLS)


@pytest.mark.parametrize('words,expected', [
    ('包月套餐，有效期 30 天，每日限额 25000，当前错误：当天限额已超限', True),
    ('访问受限：已达到今日最大请求点数限制 (30000点)。', True),
    ('时效套餐今日已超过每日最高解析次数上限！每日上限:10000次', True),
    ('每日限额 25000，剩余 10', False), ('解析失败', False), ('视频不存在', False),
    ('daily quota exceeded', True), ('HTTP 429', False),
    ('daily quota not exceeded', False), ('今日限额未超限', False),
    ('调用超过今日限制,请明日再试！或升级套餐', True), ('今日调用未超过限制', False),
])
def test_only_explicit_daily_exhaustion_closes_the_line(words, expected):
    assert daily_quota(words) is expected


async def test_quota_is_shared_by_line_not_retried_per_episode_and_expires():
    calls, now = [], [0.0]
    profile = AppProfile('appmn', 'AppMN', 'appmn', {})
    blocked = True

    async def resolve(line, item, card=''):
        calls.append((line, item))
        if line == 'limited' and blocked:
            raise AppError(reason('app_no_stream', app='AppMN', parser=line, message='今日解析次数已达上限'))
        return AppStream('https://cdn.model/video.m3u8')

    client = SimpleNamespace(profile=profile, resolve=resolve)
    player = AppPlayer(client, clock=lambda: now[0])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as http:
        for number in range(100):
            ref = PlayerRef('appmn', player.link_of('app://appmn/1', 'limited', str(number)))
            result = await settle(player, ref, http, '')
            assert result.reading.kind is Kind.RESTRICTED and result.attempts == 1 and not result.exhausted
        assert calls == [('limited', '0')]
        await player.address('healthy', '1')
        assert len(calls) == 2
        blocked = False
        now[0] = 61
        assert (await player.address('limited', '101')).address.endswith('.m3u8')
        assert len(calls) == 3


@pytest.mark.parametrize('protocol', ['banri', 'appmn', 'yunduo'])
async def test_api_status_and_retry_after_survive_even_a_json_error_body(protocol):
    def reply(request):
        assert request.extensions['timeout']['connect'] <= 8
        return httpx.Response(429, headers={'Retry-After': '7'}, json={'code': -1, 'msg': 'busy'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        if protocol == 'banri':
            client = App99Client(banri_profile(), lambda: http)
        elif protocol == 'appmn':
            client = AppMnClient(AppProfile('appmn', 'AppMN', 'appmn', MN), lambda: http)
        else:
            client = YunDuoClient(AppProfile('yunduo', 'YunDuo', 'yunduo', YD), lambda: http)
        with pytest.raises(AppError) as caught:
            await client.search_page('title', 1)
        assert caught.value.status == 429 and caught.value.retry_after == 7 and caught.value.transient


async def test_a_file_with_track_headers_in_its_head_does_not_open_a_second_network_connection(monkeypatch):
    from modules.sources.players import app_lines
    calls = []

    async def probe(address, **kwargs):
        calls.append(address)
        assert address == 'pipe:0' and kwargs['data'].startswith(b'\0\0\0\x18ftyp')
        return [{'codec_type': 'video', 'height': 2160}], ''

    monkeypatch.setattr(app_lines, 'probe', probe)
    answer = await app_lines.measure_file_frame('https://cdn.model/file.mp4', {}, head=b'\0\0\0\x18ftypisom' + bytes(100))
    assert answer.reading.resolutions == (2160,) and calls == ['pipe:0']


async def test_fast_start_mp4_frame_is_read_from_actual_cached_bytes(tmp_path):
    import shutil
    import subprocess
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg is needed')
    path = tmp_path / 'video.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=320x240:rate=25', '-t', '1',
                    '-c:v', 'mpeg4', '-movflags', '+faststart', str(path)], check=True, timeout=30)
    requests = []

    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=path.read_bytes(), headers={'Content-Type': 'image/jpeg'})

    async def resolve(*args):
        return AppStream('https://cdn.model/video')

    player = AppPlayer(SimpleNamespace(profile=AppProfile('appmn', 'AppMN', 'appmn', {}), resolve=resolve))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        answer = await player.fetch(PlayerRef('appmn', player.link_of('app://appmn/1', 'line', 'token')), http, '')
    assert answer.reading.resolutions == (240,) and requests == ['https://cdn.model/video']


async def test_app99_preserves_a_quota_after_a_long_signed_address_and_tries_allowed_alternatives():
    client = App99Client(banri_profile(), lambda: None)
    client._system = {'player': {'limited': {'type': 1, 'parseUrl': '5,9'}},
                      'parser_api': [{'id': 5}, {'id': 9}]}
    calls = []
    working = True

    async def call(path, fields, **kwargs):
        calls.append(fields['id'])
        if fields['id'] == 9 and working:
            return {'code': 0, 'data': 'https://cdn.model/movie.mp4'}
        return {'code': 0, 'data': None, 'msg': 'GET https://resolver.model/api?key=' + 'secret' * 100
                + ' resulted in 403 Forbidden: 今日解析次数已达上限'}

    client.call = call
    assert (await client.resolve('limited', 'episode')).address.endswith('movie.mp4')
    assert calls == [5, 9]
    working = False
    player = AppPlayer(client)
    for item in ('one', 'two'):
        with pytest.raises(AppError) as error:
            await player.address('limited', item)
        assert error.value.daily_quota and 'secret' not in error.value.reason
        assert '今日解析次数已达上限' in error.value.reason
    assert calls == [5, 9, 5, 9]


async def test_audit_global_and_host_gaps_hold_at_actual_starts_under_competing_queues(tmp_path):
    import json
    from modules.sources.policy import AdaptivePacer
    transport = audit.SurveyTransport(tmp_path, max_rps=200)
    await transport.inner.aclose()
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'ok'

    transport.inner = httpx.MockTransport(lambda request: httpx.Response(200, stream=Body()))
    transport.hosts = {name: AdaptivePacer(0.02) for name in ('a.model', 'b.model')}
    async with httpx.AsyncClient(transport=transport) as http:
        await asyncio.gather(*(http.get('https://' + ('a.model' if i < 10 else 'b.model')) for i in range(20)))
    records = sorted([json.loads(r) for r in (tmp_path / 'http.jsonl').read_text().splitlines()], key=lambda r: r['at'])
    assert all(b['at'] - a['at'] >= 0.004 for a, b in zip(records, records[1:]))
    for host in ('a.model', 'b.model'):
        times = [r['at'] for r in records if r['host'] == host]
        assert all(b - a >= 0.019 for a, b in zip(times, times[1:]))


async def test_a_table_does_not_queue_hundreds_of_known_quota_refusals_at_the_network_pacer():
    from core.contracts import RowState
    from modules.sources.apps import AppCardData, AppItem, AppLine
    from modules.sources.apps.lines import LineRegistry
    from modules.sources.players.app_lines import AppLines
    from modules.sources.sites import ReadContext
    from modules.sources.sites.apps.site import AppSite
    calls, waits = [], []

    async def card(card_id):
        return AppCardData(['Title'], '2026', [AppLine('limited', 'Limited', False, '',
                           tuple(AppItem(n, str(n), str(n)) for n in range(200)))])

    async def resolve(line, item, card=''):
        calls.append(item)
        await asyncio.sleep(0)
        raise AppError(reason('app_no_stream', message='今日解析次数已达上限'))

    async def wait():
        waits.append(True)
        await asyncio.sleep(0)

    client = SimpleNamespace(profile=AppProfile('appmn', 'AppMN', 'appmn', {}), card=card, resolve=resolve)
    player = AppPlayer(client, registry=LineRegistry(frozenset({'limited'})))
    site = AppSite(player, AppLines([player]))
    site.pacer.wait = wait
    draft = await site.read('app://appmn/1', ReadContext(None, lambda *_: None))
    assert len(draft.rows) == 200 and all(row.state is RowState.ABSENT for row in draft.rows)
    assert 1 <= len(calls) <= 8 and 1 <= len(waits) <= 8
    assert all('今日解析次数已达上限' in row.reason for row in draft.rows)


async def test_local_audit_probes_pass_waiting_remote_probes_without_exceeding_process_cap():
    release, started = asyncio.Event(), asyncio.Event()
    order = []
    active = peak = 0

    async def probe(target, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        order.append(target)
        try:
            if target in ('remote0', 'remote1'):
                if len(order) == 2:
                    started.set()
                await release.wait()
            await asyncio.sleep(0)
        finally:
            active -= 1

    queue = audit.ProbeQueue(probe)
    tasks = [asyncio.create_task(queue(f'remote{i}')) for i in range(2)]
    await started.wait()
    cancelled = asyncio.create_task(queue('cancelled'))
    tasks += [asyncio.create_task(queue('remote2')), asyncio.create_task(queue('local0', data=b'head')),
              asyncio.create_task(queue('local1', data=b'head'))]
    await asyncio.sleep(0)
    cancelled.cancel()
    await asyncio.gather(cancelled, return_exceptions=True)
    release.set()
    await asyncio.gather(*tasks)
    assert order == ['remote0', 'remote1', 'local0', 'local1', 'remote2']
    assert peak == 2 and not queue.granted and not queue.waiters


@pytest.mark.parametrize('playlist', [False, True])
async def test_large_fast_start_index_is_measured_without_a_remote_probe(tmp_path, monkeypatch, playlist):
    import shutil
    import subprocess
    from modules.sources.players import app_lines, hls
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg is needed')
    metadata = tmp_path / 'metadata.txt'
    metadata.write_text(';FFMETADATA1\ncomment=' + 'x' * (512 * 1024) + '\n')
    path = tmp_path / 'large-index.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=320x240:rate=25',
                    '-i', str(metadata), '-map_metadata', '1', '-t', '1', '-c:v', 'mpeg4', '-movflags',
                    '+faststart', str(path)], check=True, timeout=30)
    data = path.read_bytes()
    assert 512 * 1024 < app_lines.file_head_size(data[:65536], 65536) < len(data)
    real_probe = app_lines.probe
    asked, measured = [], []

    async def local_probe(address, **kwargs):
        measured.append(address)
        assert address == 'pipe:0', 'an already open response must supply its complete leading index'
        return await real_probe(address, **kwargs)

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for start in range(0, len(data), 16384):
                yield data[start:start + 16384]
            # The HLS probe can read a small prefix past moov, but must still stop before a movie-sized tail.
            for _ in range(4):
                yield bytes(16384)
            pytest.fail('the rest of a large movie must not be downloaded')

    def response(request):
        asked.append(str(request.url))
        if playlist and request.url.path == '/large-index':
            return httpx.Response(200, text='#EXTM3U\n#EXTINF:60,\nfile.mp4\n#EXT-X-ENDLIST\n')
        return httpx.Response(200, stream=Body(), headers={'Content-Type': 'image/jpeg'})

    async def resolve(*args):
        return AppStream('https://cdn.model/large-index')

    monkeypatch.setattr(app_lines, 'probe', local_probe)
    monkeypatch.setattr(hls, 'probe', local_probe)
    player = AppPlayer(SimpleNamespace(profile=AppProfile('appmn', 'AppMN', 'appmn', {}), resolve=resolve))
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        answer = await player.fetch(PlayerRef('appmn', player.link_of('app://appmn/1', 'line', 'token')), http, '')
    assert answer.reading.resolutions == (240,)
    assert len(asked) == (3 if playlist else 1) and measured == ['pipe:0']


def test_file_header_peek_never_follows_a_large_media_box_or_unbounded_index():
    from modules.sources.players.hls import file_head_size, FILE_HEADER_BYTES
    ftyp = (24).to_bytes(4, 'big') + b'ftyp' + bytes(16)
    for box in (b'mdat', b'moov'):
        head = ftyp + (FILE_HEADER_BYTES + 1).to_bytes(4, 'big') + box + bytes(64)
        assert file_head_size(head, 65536) == 65536
    head = ftyp + (1).to_bytes(4, 'big') + b'moov' + (256000).to_bytes(8, 'big') + bytes(64)
    assert file_head_size(head, 65536) == 256024


async def test_matroska_metadata_before_the_first_video_packet_is_read_locally(tmp_path, monkeypatch):
    import shutil
    import subprocess
    from modules.sources.players import app_lines
    from modules.sources.players.hls import MAX_HEAD_BYTES
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg is needed')
    metadata = tmp_path / 'metadata.txt'
    metadata.write_text(';FFMETADATA1\ncomment=' + 'x' * (512 * 1024) + '\n')
    path = tmp_path / 'video.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=320x240:rate=25', '-i', str(metadata),
                    '-map_metadata', '1', '-t', '1', '-c:v', 'mpeg4', str(path)], check=True, timeout=30)
    data = path.read_bytes()
    assert app_lines.file_head_size(data[:65536], 65536) == MAX_HEAD_BYTES
    real_probe = app_lines.probe
    calls = []

    async def local_probe(address, **kwargs):
        calls.append(address)
        assert address == 'pipe:0'
        return await real_probe(address, **kwargs)

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for start in range(0, len(data), 16384):
                yield data[start:start + 16384]

    async def resolve(*args):
        return AppStream('https://cdn.model/video')

    monkeypatch.setattr(app_lines, 'probe', local_probe)
    player = AppPlayer(SimpleNamespace(profile=AppProfile('appmn', 'AppMN', 'appmn', {}), resolve=resolve))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Body()))) as http:
        answer = await player.fetch(PlayerRef('appmn', player.link_of('app://appmn/1', 'line', 'token')), http, '')
    assert answer.reading.resolutions == (240,) and calls == ['pipe:0']


async def test_concurrent_expired_catalogue_requests_share_one_replacement_guest_session():
    from app99_model import App99Model, Card
    model = App99Model()
    model.cards[1] = Card(1, 'Title', [])
    expired = 0
    all_arrived = asyncio.Event()

    async def handle(request):
        nonlocal expired
        if request.url.path.endswith('/vod/detail') and expired < 8:
            expired += 1
            if expired == 8:
                all_arrived.set()
            await all_arrived.wait()
            return model._answer({'code': 1, 'msg': 'session expired'})
        return model.handle(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = App99Client(banri_profile(), lambda: http)
        await client.system()
        cards = await asyncio.gather(*(client.detail('1') for _ in range(8)))
    assert len(cards) == 8 and all(c['name'] == 'Title' for c in cards)
    assert model.sessions == 2, 'one replacement session must serve all requests refused under the old one'


def test_the_report_is_made_of_the_records_of_a_survey_for_every_application_it_read(tmp_path):
    """The whole path of the report on the records of a survey made by hand: discovery, catalogue, episodes."""
    import csv
    import json
    import sys
    tools = str(Path(__file__).resolve().parents[1] / 'tools/checks')
    sys.path.insert(0, tools)
    try:
        spec_ = importlib.util.spec_from_file_location('audit_report', Path(tools) / 'app_audit_report.py')
        made = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(made)
    finally:
        sys.path.remove(tools)
    inventory = tmp_path / 'inventory.json'
    inventory.write_text(json.dumps([{'id': 1, 'name': '万界独尊', 'query': '万界独尊', 'season': 1, 'chinese': True,
                                      'sources': []}]))
    out = tmp_path / 'out'
    (out / 'discovery').mkdir(parents=True)
    (out / 'catalog').mkdir()
    items = [{'place': place, 'label': str(place), 'token': f't{place}'} for place in range(1, 4)]
    for app in ('mtq', 'xiaozhu'):
        (out / 'discovery' / f'{app}-1.json').write_text(json.dumps({'state': 'found', 'selected': ['7']}))
        (out / 'catalog' / f'{app}-7.json').write_text(json.dumps({'state': 'ok', 'card_data': {
            'names': ['万界独尊'], 'year': '2021', 'lines': [{'code': 'JD4K', 'name': 'JD4K', 'listed': False,
                                                                  'parsers': '', 'items': items}]}}))
        rows = [dict(app=app, series_id=1, card='7', line='JD4K', place=item['place'], label=item['label'], line_total=3,
                     admitted=True, at=1.0, kind='playable' if item['place'] != 2 else 'not_found', status=200,
                     heights=[2160] if item['place'] != 2 else [], reason='', attempted=True, seconds=1.0) for item in items]
        (out / f'episodes-{app}.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    made.report(out, inventory, 3)
    assert made.apps_of(out) == ['mtq', 'xiaozhu']
    with (out / 'results' / 'series-lines.csv').open(encoding='utf-8-sig') as stored:
        lines = list(csv.DictReader(stored))
    assert [(row['app'], row['line'], row['tested'], row['playable'], row['heights']) for row in lines] == [
        ('mtq', 'JD4K', '3', '2', '2160'), ('xiaozhu', 'JD4K', '3', '2', '2160')]
    assert 'mtq: каталогов 1/1' in (out / 'results' / 'series-list.txt').read_text()


async def test_the_survey_measures_only_the_lines_in_the_list_of_the_application_unless_asked_for_all(tmp_path):
    import json
    from modules.sources.apps import AppProfile
    from modules.sources.apps.lines import LineRegistry
    registry = LineRegistry(frozenset({'JD4K'}))
    assert audit.listed(registry, 'JD4K') and not audit.listed(registry, 'NBY') and audit.listed(registry, 'NBY', True)

    out = tmp_path / 'out'
    (out / 'discovery').mkdir(parents=True)
    (out / 'catalog').mkdir()
    (out / 'discovery' / 'mtq-1.json').write_text(json.dumps({'state': 'found', 'selected': ['7']}))
    items = [{'place': place, 'label': str(place), 'token': f't{place}'} for place in range(1, 4)]
    (out / 'catalog' / 'mtq-7.json').write_text(json.dumps({'state': 'ok', 'card_data': {'names': ['x'], 'year': '', 'lines': [
        {'code': code, 'name': code, 'listed': False, 'parsers': '', 'items': items} for code in ('JD4K', 'NBY', 'BBA')]}}))

    class Client:
        profile = AppProfile('mtq', 'mtq', 'appv7', {})

        async def resolve(self, line, token, card=''):
            raise AppError(reason('app_no_parser', app='mtq', line=line, tried='—'))

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as http:
        for all_lines, expected in ((False, {'JD4K'}), (True, {'JD4K', 'NBY', 'BBA'})):
            (out / 'episodes-mtq.jsonl').unlink(missing_ok=True)
            await audit.sample('mtq', Client(), http, [{'id': 1}], out, 1, all_lines=all_lines)
            measured = {json.loads(row)['line'] for row in (out / 'episodes-mtq.jsonl').read_text().splitlines()}
            assert measured == expected, all_lines
