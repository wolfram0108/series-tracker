"""What a sync of a series did, read from its dump (`logs/runs/<series>/<start>-<pid>.jsonl.gz`, module `runlog`): the tables
read, the plan of each round by episode (which row is the video, which the voice-overs), every step of the graph with its
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

    print("\n== таблицы источников")
    progress: dict[str, list[str]] = {}
    for row in bus:
        if row["topic"] == "sources.table.progress":
            progress.setdefault(row["payload"]["url"], []).append(row["t"])
    for row in bus:
        if row["topic"] in ("sources.table.ready", "sources.table.failed"):
            url = row["payload"]["url"]
            asked = progress.get(url)
            read = f"читалась {(stamp(row['t']) - stamp(asked[0])).total_seconds():.0f} с" if asked else "из базы"
            print(f"  {clock(row['t'])} {row['topic'].rsplit('.', 1)[1]:<6} {url[:90]} — {read}"
                  f"{' — ' + str(row['payload'].get('reason')) if row['payload'].get('reason') else ''}")

    trace = [row for row in bus if row["topic"] == "planner.sync.trace"]
    for row in trace:
        data = row["payload"]["data"]
        if row["payload"]["kind"] != "plan":
            continue
        inputs, plan = data["inputs"], data["plan"]
        print(f"\n== план, круг {data['round']} ({clock(row['t'])}): онлайн-строк {len(inputs['online'])}, "
              f"файлов в активе {len(inputs['containers'])}, неудач {len(inputs['failed'])}, целей {len(plan['goals'])}")
        for goal in plan["goals"]:
            video = goal.get("video") or {}
            shot = video.get("snapshot") or {}
            dubs = [f"{each.get('voice')}←{(each.get('snapshot') or {}).get('player', '?')}" for each in goal.get("tracks") or []]
            print(f"  s{goal['slot'][0]:02d}e{goal['slot'][1]:02d}: видео {video.get('kind')} {shot.get('source')} "
                  f"{shot.get('player')} {shot.get('height')}p; озвучки: {', '.join(dubs) or '—'}")

    print("\n== шаги")
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

    print("\n== загрузки")
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

    print("\n== выравнивание")
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

    print("\n== применение групп")
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
