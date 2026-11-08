"""The planner of the table of an episode (core/plan) on a real series: what it would do with the series as it is now.

The series is read by plan_real: a copy of the database, the layout of the real constructor, the asset and the choices.
A series that is settled should come out with an empty plan: every step in it is either real work not done yet or a
place where the model and the system differ.

Usage: plan_on_series.py <series id>... [--out DIR]   (the report holds the user's data: keep it out of git)
Prints `PLAN_ON_SERIES_DONE` last."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plan_real  # noqa: E402

from core.plan.planner import plan  # noqa: E402


def report(series_id: int, inputs, skipped: Counter, series: dict) -> str:
    made = plan(inputs, plan_real.namer_of(series))
    kinds = Counter(op.kind for op in made.ops)
    lines = [f"# Сериал {series_id}", "",
             f"Онлайн-строк: {len(inputs.online)}; файлов серий: {len(inputs.containers)}; "
             f"дорожек: {sum(len(each.tracks) for each in inputs.containers)}; целей: {len(made.goals)}.", ""]
    if skipped:
        lines += ["Не учтено моделью симулятора: " + ", ".join(f"{name} — {count}" for name, count in skipped.items()), ""]
    lines += ["## Операции плана", "", "| Вид | Число |", "|---|---|"]
    lines += [f"| {kind} | {count} |" for kind, count in sorted(kinds.items())] or ["| — | 0 |"]
    renames = [(old, made.goal(slot).name) for group in made.groups for slot, old in group.kept if made.goal(slot).name != old]
    removes = [name for group in made.groups for name in group.remove]
    hides = [pair for group in made.groups for pair in group.hide]
    lost = [pair for group in made.groups for pair in group.lost]
    lines += ["", f"Переименований: {len(renames)}; уходит файлов: {len(removes)}; скрывается: {len(hides)}; "
                  f"теряется дорожек по команде: {len(lost)}.", "", "## Серии с работой", ""]
    for goal in made.goals:
        ways = [f"{way.voice}: {way.kind}{' (свой звук)' if way.own else ''}" for way in goal.tracks
                if way.kind != "reuse" or way.own]
        if goal.video.kind != "asset" or ways:
            lines.append(f"- серия {goal.slot[1]}: видео {goal.video.kind} {goal.video.snapshot.group} "
                         f"{goal.video.snapshot.height}p; " + ("; ".join(ways) or "дорожки как есть"))
    if renames:
        lines += ["", "## Переименования", ""] + [f"- {old} → {new}" for old, new in renames[:40]]
    if hides:
        lines += ["", "## Скрываются (ничто их не несёт)", ""] + [f"- {old}" for old, _ in hides[:40]]
    return "\n".join(lines) + "\n"


async def series_report(real: plan_real.RealSeries, series_id: int) -> str:
    inputs, skipped, info = await plan_real.inputs_of(real, series_id)
    diff = plan_real.same_layout(info["rows"], info["stored"].rows)
    same = "да" if not diff else "нет: " + "; ".join(diff)
    references, voices = plan_real.agreement(real.path, series_id, inputs, plan(inputs))
    told = (f"Раскладка настоящего конструктора здесь совпадает с раскладкой сервиса: {same}.\n"
            f"Выбор планировщика против выбора сервиса — референс: {dict(references)}; озвучки: {dict(voices)}.")
    return report(series_id, inputs, skipped, info["card"]).replace("\n\n", f"\n\n{told}\n\n", 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("series", nargs="+", type=int)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    real = plan_real.RealSeries(plan_real.copy_db(os.path.join(args.out or "/tmp", "app-copy.db")))
    for series_id in args.series:
        text = plan_real.run(series_report(real, series_id))
        if args.out:
            with open(os.path.join(args.out, f"plan_series_{series_id}.md"), "w") as out:
                out.write(text)
        print(text)
    print("PLAN_ON_SERIES_DONE")


if __name__ == "__main__":
    main()
