"""Where the time of a recount of a series goes, on a copy of the service's base: the real modules of the path of the data
(catalog, settings, rules, sources, voices, composition, planner) on one bus, no network (every request goes to a proxy
that does not exist), no sync, no download. A series is counted as the service counts it after a change: its layout
(`composition.recompute`, from the stored tables), then its plan and view (`composition.changed` -> the planner).

    systemd-run --scope -q -p MemoryMax=2G -p MemorySwapMax=0 .venv/bin/python tools/checks/recount_profile.py \
        --db <copy of app.db> 46 40 --times 5 --out <raw.jsonl> [--root <tree to measure>]

`--root` names the tree whose code is measured (default: the tree this file lies in); to compare with the state before a change,
export that commit into another folder and give its path. The base is copied again for every run (the modules write their
tables). One raw line per (series, round, phase): wall time, processor time of all threads of the process, the requests over the
bus by topic (count, total, longest) and the steps inside the modules that exist in that tree. A copy of the base comes from
`plan_real.copy_db`. Wall time on a loaded box varies by a third between runs: compare the best of several runs, not one.
RECOUNT_DONE closes the run.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if "--root" in sys.argv:
    ROOT = os.path.abspath(sys.argv[sys.argv.index("--root") + 1])
sys.path.insert(0, ROOT)
os.chdir(ROOT)
# No request leaves the machine: a stored table is all a count reads.
for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ[name] = "http://127.0.0.1:9"
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

from core.bus import Bus  # noqa: E402
from core.module import BaseModule  # noqa: E402
from core.runner import Runner  # noqa: E402
from core.db import Database  # noqa: E402

STEPS: dict[str, list[float]] = defaultdict(list)


def timed(owner, name: str, label: str) -> bool:
    """`owner.name` (a function or a coroutine function) counted under `label` each time it is called."""
    original = getattr(owner, name, None)
    if original is None:
        return False
    if asyncio.iscoroutinefunction(original):
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return await original(*args, **kwargs)
            finally:
                STEPS[label].append(time.perf_counter() - start)
    else:
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                STEPS[label].append(time.perf_counter() - start)
    setattr(owner, name, wrapper)
    return True


def first_module(*names: str):
    for each in names:
        try:
            return importlib.import_module(each)
        except ImportError:
            continue
    return None


class Probe(BaseModule):
    name = "probe"


async def main(series: list[int], base: str, rounds: int, out: str | None) -> None:
    from core.topics import (COMPOSITION_CHANGED, COMPOSITION_RECOMPUTE, PLANNER_SERIES_CHANGED)
    composition_module = importlib.import_module("modules.composition.module")
    planner_module = importlib.import_module("modules.planner.module")
    rules_module = importlib.import_module("modules.rules.module")
    executor = first_module("modules.planner.executor", "core.plan.executor")
    from modules.catalog import CatalogModule
    from modules.composition import CompositionModule
    from modules.planner import PlannerModule
    from modules.rules import RulesModule
    from modules.settings import SettingsModule
    from modules.sources import SourcesModule
    from modules.voices import VoicesModule

    folder = tempfile.mkdtemp(prefix="recount_")
    path = os.path.join(folder, "app.db")
    shutil.copy(base, path)
    db = Database(path)
    bus = Bus()
    probe = Probe(bus)
    composition, planner = CompositionModule(bus, db), PlannerModule(bus, db)
    modules = [CatalogModule(bus, db), SettingsModule(bus, db), RulesModule(bus, db),
               SourcesModule(bus, db, torrent_cache_dir=os.path.join(folder, "torrent_cache")), VoicesModule(bus, db),
               composition, planner, probe]
    wrapped = []
    for owner, name, label in (
            (composition.repo, "stored", "composition: read the stored layout"),
            (composition.repo, "write", "composition: write the layout"),
            (composition, "_gather", "composition: what the neighbours say (tables, answers, groups)"),
            (composition_module, "_derive", "composition: projection and comparison (pool)"),
            (composition_module, "derive", "composition: projection and comparison (pool)"),
            (composition_module, "layout_of", "composition: the layout as the model for the planner (pool)"),
            (rules_module, "reply_of", "rules: the answer to the lines of a page (pool)"),
            (planner_module, "_inputs_of", "planner: the inputs of the plan from the layout (pool)"),
            (planner_module, "layout_inputs", "planner: the inputs of the plan from the layout (pool)"),
            (executor, "plan", "planner: the plan (pool)"),
            (planner_module, "_view_body", "planner: the view (pool)"),
            (planner_module, "view_body", "planner: the view (pool)"),
            (planner.repo, "containers", "planner: read the asset")):
        if owner is not None and timed(owner, name, label):
            wrapped.append(label)
    started = bus.subscribe(COMPOSITION_CHANGED.name)
    runner = Runner(bus, modules)
    await runner.start()
    await composition.idle()
    whole = []
    while not started.queue.empty():
        env = started.queue.get_nowait()
        if env.payload.get("part") == "whole":
            whole.append(env.payload["series_id"])
    print(f"START: layouts whose rows the counts of the start changed: {len(whole)}; steps timed: {len(wrapped)}")
    meta = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "root": ROOT, "db": os.path.basename(base),
            "loadavg": os.getloadavg(), "python": sys.version.split()[0]}
    sink = open(out, "a", encoding="utf-8") if out else None

    links: dict[str, list[float]] = defaultdict(list)
    asked: dict[str, tuple[str, float]] = {}

    def seen(env) -> None:
        if env.reply_to:  # a request of either kind: a query or a command that waits for its answer
            asked[env.correlation_id] = (env.topic, time.perf_counter())
        elif env.kind == "reply" and env.correlation_id in asked:
            topic, start = asked.pop(env.correlation_id)
            links[topic].append(time.perf_counter() - start)

    untap = bus.tap(seen)
    told = bus.subscribe(PLANNER_SERIES_CHANGED.name)

    def emit(series_id: int, round_: int, phase: str, wall: float, cpu: float) -> None:
        row = {**meta, "series": series_id, "round": round_, "phase": phase, "wall_ms": wall * 1000, "cpu_ms": cpu * 1000,
               "requests": {t: [len(v), sum(v) * 1000, max(v) * 1000] for t, v in links.items()},
               "steps": {k: [len(v), sum(v) * 1000] for k, v in STEPS.items()}}
        print(f"SERIES {series_id} round {round_} {phase}: wall {wall * 1000:.0f} ms, processor {cpu * 1000:.0f} ms")
        if sink:
            sink.write(json.dumps(row, ensure_ascii=False) + "\n")
            sink.flush()

    try:
        for series_id in series:
            for round_ in range(1, rounds + 1):
                STEPS.clear()
                links.clear()
                start, cpu = time.perf_counter(), time.process_time()
                await probe.request(COMPOSITION_RECOMPUTE, {"series_id": series_id}, timeout=170)
                emit(series_id, round_, "layout", time.perf_counter() - start, time.process_time() - cpu)
                while not told.queue.empty():
                    told.queue.get_nowait()
                STEPS.clear()
                links.clear()
                start, cpu = time.perf_counter(), time.process_time()
                probe.publish_event(COMPOSITION_CHANGED, {"series_id": series_id, "rev": 0, "summary_only": False,
                                                            "part": "whole", "episodes": []})
                while True:
                    env = await asyncio.wait_for(told.queue.get(), 170)
                    if env.payload.get("series_id") == series_id:
                        break
                emit(series_id, round_, "plan_view", time.perf_counter() - start, time.process_time() - cpu)
    finally:
        untap()
        await runner.stop()
        shutil.rmtree(folder, ignore_errors=True)
    print("RECOUNT_DONE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("series", type=int, nargs="+")
    parser.add_argument("--db", required=True, help="a copy of the service's base (copied again for the run)")
    parser.add_argument("--times", type=int, default=3, help="counts of each series in a row")
    parser.add_argument("--out", help="the raw lines of the run, appended")
    parser.add_argument("--root", help="the tree whose code is measured (default: the tree of this file)")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.series, arguments.db, arguments.times, arguments.out))
