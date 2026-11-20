"""Where the time of a count of a series goes, link by link, on a copy of the service's base: the real modules of the path
of the data (catalog, settings, rules, sources, voices, composition, planner) on one bus, no network (every request goes
to a proxy that does not exist), no sync, no download. A series is counted as the service counts it after a change: its
layout (`composition.recompute`, from the stored tables), then its plan and view (`composition.changed` → the planner).

Told: the layouts whose rows the counts of the start changed, the wall time of each count, the processor time of all the
threads of the process in it (what the work took, apart from waiting on a busy host) and whether it changed the stored
layout, every request over the bus by topic (count, total, longest), and the steps inside the
modules (reading and writing the tables, the projection, the plan, the view), each by its total.

    systemd-run --scope -q -p MemoryMax=1500M .venv/bin/python tools/checks/recount_profile.py 46 40 [--db <base>]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
# No request leaves the machine: a stored table is all a count reads.
for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ[name] = "http://127.0.0.1:9"
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

from core import BaseModule, Bus, Runner  # noqa: E402
from core.db import Database  # noqa: E402
from tools.checks.plan_real import copy_db  # noqa: E402

STEPS: dict[str, list[float]] = defaultdict(list)


def timed(owner, name: str, label: str) -> None:
    """`owner.name` (a function or a coroutine function) counted under `label` each time it is called."""
    original = getattr(owner, name)
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


class Probe(BaseModule):
    name = "probe"


async def main(series: list[int], base: str | None = None, rounds: int = 1) -> None:
    import core.plan.executor as executor
    import modules.composition.module as composition_module
    import modules.planner.module as planner_module
    from modules.catalog import CatalogModule
    from modules.composition import CompositionModule
    from modules.planner import PlannerModule
    from modules.rules import RulesModule
    from modules.settings import SettingsModule
    from modules.sources import SourcesModule
    from modules.voices import VoicesModule

    folder = tempfile.mkdtemp(prefix="recount_profile_")
    path = copy_db(os.path.join(folder, "app.db"), *([base] if base else []))
    db = Database(path)
    bus = Bus()
    probe = Probe(bus)
    composition, planner = CompositionModule(bus, db), PlannerModule(bus, db)
    modules = [CatalogModule(bus, db), SettingsModule(bus, db), RulesModule(bus, db),
               SourcesModule(bus, db, torrent_cache_dir=os.path.join(folder, "torrent_cache")), VoicesModule(bus, db),
               composition, planner, probe]
    # The steps inside the modules.
    timed(composition.repo, "stored", "composition: read the stored layout")
    timed(composition.repo, "write", "composition: write the layout")
    timed(composition, "_gather", "composition: what the neighbours say (tables, answers, groups)")
    timed(composition_module, "_derive", "composition: projection and comparison (pool)")
    timed(composition_module, "layout_print", "composition: the print of the inputs (pool)")
    timed(composition_module, "layout_of", "composition: the layout as the model for the planner (pool)")
    timed(planner_module, "_inputs_of", "planner: the inputs of the plan from the layout (pool)")
    timed(planner_module, "count", "planner: the goals, counted by the core (pool)")
    timed(executor, "plan", "planner: the plan of a sync (pool)")
    timed(planner_module, "_view_body", "planner: the view (pool)")
    timed(planner.repo, "containers", "planner: read the asset")
    # What the counts at the start changed: a layout whose rows came out other than stored is told with part `whole`.
    started = bus.subscribe("composition.changed")
    runner = Runner(bus, modules)
    await runner.start()
    await composition.idle()
    whole = []
    while not started.queue.empty():
        env = started.queue.get_nowait()
        if env.payload.get("part") == "whole":
            whole.append(env.payload["series_id"])
    print(f"START: layouts whose rows the counts of the start changed: {len(whole)} {sorted(set(whole))}")
    links: dict[str, list[float]] = defaultdict(list)
    asked: dict[str, tuple[str, float]] = {}

    def seen(env) -> None:
        if env.kind == "query":
            asked[env.correlation_id] = (env.topic, time.perf_counter())
        elif env.kind == "reply" and env.correlation_id in asked:
            topic, start = asked.pop(env.correlation_id)
            links[topic].append(time.perf_counter() - start)

    untap = bus.tap(seen)
    told = bus.subscribe("planner.series.changed")
    try:
        for series_id in series:
            plans = []
            for _ in range(rounds):
                STEPS.clear()
                links.clear()
                start, cpu = time.perf_counter(), time.process_time()
                answer = await probe.request("composition.recompute", {"series_id": series_id}, timeout=170)
                layout, layout_cpu = time.perf_counter() - start, time.process_time() - cpu
                while not told.queue.empty():
                    told.queue.get_nowait()
                start, cpu = time.perf_counter(), time.process_time()
                probe.publish_event("composition.changed", {"series_id": series_id, "rev": 0, "summary_only": False,
                                                            "part": "whole", "episodes": []})
                while True:
                    env = await asyncio.wait_for(told.queue.get(), 170)
                    # Taken when the view comes: reading it (a text of JSON made into a dict) is no part of the count.
                    came, came_cpu = time.perf_counter() - start, time.process_time() - cpu
                    if env.payload.get("series_id") == series_id:
                        break
                plans.append((came, came_cpu))
            plan, plan_cpu = plans[-1]
            row = ", ".join(f"{each * 1000:.0f}/{used * 1000:.0f}" for each, used in plans)
            print(f"SERIES {series_id}: layout counted in {layout * 1000:.0f} ms, processor {layout_cpu * 1000:.0f} ms of all "
                  f"threads (changed: {answer.get('changed')}), plan and view in {plan * 1000:.0f} ms, processor "
                  f"{plan_cpu * 1000:.0f} ms" + (f" (counts in a row, wall/processor: {row} ms)" if rounds > 1 else ""))
            print("  requests over the bus (count, total ms, longest ms):")
            for topic, times in sorted(links.items(), key=lambda item: -sum(item[1])):
                print(f"    {topic:34} {len(times):4} {sum(times) * 1000:8.1f} {max(times) * 1000:8.1f}")
            print("  steps inside the modules (count, total ms):")
            for label, times in sorted(STEPS.items(), key=lambda item: -sum(item[1])):
                print(f"    {label:62} {len(times):4} {sum(times) * 1000:8.1f}")
    finally:
        untap()
        await runner.stop()
    print("RECOUNT_PROFILE_DONE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("series", type=int, nargs="+")
    parser.add_argument("--db", help="the base to copy instead of the service's (a fixed one, for counts to compare)")
    parser.add_argument("--times", type=int, default=1,
                        help="counts of each series in a row; the links and the steps are told of the last one")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.series, arguments.db, arguments.times))
