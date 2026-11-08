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

from core.plan.marks import references_of  # noqa: E402
from core.plan.planner import plan  # noqa: E402


def report(series_id: int, inputs, skipped: Counter, series: dict) -> str:
    made = plan(inputs, plan_real.namer_of(series))
    kinds = Counter(op.kind for op in made.ops)
    lines = [f"# Сериал {series_id}", "",
             f"Онлайн-строк: {len(inputs.online)}; файлов серий: {len(inputs.containers)}; "
             f"дорожек: {sum(len(each.tracks) for each in inputs.containers)}; целей: {len(made.goals)}.", ""]
    online = {row.id for row in inputs.online}
    keys = {(row.id.source_id, row.id.row_key) for row in inputs.online}
    swapped = [each.name for each in inputs.containers if each.video.id not in online
               and (each.video.id.source_id, each.video.id.row_key) in keys]
    lines += [f"Файлов, за строкой которых теперь другое видео (номер хоста сменился): {len(swapped)}"
              + (" — " + ", ".join(swapped[:10]) if swapped else "") + ".", ""]
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
    made = plan(inputs)
    references, voices = plan_real.agreement(real.path, series_id, inputs, made)
    marks = marks_against(real.path, series_id, inputs, made)
    told = (f"Раскладка настоящего конструктора здесь совпадает с раскладкой сервиса: {same}.\n"
            f"Выбор планировщика против выбора сервиса — референс: {dict(references)}; озвучки: {dict(voices)}.\n"
            f"Объяснение выбора (отметка и строка) против записанного сервисом: {dict(marks)}.")
    return report(series_id, inputs, skipped, info["card"]).replace("\n\n", f"\n\n{told}\n\n", 1)


def marks_against(path: str, series_id: int, inputs, made) -> Counter:
    """The reference the plan tells of every episode (core/plan/marks) against the one scan wrote (scan_references): the
    mark and the row; an episode one of them does not name is counted apart."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    stored = {(r[0], r[1]): (r[2], r[3], r[4]) for r in conn.execute(
        "SELECT season, episode, mark, source_id, row_key FROM scan_references WHERE series_id=? AND part IS NULL",
        (series_id,))}
    conn.close()
    found = Counter()
    for ref in references_of(inputs, made):
        if ref.part is not None:
            continue
        theirs = stored.get((ref.season, ref.episode))
        mine = (ref.mark.value, ref.source_id, ref.row_key)
        found["нет у сервиса" if theirs is None else "то же" if theirs == mine else
              f"другое: {theirs[0]}→{mine[0]}"] += 1
    return found


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
