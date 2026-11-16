"""The planner on the asset table the service holds now (plan_assets of a copy of its database), with the layout of the real
constructor: what the next sync of every series would do with its files. The files of the library are read, never written:
the order of the tracks of a file made before the planner is read from its tags, as the module reads it.

A series whose files are already its goals comes out with no step. Every step is either real work the plan owes or a place
where the table does not tell the files as they are (what a carried-over table got wrong).

Usage: plan_on_asset.py [series id ...] [--out DIR]   (no id: every series with an asset; the report holds the user's data)
Prints `PLAN_ON_ASSET_DONE` last."""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plan_real  # noqa: E402

from core.plan.files import Library, as_in_file  # noqa: E402
from core.plan.inputs import assets_against  # noqa: E402
from core.plan.planner import plan  # noqa: E402
from core.plan.tables import Inputs  # noqa: E402


async def series_report(real: plan_real.RealSeries, series_id: int) -> tuple[str, Counter]:
    inputs, _skipped, info = await plan_real.inputs_of(real, series_id)
    card = info["card"]
    files = Library(card["save_path"])
    held = [await as_in_file(files, each) if os.path.exists(files.path(each.name)) else None
            for each in inputs.containers]
    missing = sum(1 for each in held if each is None)
    containers = assets_against([each for each in held if each is not None], inputs.online)
    made = plan(Inputs(inputs.online, tuple(containers), inputs.choices), plan_real.namer_of(card))
    kinds = Counter(op.kind for op in made.ops)
    builds = [op.slot for op in made.ops if op.kind == "build"]
    renames = [(old, made.goal(slot).name) for group in made.groups for slot, old in group.kept
               if made.goal(slot).name != old]
    removes = [name for group in made.groups for name in group.remove]
    hides = [old for group in made.groups for old, _ in group.hide]
    lines = [f"# Сериал {series_id}", "",
             f"Файлов в таблице: {len(held)}, нет на диске: {missing}; целей: {len(made.goals)}.",
             "Операции: " + (", ".join(f"{kind} {count}" for kind, count in sorted(kinds.items())) or "нет") + ".",
             f"Пересборок: {len(builds)}; переименований: {len(renames)}; уходит: {len(removes)}; скрывается: {len(hides)}.", ""]
    for goal in made.goals:
        ways = [f"{way.voice}: {way.kind}" for way in goal.tracks if way.kind != "reuse"]
        if goal.slot in builds:
            lines.append(f"- серия {goal.slot}: видео {goal.video.kind}; " + ("; ".join(ways) or "дорожки как есть, пересборка"))
    lines += [f"- переименование: {old} → {new}" for old, new in renames[:20]]
    lines += [f"- уходит: {name}" for name in removes[:20]] + [f"- скрывается: {name}" for name in hides[:20]]
    return "\n".join(lines) + "\n", Counter({"builds": len(builds), "ops": len(made.ops), "renames": len(renames),
                                              "removes": len(removes), "hides": len(hides), "missing": missing})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("series", nargs="*", type=int)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    real = plan_real.RealSeries(plan_real.copy_db(os.path.join(args.out or "/tmp", "app-copy.db")))
    ids = args.series
    if not ids:
        conn = sqlite3.connect(f"file:{real.path}?mode=ro", uri=True)
        ids = [each for (each,) in conn.execute("SELECT DISTINCT series_id FROM plan_assets ORDER BY series_id")]
        conn.close()
    for series_id in ids:
        try:
            text, counts = plan_real.run(series_report(real, series_id))
        except Exception as exc:  # noqa: BLE001 - one series that cannot be read is a line of the report, not its end
            print(f"SERIES {series_id} не прочитан: {type(exc).__name__}: {exc}")
            continue
        if args.out:
            with open(os.path.join(args.out, f"plan_asset_{series_id}.md"), "w") as out:
                out.write(text)
        print(f"SERIES {series_id} " + " ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    print("PLAN_ON_ASSET_DONE")


if __name__ == "__main__":
    asyncio.set_event_loop_policy(None)
    main()
