"""What a sync of a series did, read from its dump (`logs/runs/<series>/<start>-<pid>.jsonl.gz`, module `runlog`), and what
a read of a page outside a sync did, from its own (`logs/runs/reads/<page>/…`): the pages read with what each player of a
page gave (rows by state, frames, every cause a row was left without a video with), the phases of each read with their
times, the asks of the players counted from the log (attempts, outcomes, seconds), the tables
read, the plan of each round by episode (which row is the video, which the voice-overs; a plan made again shows the
episodes it changed and what made it), every step of the graph with its
start, its end and its outcome, every download with its variant, its time, the size of its file, the seconds of its stages
(queue, link, download, remux, check) and the length it got against the length its source stated, every job of the
alignment with what the daemon said of each voice-over, every group applied and how long it waited after its last step,
and the warnings and errors of the log.

The dump is the raw data and is only read. No request to any service is made.

    .venv/bin/python tools/analyzers/run_dump.py <dump> [--logs]     (--logs: every line of INFO of the modules too)
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sys
from datetime import datetime


# A line of the log of an ask of a player (modules/sources/policy.py, `settle`).
ASK = re.compile(r"^плеер (\S+), .*: попытка (\d+) — (\w+), статус (\S+), .* \(([0-9.]+) с\)$", re.S)


def stamp(text: str) -> datetime:
    return datetime.fromisoformat(text)


def clock(text: str) -> str:
    return text[11:19]


def row_of(op: str) -> str:
    """`source:key` of the row an operation names, without the name of its video."""
    found = re.search(r"source_id=(\d+), row_key='([^']*)'", op)
    return f"{found.group(1)}:{found.group(2)}" if found else op


def main(path: str, logs: bool) -> None:
    rows = [json.loads(line) for line in gzip.open(path, "rt", encoding="utf-8")]
    bus = [row for row in rows if row["e"] == "bus"]
    began, ended = stamp(rows[0]["t"]), stamp(rows[-1]["t"])
    print(f"дамп {os.path.basename(path)}: {clock(rows[0]['t'])} — {clock(rows[-1]['t'])} UTC, "
          f"{(ended - began).total_seconds():.0f} с, строк {len(rows)}")

    subject = rows[0].get("url") or (f"сериал {rows[0]['series_id']}" if rows[0].get("series_id") is not None else "")
    if subject:
        print(f"предмет: {subject}")

    print("\n== таблицы источников")
    progress: dict[str, list[dict]] = {}
    for row in bus:
        if row["topic"] == "sources.table.progress":
            progress.setdefault(row["payload"]["url"], []).append(row)
    for row in bus:
        if row["topic"] in ("sources.table.ready", "sources.table.failed"):
            url = row["payload"]["url"]
            asked = progress.get(url)
            read = f"читалась {(stamp(row['t']) - stamp(asked[0]['t'])).total_seconds():.0f} с" if asked else "из базы"
            print(f"  {clock(row['t'])} {row['topic'].rsplit('.', 1)[1]:<6} {url[:90]} — {read}"
                  f"{' — ' + str(row['payload'].get('reason')) if row['payload'].get('reason') else ''}")

    reads = [row for row in bus if row["topic"] == "sources.table.trace" and row["payload"].get("kind") == "end"]
    if reads or progress:
        print("\n== чтение страниц: фазы и итог по плеерам")
    for url, steps in progress.items():
        print(f"  {url[:100]}")
        phases: dict[str, list[dict]] = {}
        for step in steps:
            phases.setdefault(step["payload"]["phase"], []).append(step)
        for phase, found in phases.items():
            last = found[-1]["payload"]
            seconds = (stamp(found[-1]["t"]) - stamp(found[0]["t"])).total_seconds()
            print(f"    фаза {phase:<9} {clock(found[0]['t'])}→{clock(found[-1]['t'])} {seconds:5.0f} с  "
                  f"{last['current']} из {last['total']}" + (f"  {last['current'] / seconds:.1f} строк/с" if seconds >= 1 else ""))
    for row in reads:
        data = row["payload"]
        print(f"  {clock(row['t'])} {data['url'][:90]}: {'прочитана' if data.get('ok') else 'НЕ ПРОЧИТАНА'} за {data.get('seconds')} с"
              f"{', строк ' + str(data['rows']) if data.get('rows') is not None else ''}"
              f"{' — ' + str(data.get('reason')) if data.get('reason') else ''}")
        for each in data.get("players") or []:
            states = ", ".join(f"{name} {count}" for name, count in sorted(each["states"].items()))
            heights = ", ".join(f"{height}p×{count}" for height, count in sorted(each["heights"].items(), key=lambda item: -int(item[0])))
            print(f"      {each['label']:<14} строк {each['rows']:>5}: {states}{'; кадр: ' + heights if heights else ''}")
        for each in data.get("causes") or []:
            print(f"      причина ×{each['rows']:<4} {each['player']:<16} {each['state']:<12} {str(each['reason'])[:170]}")
        for each in data.get("unread") or []:
            print(f"      не прочитана часть {each.get('part')}: {str(each.get('reason'))[:150]}")

    asks: dict[str, list[tuple[int, str, str, float]]] = {}
    rechecks = []
    for row in rows:
        if row["e"] != "log" or row.get("group") != "sources":
            continue
        message = row.get("message") or ""
        found = ASK.match(message)
        if found:
            asks.setdefault(found.group(1), []).append((int(found.group(2)), found.group(3), found.group(4), float(found.group(5))))
        elif "без окончательного ответа" in message or "подтвердилось" in message or "ответом хоста" in message:
            rechecks.append((row["t"], message))
    if asks:
        print("\n== опрос плееров (по журналу)")
    for player, found in asks.items():
        spent = sorted(each[3] for each in found)
        outcomes: dict[str, int] = {}
        for attempt, kind, status, _seconds in found:
            key = f"{kind}{'' if status in ('200', 'None', '0') else ' ' + status} (попытка {attempt})" if kind != "playable" or attempt > 1 \
                else "playable"
            outcomes[key] = outcomes.get(key, 0) + 1
        print(f"  {player:<18} запросов {len(found):>5}, медиана {spent[len(spent) // 2]:.1f} с, наибольший {spent[-1]:.1f} с: "
              + ", ".join(f"{name} ×{count}" for name, count in sorted(outcomes.items(), key=lambda item: -item[1])))
    for at, message in rechecks:
        print(f"  {clock(at)} {message[:260]}")

    trace = [row for row in bus if row["topic"] == "planner.sync.trace"]
    synced = bool(trace) or any(row["topic"] == "downloads.fetch" for row in bus)  # the dump of a read has no sync
    online, shown = 0, {}
    for row in trace:
        data = row["payload"]["data"]
        if row["payload"]["kind"] == "replan":
            print(f"\n== план составляется заново ({clock(row['t'])}): записано неудач {len(data.get('recorded') or [])}, "
                  f"не скачалось на этот раз {len(data.get('for_this_sync') or [])}, "
                  f"видео другой длины {len(data.get('retimed') or [])}")
            continue
        if row["payload"]["kind"] != "plan":
            continue
        inputs, plan = data["inputs"], data["plan"]
        online = len(inputs["online"]) or online  # the rows of the sources are told with the first plan of a sync
        print(f"\n== план, круг {data['round']} ({clock(row['t'])}): онлайн-строк {online}, "
              f"файлов в активе {len(inputs['containers'])}, неудач {len(inputs['failed'])}, целей {len(plan['goals'])}")
        for goal in plan["goals"]:
            video = goal.get("video") or {}
            shot = video.get("snapshot") or {}
            dubs = [f"{each.get('voice')}←{(each.get('snapshot') or {}).get('player', '?')}" for each in goal.get("tracks") or []]
            line = (f"видео {video.get('kind')} {shot.get('source')} {shot.get('player')} {shot.get('height')}p; "
                    f"озвучки: {', '.join(dubs) or '—'}")
            slot = tuple(goal["slot"])
            if shown.get(slot) != line:  # a later plan shows only the episodes it changed
                print(f"  s{goal['slot'][0]:02d}e{goal['slot'][1]:02d}: {line}")
            shown[slot] = line

    print("\n== шаги") if synced else None
    started: dict[str, tuple[str, dict]] = {}
    last_done: dict[str, str] = {}
    for row in trace:
        kind, data = row["payload"]["kind"], row["payload"]["data"]
        if kind == "step":
            started[data["op"]] = (row["t"], data)
        elif kind == "done":
            at, step = started.get(data["op"], (row["t"], {}))
            last_done[data["op"]] = row["t"]
            slot = step.get("slot")
            where = f"s{slot[0]:02d}e{slot[1]:02d}" if slot else "      "
            print(f"  {where} {step.get('kind', '?'):<15} {clock(at)}→{clock(row['t'])} "
                  f"{(stamp(row['t']) - stamp(at)).total_seconds():6.0f} с  {data['outcome']:<7} {row_of(data['op'])[:70]}"
                  f"{'  ' + str(data.get('problem') or data.get('error') or '')[:160] if data['outcome'] != 'made' else ''}")

    print("\n== загрузки") if synced else None
    asked = {row["payload"]["key"]: row for row in bus if row["topic"] == "downloads.fetch"}
    tasks: dict[str, dict] = {}
    for row in bus:
        if row["topic"] == "downloads.queue.changed":
            for task in row["payload"].get("tasks") or []:
                tasks[task["id"]] = task
    begun = {row["payload"]["key"]: row["t"] for row in bus if row["topic"] == "downloads.fetch.started"}
    for row in bus:
        if row["topic"] != "downloads.fetch.finished":
            continue
        payload = row["payload"]
        ask = asked.get(payload["key"], {}).get("payload", {})
        variant = ask.get("variant") or {}
        at = begun.get(payload["key"], row["t"])
        seconds = (stamp(row["t"]) - stamp(at)).total_seconds()
        need = ask.get("need") or {}
        task = tasks.get(need.get("key", ""), {})
        stages = {each["name"]: each["seconds"] for each in task.get("stages") or []}
        figures = task.get("figures") or {}
        size = figures.get("size_mb") or (os.path.getsize(payload["path"]) / 1e6 if os.path.exists(payload["path"]) else None)
        taken = stages.get("download") or seconds
        speed = f"{size:7.1f} МБ {(figures.get('done_mb') or size) * 8 / taken:6.1f} Мбит/с" if size and taken else "размер неизвестен"
        speed += "  " + " ".join(f"{name} {spent:.0f}" for name, spent in stages.items() if spent >= 1)
        if figures.get("source_seconds") and figures.get("got_seconds"):
            speed += f"  длина {figures['got_seconds']:.1f} из {figures['source_seconds']:.1f} с"
        attempts = len([each for each in task.get("log") or [] if each.get("kind") == "attempt"])
        speed += f"  попыток {attempts}" if attempts > 1 else ""
        print(f"  {need.get('episode', ''):<7} {need.get('kind', ''):<6} {str(need.get('group') or ''):<12} "
              f"{variant.get('place', ''):<28.28} {variant.get('player_label', ''):<12} {str(variant.get('height') or ''):>4}p "
              f"{clock(at)}→{clock(row['t'])} {seconds:5.0f} с  {speed}  {'ok' if payload['ok'] else 'ОТКАЗ: ' + payload['error'][:160]}")

    print("\n== выравнивание") if synced else None
    submitted = [row for row in bus if row["topic"] == "conform.submit"]
    for row in bus:
        if row["topic"] != "conform.job.finished":
            continue
        payload = row["payload"]
        print(f"  {clock(row['t'])} задача {payload['job_id']} {payload['status']}"
              f"{' — ' + str(payload['error']) if payload.get('error') else ''}")
        for each in payload.get("results") or []:
            verdict = "отказ" if each.get("refused") else "пропуск" if each.get("skipped") else \
                "подозрение" if each.get("suspect") else "ok" if each.get("ok") else "провал"
            said = "; ".join([*(each.get("critical") or []), *(each.get("warnings") or [])]) or each.get("error") or ""
            print(f"      {each['dub'][:70]:<70} {verdict:<10} остаток {each.get('resid_ms') or 0:.1f} мс  {said[:120]}")
    if submitted:
        print(f"  отправлено задач: {len(submitted)}, первая {clock(submitted[0]['t'])}, последняя {clock(submitted[-1]['t'])}")

    print("\n== применение групп") if synced else None
    for row in trace:
        if row["payload"]["kind"] != "apply":
            continue
        data = row["payload"]["data"]
        ops = (data.get("group") or {}).get("ops") or []
        ends = [last_done[op] for op in ops if op in last_done]
        waited = f"ждала после последнего шага {(stamp(row['t']) - stamp(max(ends))).total_seconds():.0f} с" if ends else ""
        slots = ", ".join(f"s{slot[0]:02d}e{slot[1]:02d}" for slot in data["slots"])
        print(f"  {clock(row['t'])} {slots}: {'применена' if data['applied'] else 'ОСТАВЛЕНА'} {waited}"
              f"{' — ' + str(data.get('missing')) + ' ' + str(data.get('error') or '') if not data['applied'] else ''}")

    print("\n== журнал: предупреждения и ошибки")
    seen: dict[str, int] = {}
    for row in rows:
        if row["e"] == "log" and (row.get("level") in ("WARNING", "ERROR", "CRITICAL") or (logs and row.get("level") == "INFO"
                                                                                            and row.get("group") != "httpx")):
            text = f"{row.get('level')} {row.get('group')}: {row.get('message')}"
            if text not in seen:
                print(f"  {clock(row['t'])} {text[:330]}")
            seen[text] = seen.get(text, 0) + 1
    repeated = {text: count for text, count in seen.items() if count > 1}
    for text, count in repeated.items():
        print(f"  ×{count}: {text[:160]}")
    if not seen:
        print("  нет")


if __name__ == "__main__":
    main(sys.argv[1], "--logs" in sys.argv[2:])
    print("RUN_DUMP_DONE")
