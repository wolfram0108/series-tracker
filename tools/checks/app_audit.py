"""An isolated, resumable survey of application catalogues and evenly spaced episodes.

Inputs are an exported inventory and a private environment file; this tool never opens the service database or starts
its modules. Output holds catalogue snapshots (private tokens), HTTP timing without URLs, and per-episode verdicts.
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import heapq
import itertools
import json
import os
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
from dotenv import dotenv_values

from core.http_limits import BoundedTransport
from modules.sources.apps import AppCardData, AppError, AppItem, AppLine, daily_quota, profiles_of
from modules.sources.apps.lines import LineRegistry
from modules.sources.module import APP_PROTOCOLS
from modules.sources.players.app_lines import AppPlayer
from modules.sources.players.contract import PlayerRef
from modules.sources.policy import AdaptivePacer
from modules.sources.players.http import retry_after

CONTEXT = contextvars.ContextVar('audit_context', default={})


def spaced(items, count=25):
    """Include both ends and distribute the remaining observations across the complete line; a sample of one is the last
    position, the newest episode."""
    if len(items) <= count:
        return list(items)
    if count == 1:
        return [items[-1]]
    if count < 1:
        raise ValueError('a sample needs at least one position')
    return [items[round(i * (len(items) - 1) / (count - 1))] for i in range(count)]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.new')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


def append(path, data):
    with path.open('a') as out:
        out.write(json.dumps(data, ensure_ascii=False) + '\n')


def clean(value):
    # Truncating the serialized reason destroys JSON and can hide a quota from a resumed audit.
    def scrub(item):
        if isinstance(item, dict):
            return {key: scrub(val) for key, val in item.items()}
        if isinstance(item, list):
            return [scrub(val) for val in item]
        if isinstance(item, str):
            return re.sub(r'''https?://[^\s`"'<>]+''', lambda m: urlsplit(m[0]).hostname or '[address]', item)[:500]
        return item
    try:
        decoded = json.loads(str(value))
    except ValueError:
        return scrub(str(value))
    return json.dumps(scrub(decoded), ensure_ascii=False) if isinstance(decoded, (dict, list)) else scrub(str(value))


class ObservedStream(httpx.AsyncByteStream):
    def __init__(self, inner, record, done):
        self.inner, self.record, self.done = inner, record, done
        self.closed = False

    async def __aiter__(self):
        async for chunk in self.inner:
            self.record['bytes'] += len(chunk)
            yield chunk

    async def aclose(self):
        if not self.closed:
            self.closed = True
            try:
                await self.inner.aclose()
            finally:
                self.done(self.record)


class UnreachableHost(httpx.ConnectError):
    """A recent repeated failure to establish any connection to this host, independent of episode URL."""


class ProbeQueue:
    """Keep the two-process cap without putting every short local header behind slow remote ffprobe jobs."""

    def __init__(self, probe, capacity=2, output=None):
        self.probe, self.capacity = probe, capacity
        self.output = output
        self.waiters, self.granted = [], set()
        self.sequence = itertools.count()

    def wake(self):
        while len(self.granted) < self.capacity and self.waiters:
            _, _, ready = heapq.heappop(self.waiters)
            if not ready.cancelled():
                self.granted.add(ready)
                ready.set_result(None)

    async def __call__(self, target, **kwargs):
        queued = time.monotonic()
        ready = asyncio.get_running_loop().create_future()
        heapq.heappush(self.waiters, (0 if kwargs.get('data') is not None else 1, next(self.sequence), ready))
        self.wake()
        try:
            await ready
            started = time.monotonic()
            result = await self.probe(target, **kwargs)
            if self.output is not None:
                data = kwargs.get('data') or b''
                boxes, pos = [], 0
                if data[4:8] == b'ftyp':
                    while pos + 8 <= len(data) and len(boxes) < 8:
                        size = int.from_bytes(data[pos:pos + 4], 'big')
                        boxes.append((pos, size, data[pos + 4:pos + 8].decode('ascii', 'replace')))
                        if size < 8:
                            break
                        pos += size
                append(self.output / 'probes.jsonl', dict(CONTEXT.get(), local=kwargs.get('data') is not None,
                    head_bytes=len(data), boxes=boxes, queue_seconds=round(started - queued, 3),
                    seconds=round(time.monotonic() - started, 3), at=time.time(),
                    heights=[s.get('height') for s in result[0] or [] if s.get('codec_type') == 'video'],
                    reason=clean(result[1])))
            return result
        finally:
            self.granted.discard(ready)
            self.wake()


class SurveyTransport(httpx.AsyncBaseTransport):
    def __init__(self, output, max_rps=4):
        self.inner = BoundedTransport(httpx.AsyncHTTPTransport(limits=httpx.Limits(max_connections=64, max_keepalive_connections=64, keepalive_expiry=120)))
        self.interval = 1 / max_rps
        self.next_start = 0.0
        self.next_host = {}
        self.starts = asyncio.Lock()
        self.hosts = {}
        self.gate = asyncio.Semaphore(16)
        self.output = output
        self.failures, self.down = {}, {}
        history = output / 'http.jsonl'
        if history.exists():
            for raw in history.read_text().splitlines():
                row = json.loads(raw)
                host = row['host']
                if row.get('error') in ('ConnectError', 'ConnectTimeout'):
                    self.failures[host] = self.failures.get(host, 0) + 1
                    if self.failures[host] >= 3:
                        self.down[host] = row['at'] + 300
                elif row.get('status'):
                    self.failures.pop(host, None)
                    self.down.pop(host, None)

    async def start(self, host, pace):
        # Reserve the actual start against BOTH limits. Sequentially waiting on two independent pacers allows
        # one queue to compress the gaps granted by the other when several hosts compete for the global limit.
        while True:
            async with self.starts:
                now = time.monotonic()
                delay = max(self.next_start, self.next_host.get(host, 0), pace._next_at) - now
                if delay <= 0:
                    self.next_start = now + self.interval
                    self.next_host[host] = now + pace.interval
                    return
            await asyncio.sleep(delay)

    async def handle_async_request(self, request):
        host = request.url.host
        if self.down.get(host, 0) > time.time():
            append(self.output / 'http.jsonl', dict(CONTEXT.get(), host=host, method=request.method, at=time.time(),
                                                   error='UnreachableHost', cached=True, bytes=0, seconds=0))
            raise UnreachableHost('connection unavailable during cooldown', request=request)
        pace = self.hosts.setdefault(host, AdaptivePacer(0.6, ceiling_s=3))
        await self.gate.acquire()
        began = time.monotonic()
        record = dict(CONTEXT.get(), host=request.url.host, method=request.method, at=time.time(), bytes=0)
        try:
            await self.start(host, pace)
            record['queue_seconds'] = round(time.monotonic() - began, 3)
            record['at'] = time.time()
            # Diagnostic connection attempts cannot occupy a slot for the legacy 30–60 second connect timeout.
            timeout = request.extensions.get('timeout', {})
            request.extensions['timeout'] = {**timeout, 'connect': min(timeout.get('connect') or 8, 8)}
            response = await self.inner.handle_async_request(request)
            record['status'] = response.status_code
            self.failures.pop(host, None)
            self.down.pop(host, None)
            if response.status_code == 429 or response.status_code >= 500:
                pace.slow_down(retry_after(response) or 0)
            elif response.status_code in (200, 206):
                pace.recovered()
            response.stream = ObservedStream(response.stream, record, self.done)
            return response
        except BaseException as exc:
            record['error'] = type(exc).__name__
            if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
                self.failures[host] = self.failures.get(host, 0) + 1
                if self.failures[host] >= 3:
                    self.down[host] = time.time() + 300
            if isinstance(exc, httpx.TransportError):
                pace.slow_down()
            self.done(record)
            raise

    def done(self, record):
        record['seconds'] = round(time.time() - record['at'], 3)
        append(self.output / 'http.jsonl', record)
        self.gate.release()

    async def aclose(self):
        await self.inner.aclose()


def card_from(data):
    return AppCardData(data['names'], data['year'], [
        AppLine(x['code'], x['name'], x['listed'], x['parsers'], tuple(AppItem(**i) for i in x['items'])) for x in data['lines']])


def norm(name):
    return re.sub(r'[\s：:！!··（）()\-]', '', name).lower()


def chosen(candidates, query, known, match='exact'):
    """The cards a series is read through and how they were chosen: its links in production, else the cards named as the
    series — exactly (`exact`) or with its name inside theirs (`contains`: other seasons and versions come with it)."""
    if known:
        return list(dict.fromkeys(known)), 'production_link'
    wanted = norm(query)
    same = (lambda name: norm(name or '') == wanted) if match == 'exact' else (lambda name: wanted in norm(name or ''))
    found = [str(x['id']) for x in candidates if same(x['name'])]
    return list(dict.fromkeys(found)), ('exact_name' if match == 'exact' else 'name_contains') if found else 'unmatched'


async def discover(app, client, inventory, output, match='exact'):
    failures = 0
    blocked = ''
    for series in inventory:
        destination = output / 'discovery' / f"{app}-{series['id']}.json"
        if destination.exists():
            continue
        context = dict(app=app, series_id=series['id'], phase='search')
        CONTEXT.set(context)
        record = dict(context, query=series['query'], candidates=[], selected=[])
        if blocked:
            record.update(state='source_unreachable', reason=blocked, attempted=False)
            write(destination, record)
            continue
        try:
            async with asyncio.timeout(75):
                page = await client.search_page(series['query'], 1)
            candidates = [{k: c.get(k) for k in ('id', 'name', 'sub', 'year', 'area', 'class', 'remarks')} for c in page.cards]
            record.update(candidates=candidates, pages=page.pages, total=page.total)
            known = [urlsplit(x['url']).path.strip('/') for x in series['sources'] if x['url'].startswith(f'app://{app}/')]
            selected, record['selection'] = chosen(candidates, series['query'], known, match)
            record['selected'] = selected
            record['state'] = 'found' if selected else 'needs_matching' if candidates else 'not_found'
            record['attempted'] = True
            failures = 0
        except Exception as exc:
            record.update(state='search_failed', attempted=True, reason=clean(getattr(exc, 'reason', type(exc).__name__)))
            failures += 1
            if failures >= 3:
                blocked = record['reason']
        write(destination, record)
        print(app, series['id'], record['state'], [(x['id'], x['name']) for x in record['candidates']][:12], flush=True)


async def catalog(app, client, inventory, output):
    for series in inventory:
        found = output / 'discovery' / f"{app}-{series['id']}.json"
        if not found.exists():
            continue
        discovery = json.loads(found.read_text())
        for card_id in discovery['selected']:
            destination = output / 'catalog' / f'{app}-{card_id}.json'
            if destination.exists():
                continue
            CONTEXT.set(dict(app=app, series_id=series['id'], card=card_id, phase='catalog'))
            record = dict(CONTEXT.get())
            try:
                async with asyncio.timeout(75):
                    card = await client.card(card_id)
                record.update(state='ok', card_data=asdict(card))
                print('catalog', app, series['id'], card_id, card.names, [(x.code, len(x.items)) for x in card.lines], flush=True)
            except Exception as exc:
                record.update(state='failed', reason=clean(getattr(exc, 'reason', type(exc).__name__)))
                print('catalog', app, series['id'], card_id, record['reason'], flush=True)
            write(destination, record)


async def sample(app, client, http, inventory, output, count, workers=8, retry_audit_timeouts=False, retry_probe_timeouts=False):
    player = AppPlayer(client)
    registry = LineRegistry.load(app)
    done_path = output / f'episodes-{app}.jsonl'
    completed = set()
    quotas = {}
    if done_path.exists():
        for row in done_path.read_text().splitlines():
            r = json.loads(row)
            audit_timeout = r['kind'] == 'audit_timeout' or r['kind'] == 'exception' and r.get('reason') == 'TimeoutError'
            probe_timeout = r['kind'] == 'no_answer' and 'ffprobe gave no answer' in r.get('reason', '')
            if not (retry_audit_timeouts and audit_timeout or retry_probe_timeouts and probe_timeout):
                completed.add((r['card'], r['line'], r['place']))
            try:
                words = json.loads(r.get('reason', '')).get('message', '')
            except ValueError:
                words = ''
            if daily_quota(words):
                quotas[r['line']] = r['reason']
    jobs = []
    for series in inventory:
        discovery_path = output / 'discovery' / f"{app}-{series['id']}.json"
        if not discovery_path.exists():
            continue
        discovery = json.loads(discovery_path.read_text())
        for card_id in discovery['selected']:
            path = output / 'catalog' / f'{app}-{card_id}.json'
            if not path.exists():
                continue
            record = json.loads(path.read_text())
            if record['state'] != 'ok':
                continue
            card = card_from(record['card_data'])
            for line in card.lines:
                selected = spaced(line.items, count)
                priority = list(dict.fromkeys([0, len(selected) - 1, len(selected) // 2, *range(len(selected))])) if selected else []
                for sample_index, position in enumerate(priority):
                    item = selected[position]
                    if (card_id, line.code, item.place) not in completed:
                        jobs.append((sample_index, series['id'], card_id, line, item))
    jobs.sort(key=lambda job: job[:3])  # spread a run across titles; time of day is not confounded with one title
    print('sample_jobs', app, len(jobs), flush=True)
    gate = asyncio.Semaphore(workers)

    async def one(index, job):
        _, series_id, card_id, line, item = job
        async with gate:
            CONTEXT.set(dict(app=app, series_id=series_id, card=card_id, line=line.code, place=item.place, phase='episode'))
            record = dict(CONTEXT.get(), label=item.label, line_total=len(line.items), admitted=registry.admits(line.code), at=time.time())
            began = time.monotonic()
            if line.code in quotas:
                record.update(kind='quota_blocked', reason=quotas[line.code], heights=[], attempted=False, seconds=0)
                append(done_path, record)
                return
            try:
                # HTTP and each ffprobe process have their own shorter timeouts. The outer guard also includes
                # the audit's deliberately small shared process queue, so it must not masquerade as a CDN timeout.
                async with asyncio.timeout(600):
                    answer = await player.fetch(PlayerRef(app, player.link_of(f'app://{app}/{card_id}', line.code, item.token)), http, '')
                reading = player.read(answer)
                record.update(kind=reading.kind.value, status=reading.status or answer.status, heights=reading.resolutions,
                              reason=clean(reading.message or answer.error))
            except TimeoutError:
                record.update(kind='audit_timeout', reason='audit wall-clock guard including shared queues', heights=[])
            except Exception as exc:
                record.update(kind='exception', reason=clean(getattr(exc, 'reason', type(exc).__name__)), heights=[])
            record['seconds'] = round(time.monotonic() - began, 3)
            record['attempted'] = True
            if 'UnreachableHost' in record['reason']:
                record.update(kind='host_blocked', attempted=False)
            try:
                words = json.loads(record.get('reason', '')).get('message', '')
            except ValueError:
                words = ''
            if daily_quota(words):
                quotas[line.code] = record['reason']
            append(done_path, record)
            print('sample', app, index + 1, len(jobs), series_id, card_id, line.code, item.label, record['kind'], record['heights'], flush=True)
    await asyncio.gather(*(one(index, job) for index, job in enumerate(jobs)))


async def main(args):
    from core import ffprobe
    from modules.sources.players import app_lines, hls
    ffprobe.PROBES_AT_ONCE = 2
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    app_lines.probe = hls.probe = ProbeQueue(ffprobe.probe, output=output)
    inventory = [s for s in json.loads(Path(args.inventory).read_text()) if s['chinese']]
    profiles = profiles_of({k: v for k, v in dotenv_values(args.env).items() if k.startswith('ST_APP_') and v})
    fingerprint = hashlib.sha256()
    for path in sorted((ROOT / 'modules/sources').rglob('*.py')):
        fingerprint.update(path.relative_to(ROOT).as_posix().encode())
        fingerprint.update(path.read_bytes())
    append(output / 'runs.jsonl', dict(at=time.time(), phase=args.phase, apps=args.apps, count=args.count, match=args.match,
                                     max_http_rps=args.max_rps, per_host_interval_s=0.6, http_in_flight=16,
                                     workers_per_source=args.workers, ffprobe_in_flight=2,
                                     retry_audit_timeouts=args.retry_audit_timeouts, retry_probe_timeouts=args.retry_probe_timeouts,
                                     source_code_sha256=fingerprint.hexdigest()))
    async with httpx.AsyncClient(transport=SurveyTransport(output, args.max_rps), follow_redirects=True) as http:
        clients = {p.name: APP_PROTOCOLS[p.protocol](p, lambda: http) for p in profiles if not args.apps or p.name in args.apps}
        if args.phase in ('discover', 'all'):
            await asyncio.gather(*(discover(app, client, inventory, output, args.match) for app, client in clients.items()))
        if args.phase in ('catalog', 'all'):
            await asyncio.gather(*(catalog(app, client, inventory, output) for app, client in clients.items()))
        if args.phase in ('sample', 'all'):
            await asyncio.gather(*(sample(app, client, http, inventory, output, args.count, args.workers,
                                         args.retry_audit_timeouts, args.retry_probe_timeouts) for app, client in clients.items()))


if __name__ == '__main__':
    os.umask(0o077)
    try:
        Path('/proc/self/oom_score_adj').write_text('1000')
    except OSError:
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', required=True)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--apps', nargs='+', help='applications to read; by default every one `ST_APP_*` describes')
    parser.add_argument('--match', choices=('exact', 'contains'), default='exact',
                        help='cards of a series: named exactly as it, or with its name inside theirs')
    parser.add_argument('--phase', choices=('discover', 'catalog', 'sample', 'all'), required=True)
    parser.add_argument('--count', type=int, choices=range(1, 31), default=25)
    parser.add_argument('--max-rps', type=int, choices=range(1, 9), default=4,
                        help='maximum HTTP starts per second across all sources (ffprobe has its own two-process cap)')
    parser.add_argument('--workers', type=int, choices=(4, 8, 16), default=8,
                        help='episode tasks per source; the shared transport still limits HTTP concurrency to 16')
    parser.add_argument('--retry-audit-timeouts', action='store_true',
                        help='retry only previously unfinished measurements stopped by the audit wall-clock guard')
    parser.add_argument('--retry-probe-timeouts', action='store_true',
                        help='recheck prior ffprobe timeouts, e.g. after improving bounded MP4 header reads')
    asyncio.run(main(parser.parse_args()))
    print('APP_AUDIT_DONE')
