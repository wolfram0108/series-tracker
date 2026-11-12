"""Build shareable CSV lists from a private app_audit run, without stream URLs, tokens or credentials."""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from app_audit import APPS, daily_quota, spaced


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def cause(row):
    kind, text = row.get('kind', ''), row.get('reason', '')
    try:
        data = json.loads(text)
    except ValueError:
        data = {}
    if kind == 'quota_blocked' or daily_quota(data.get('message', '')):
        return 'daily_quota'
    if kind == 'host_blocked':
        return 'host_cooldown'
    if kind == 'playable':
        return 'playable'
    if data.get('code'):
        return data['code'] + (':' + str(data['status']) if data.get('status') else '')
    if row.get('status') and row['status'] != 200:
        return 'HTTP ' + str(row['status'])
    if 'MPD' in text:
        return 'DASH_manifest'
    if 'Timeout' in text or 'timed out' in text or 'gave no answer' in text:
        return 'timeout'
    if 'Connect' in text:
        return 'connection_failed'
    return kind or 'unknown'


def counts(rows, planned):
    tested = [r for r in rows if r.get('attempted', True)]
    good = sum(r['kind'] == 'playable' for r in tested)
    return dict(planned=planned, recorded=len(rows), tested=len(tested), playable=good,
                failed=len(tested) - good, quota_skipped=sum(r['kind'] == 'quota_blocked' for r in rows),
                host_skipped=sum(r['kind'] == 'host_blocked' for r in rows), pending=planned - len(rows),
                playable_percent=round(100 * good / len(tested), 1) if tested else '',
                heights=','.join(str(h) for h in sorted({h for r in rows for h in r.get('heights', [])})),
                causes=json.dumps(Counter(cause(r) for r in rows), ensure_ascii=False))


def csv_file(path, rows):
    if not rows:
        return
    temporary = path.with_name(path.name + f'.{os.getpid()}.new')
    with temporary.open('w', encoding='utf-8-sig', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def text_file(path, text):
    temporary = path.with_name(path.name + f'.{os.getpid()}.new')
    temporary.write_text(text)
    temporary.replace(path)


def report(root, inventory_path, count=25):
    inventory = [r for r in read(inventory_path) if r['chinese']]
    observations = []
    for app in APPS:
        path = root / f'episodes-{app}.jsonl'
        if path.exists():
            observations += [json.loads(line) for line in path.read_text().splitlines()]
    attempts, first = Counter(), {}
    for row in observations:
        key = (row['app'], row['card'], row['line'], row['place'])
        attempts[key] += 1
        first.setdefault(key, row)
    # A resumed or interrupted run must never inflate a denominator through duplicate records.
    observations = list({(r['app'], r['card'], r['line'], r['place']): r for r in observations}.values())
    lines, matrix, episodes = [], [], []
    for series in inventory:
        for app in APPS:
            discovery = read(root / 'discovery' / f"{app}-{series['id']}.json", {})
            selected = discovery.get('selected', [])
            title_rows = []
            title_lines = []
            catalog_errors = []
            for card in selected:
                catalog = read(root / 'catalog' / f'{app}-{card}.json', {})
                if catalog.get('state') != 'ok':
                    catalog_errors.append(card)
                    continue
                for line in catalog['card_data']['lines']:
                    sample = spaced(line['items'], count)
                    places = {r['place'] for r in sample}
                    rows = [r for r in observations if r['app'] == app and r['card'] == card
                            and r['line'] == line['code'] and r['place'] in places]
                    title_rows += rows
                    item = dict(series_id=series['id'], title=series['name'], chinese=series['query'], app=app,
                                card=f'app://{app}/{card}', card_title=' / '.join(catalog['card_data']['names']),
                                line=line['code'], line_name=line['name'], episodes=len(line['items']),
                                admitted=rows[0]['admitted'] if rows else '',
                                sampled_positions=','.join(str(r['place']) for r in sample), **counts(rows, len(sample)))
                    lines.append(item)
                    title_lines.append(item)
            totals = counts(title_rows, sum(r['planned'] for r in title_lines))
            state = ('catalog_failed' if catalog_errors else 'source_unreachable' if discovery.get('state') in
                     ('search_failed', 'source_unreachable') else 'pending' if totals['pending'] else
                     'quota_blocked' if not totals['tested'] and totals['quota_skipped'] else
                     'host_unreachable' if not totals['tested'] and totals['host_skipped'] else
                     'sampled' if title_lines else discovery.get('state', 'not_started'))
            matrix.append(dict(series_id=series['id'], title=series['name'], chinese=series['query'], season=series['season'],
                               app=app, state=state, cards=' | '.join(f'app://{app}/{c}' for c in selected),
                               production_links=' | '.join(s['url'] for s in series['sources'] if s['url'].startswith(f'app://{app}/')),
                               lines=len(title_lines), good_lines=' | '.join(dict.fromkeys(r['line'] for r in title_lines if r['playable'])),
                               good_admitted_lines=' | '.join(dict.fromkeys(r['line'] for r in title_lines if r['playable'] and r['admitted'])),
                               table_cards=' | '.join(dict.fromkeys(r['card'] for r in title_lines if r['playable'] and r['admitted'])),
                               admitted_tested=sum(r['tested'] for r in title_lines if r['admitted']),
                               admitted_playable=sum(r['playable'] for r in title_lines if r['admitted']),
                               discovery_reason=discovery.get('reason', ''), **totals))
    for row in observations:
        key = (row['app'], row['card'], row['line'], row['place'])
        episodes.append({k: row.get(k, '') for k in ('app', 'series_id', 'card', 'line', 'place', 'label',
                                                     'line_total', 'admitted', 'at', 'kind', 'status', 'attempted', 'seconds')}
                        | dict(heights=','.join(map(str, row.get('heights', []))), cause=cause(row),
                               observations=attempts[key], first_kind=first[key]['kind'], first_cause=cause(first[key]),
                               first_at=first[key]['at']))
        episodes[-1]['attempted'] = row.get('attempted', True)
    destination = root / 'results'
    destination.mkdir(exist_ok=True)
    csv_file(destination / 'series-sources.csv', matrix)
    csv_file(destination / 'series-lines.csv', lines)
    csv_file(destination / 'episode-samples.csv', episodes)
    patterns = []
    for app, code in sorted({(r['app'], r['line']) for r in lines}):
        group = [r for r in lines if r['app'] == app and r['line'] == code]
        observed = [r for r in observations if r['app'] == app and r['line'] == code]
        tested_titles = {r['series_id'] for r in group if r['tested']}
        good_titles = {r['series_id'] for r in group if r['playable']}
        patterns.append(dict(app=app, line=code, series=len({r['series_id'] for r in group}),
                             series_tested=len(tested_titles), series_with_video=len(good_titles),
                             series_tested_without_video=len(tested_titles - good_titles),
                             series_pending=len({r['series_id'] for r in group if r['pending']}),
                             **counts(observed, sum(r['planned'] for r in group))))
    csv_file(destination / 'line-patterns.csv', patterns)
    summary = dict(generated=datetime.now().astimezone().isoformat(), chinese_series=len(inventory),
                   matrix_rows=len(matrix), line_rows=len(lines),
                   rechecked_positions=sum(r['observations'] > 1 for r in episodes),
                   recovered_positions=sum(r['kind'] == 'playable' and r['first_kind'] != 'playable' for r in episodes),
                   sources={})
    for app in APPS:
        app_lines = [r for r in lines if r['app'] == app]
        app_rows = [r for r in matrix if r['app'] == app]
        summary['sources'][app] = dict(series=len(app_rows), with_catalog=sum(bool(r['lines']) for r in app_rows),
                                       series_with_video=sum(bool(r['playable']) for r in app_rows),
                                       series_with_admitted_video=sum(bool(r['admitted_playable']) for r in app_rows),
                                       admitted=counts([r for r in observations if r['app'] == app and r.get('admitted')],
                                                       sum(r['planned'] for r in app_lines if r['admitted'])),
                                       lines=len(app_lines), **counts([r for r in observations if r['app'] == app],
                                                                     sum(r['planned'] for r in app_lines)))
    text_file(destination / 'summary.json', json.dumps(summary, ensure_ascii=False, indent=2))
    text = ['ВЫБОРОЧНАЯ ПРОВЕРКА КИТАЙСКИХ ИСТОЧНИКОВ', summary['generated'], '',
            'На каждой доступной линии выбрано до 25 серий равномерно по всему списку, включая первую и последнюю.',
            'Доля успехов относится только к реально проверенным позициям. Она не означает долю рабочих серий во всём каталоге.',
            'Пропуски из-за подтверждённой квоты или недоступного хоста не считаются проверенными отказами.',
            'Проверяется получение потока и измерение разрешения; полная загрузка каждой серии не выполняется.',
            'При недоступности API наличие и качество конкретных сериалов установить нельзя.', '']
    for app, data in summary['sources'].items():
        text.append(f"{app}: каталогов {data['with_catalog']}/24; линий {data['lines']}; "
                    f"проверено {data['tested']}, с видео {data['playable']}; "
                    f"пропущено по квоте {data['quota_skipped']}, по хосту {data['host_skipped']}; "
                    f"ожидают проверки {data['pending']}.")
    text += ['', 'СПИСОК СЕРИАЛОВ']
    for series in inventory:
        text += ['', f"{series['id']}. {series['name']} — {series['query']} ({series['season']})"]
        for row in (r for r in matrix if r['series_id'] == series['id']):
            state = 'API недоступен, серии не проверены' if row['state'] == 'source_unreachable' else (
                f"видео {row['playable']}/{row['tested']} проверок; квота: пропущено {row['quota_skipped']}; "
                f"хост: пропущено {row['host_skipped']}; осталось {row['pending']}")
            text.append(f"  {row['app']}: {state}")
            if row['cards']:
                text.append('    Карточки: ' + row['cards'])
            if row['good_lines']:
                text.append('    Видео найдено на линиях: ' + row['good_lines'])
                text.append('    Из них допущены к рабочим таблицам: ' + (row['good_admitted_lines'] or 'нет'))
            if row['table_cards']:
                text.append('    Карточки с найденным видео допущенных линий: ' + row['table_cards'])
    text_file(destination / 'series-list.txt', '\n'.join(text) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--count', type=int, default=25, choices=range(20, 31))
    args = parser.parse_args()
    report(args.output, args.inventory, args.count)
