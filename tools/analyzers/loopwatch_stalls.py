"""The stalls of the event loop of the service over a window, from the loopwatch lines of logs/warning.log (core/loopwatch.py).

    loopwatch_stalls.py collect <warning.log> <raw.jsonl> <end, ISO time with +00:00> <hours>   # copies the lines of the window
    loopwatch_stalls.py read <raw.jsonl>                                                         # a pure reader of the raw lines

`read` tells how many stalls (the watch reports those of 100 ms and more), their total and quantiles, the count by length, the
hours with most of them, the innermost frame of the project that held the loop (count, total, longest), the stalls with a frame
of the code that does the counting of layout, plan and view, the stalls in which a thread of the pool of computation held the
interpreter, and the stalls in starting a program (core/proc.py). The innermost-frame patterns name both the old (`core/plan/...`,
`modules/composition/...`) and the new (`calc/...`) places of the counting code.
"""
import collections
import datetime
import json
import re
import sys

STALL = re.compile(r"цикл событий стоял (\d+) мс; держал \((\d+) из (\d+) замеров\): (.*)")


def collect(log, out, end_iso, hours):
    end = datetime.datetime.fromisoformat(end_iso)
    start = end - datetime.timedelta(hours=float(hours))
    kept = total = 0
    with open(log, encoding="utf-8") as src, open(out, "a", encoding="utf-8") as dst:
        for line in src:
            if '"group": "loopwatch"' not in line:
                continue
            total += 1
            stamp = datetime.datetime.fromisoformat(json.loads(line)["timestamp"])
            if start <= stamp <= end:
                dst.write(line)
                kept += 1
    print(f"window {start.isoformat()} .. {end.isoformat()}: kept {kept} of {total} loopwatch lines")
    print("COLLECT_DONE")


def read(raw):
    stalls = []
    for line in open(raw, encoding="utf-8"):
        d = json.loads(line)
        m = STALL.match(d["message"])
        if not m:
            print("UNPARSED", d["message"][:120])
            continue
        ms, got, of, chain = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
        stalls.append((d["timestamp"], ms, got, of, chain))
    n = len(stalls)
    if not n:
        print("no stalls")
        return
    durs = sorted(s[1] for s in stalls)
    pct = lambda p: durs[min(n - 1, int(p * n))]
    print(f"STALLS {n}; total {sum(durs)} ms; min {durs[0]} median {pct(.5)} p90 {pct(.9)} p99 {pct(.99)} max {durs[-1]} ms")
    buckets = collections.Counter()
    for d in durs:
        buckets["100-199" if d < 200 else "200-499" if d < 500 else "500-999" if d < 1000 else "1000-1999" if d < 2000 else "2000+"] += 1
    print("by length (ms):", dict(buckets))
    print("first", stalls[0][0], "last", stalls[-1][0])
    hours = collections.Counter(s[0][:13] for s in stalls)
    print("stalls per UTC hour (top):", hours.most_common(6))
    by_where = collections.defaultdict(list)
    for ts, ms, got, of, chain in stalls:
        inner = chain.split(" ← ")[0] if chain else "?"
        by_where[inner].append(ms)
    print("where (innermost frame): count, total ms, max ms")
    for where, items in sorted(by_where.items(), key=lambda kv: -sum(kv[1]))[:12]:
        print(f"  {len(items):4} {sum(items):7} {max(items):6}  {where[:150]}")
    calc = re.compile(r"core/plan/(planner|tables|inputs|marks)|core/(variant_queue|subtitle_queue|voice_names)|modules/rules/engine|"
                      r"modules/composition/(project|settings|levers|queues|compilations)|modules/unpack/layout|"
                      r"modules/planner/(view|packing)|calc/")
    mine = [s for s in stalls if calc.search(s[4])]
    pool = [s for s in stalls if "интерпретатор в это время держал поток compute" in s[4]]
    spawn = [s for s in stalls if "core/proc.py" in s[4]]
    print(f"stalls with a frame of the code that goes to calc (any thread): {len(mine)}, {sum(s[1] for s in mine)} ms total, "
          f"max {max((s[1] for s in mine), default=0)} ms")
    print(f"stalls where a thread of the pool of computation held the interpreter: {len(pool)}, {sum(s[1] for s in pool)} ms total")
    print(f"stalls with a frame of core/proc.py (starting a program): {len(spawn)}, {sum(s[1] for s in spawn)} ms total")
    print("longest 5:")
    for ts, ms, got, of, chain in sorted(stalls, key=lambda s: -s[1])[:5]:
        print(f"  {ts} {ms} ms ({got}/{of}) {chain[:230]}")
    print("READ_DONE")


if sys.argv[1] == "collect":
    collect(*sys.argv[2:6])
else:
    read(sys.argv[2])
