"""Situations on real series: what the planner of the table of an episode (calc/plan) does when the sources or the
choices of a real series change, played in the world of the simulator (tests/plan_world.py).

The series is read by plan_real (a copy of the database, the real constructor). The world knows the truth of every
video: the episode the layout gives it now. A situation changes the inputs — a source loses all its videos, a rule of a
profile is removed and the real constructor reads the tables again, an episode or a group is switched off, the reference
is deleted, a higher reference comes — and the world syncs: downloads, cuts and alignments run, an alignment of a voice-over
and a video of different episodes fails as on the real service; then the invariants of the model are checked.

Usage: plan_situations.py <series id>... [--out DIR]   (the report holds the user's data: keep it out of git)
Prints `PLAN_SITUATIONS_DONE` last."""
from __future__ import annotations

import argparse
import copy
import os
import sys
from collections import Counter
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "tests"))

import plan_real  # noqa: E402
from plan_world import SiteRow, World, truth_of  # noqa: E402

from calc.plan.tables import Inputs, OnlineRow, RowId  # noqa: E402


def world_of(inputs: Inputs) -> World:
    """The world of a real series: the sources as read now, the asset, the truth of every video = its number now."""
    world = World(choices=inputs.choices)
    for row in inputs.online:
        truth = tuple(truth_of(row.season, number) for number in range(row.start, row.end + 1))
        world.site.setdefault(row.id.source_id, []).append(SiteRow(row, truth))
        world.content[("site", row.id)] = truth
    world.online = {source: tuple(each.row for each in rows) for source, rows in world.site.items()}
    for container in inputs.containers:
        truth = (truth_of(*container.video.episode),)
        world.containers[container.name] = container
        world.content[container.video.key] = truth
        for track in container.tracks:
            world.content[("track", container.name, track.dub)] = truth
    return world


def renumbered(world: World, rows: list[OnlineRow]) -> World:
    """The world whose sources now give `rows` (the real constructor with other rules); the truth does not change."""
    truth = {row.id: each.content for source in world.site.values() for each in source for row in [each.row]}
    world.site = {}
    for row in rows:
        made = tuple(truth_of(row.season, number) for number in range(row.start, row.end + 1))
        world.site.setdefault(row.id.source_id, []).append(SiteRow(row, truth.get(row.id, made)))
    for source in list(world.online):
        world.site.setdefault(source, [])
    return world


def outcome(world: World, title: str) -> dict:
    step = world.sync(title)
    try:
        world.check(step)
        broken = ""
    except AssertionError as exc:
        broken = str(exc)[:200]
    kinds = Counter(op.kind for op in step.plan.ops)
    tracks = Counter(way.kind + ("·свой" if way.own else "") for goal in step.plan.goals for way in goal.tracks
                     if way.kind != "reuse" or way.own)
    groups = step.plan.groups
    details = []
    for goal in step.plan.goals:
        ways = [f"{way.voice}: {way.kind}{' (свой звук)' if way.own else ''}" for way in goal.tracks
                if way.kind != "reuse" or way.own]
        if goal.video.kind != "asset" or ways:
            details.append(f"серия {goal.slot[1]}: видео {goal.video.kind} {goal.video.snapshot.group} "
                           f"{goal.video.snapshot.height}p; " + ("; ".join(ways) or "дорожки как есть"))
    return {
        "details": details,
        "title": title, "ops": kinds, "tracks": tracks, "failed": len(step.failed),
        "blocked": sum(1 for group in groups if group.slots not in step.applied and group.ops),
        "remove": sum(len(group.remove) for group in groups if group.slots in step.applied),
        "hide": sum(len(group.hide) for group in groups if group.slots in step.applied),
        "lost": sum(len(group.lost) for group in groups if group.slots in step.applied),
        "broken": broken,
    }


async def situations(real: plan_real.RealSeries, series_id: int) -> tuple[list[dict], Counter]:
    inputs, skipped, info = await plan_real.inputs_of(real, series_id)
    # «As it is» is the work the series has now; every situation is played on the world that work has settled, so a row
    # shows what the situation changes and nothing else.
    base = world_of(inputs)
    found = [outcome(base, "как есть (работа, которая ждёт сейчас)")]
    inputs = base.inputs(base.history[-1].online, base.choices)
    sources = sorted({row.id.source_id for row in inputs.online})
    for source in sources:
        world = copy.deepcopy(base)
        world.site[source] = []
        found.append(outcome(world, f"источник {source} ({info['places'].get(source, '')}) удалил все видео"))
    profiles = sorted({each["parser_profile_id"] for each in info["card"]["sources"] if each.get("parser_profile_id")})
    for profile_id in profiles:
        rules = await real.rules_of(profile_id)
        for place, rule in enumerate(rules):
            changed, _, _ = await plan_real.inputs_of(real, series_id, {profile_id: rules[:place] + rules[place + 1:]})
            if set(changed.online) == set(inputs.online):
                continue  # the rule changes nothing in this series
            world = renumbered(copy.deepcopy(base), list(changed.online))
            found.append(outcome(world, f"профиль {profile_id}: удалено правило «{(rule.get('name') or '').strip()}»"))
    with_file = [each for each in inputs.containers if not each.off]
    if with_file:
        first = with_file[0]
        slot = first.video.episode
        world = copy.deepcopy(base)
        world.choose(off_episodes=world.choices.off_episodes | {slot})
        found.append(outcome(world, f"отключена серия {slot[1]}"))
        world = copy.deepcopy(base)
        world.delete("video", first.name)
        found.append(outcome(world, f"«Удалить» у видео серии {slot[1]}"))
        reference = next((row for row in inputs.online if row.id == first.video.timeline), None)
        if reference is not None:
            # The row of the reference is numbered as the next episode on purpose; what the video is does not change.
            world = copy.deepcopy(base)
            for each in world.site[reference.id.source_id]:
                if each.row.id == reference.id:
                    each.row = replace(each.row, start=slot[1] + 1, end=slot[1] + 1)
            found.append(outcome(world, f"строка референса серии {slot[1]} получила номер {slot[1] + 1}"))
            world = copy.deepcopy(base)
            higher = replace(reference, id=RowId(reference.id.source_id, reference.id.row_key + "-hd", "hd"),
                             height=reference.height * 2)
            world.site[reference.id.source_id].append(SiteRow(higher, (truth_of(*slot),)))
            found.append(outcome(world, f"вышел референс выше у серии {slot[1]} ({higher.height}p)"))
    for group in sorted({track.group for each in inputs.containers for track in each.tracks if track.group}):
        world = copy.deepcopy(base)
        world.choose(off_groups=world.choices.off_groups | {group})
        found.append(outcome(world, f"отключена группа {group}"))
    return found, skipped


def report(series_id: int, found: list[dict], skipped: Counter) -> str:
    lines = [f"# Сериал {series_id}: ситуации", ""]
    if skipped:
        lines += ["Не учтено моделью симулятора: " + ", ".join(f"{name} — {count}" for name, count in skipped.items()), ""]
    lines += ["| Ситуация | Операции | Дорожки | Сбоев | Не применено групп | Уходит | Скрыто | Потеряно по команде | Инварианты |",
              "|---|---|---|---|---|---|---|---|---|"]
    for each in found:
        ops = ", ".join(f"{kind} {count}" for kind, count in sorted(each["ops"].items())) or "—"
        tracks = ", ".join(f"{kind} {count}" for kind, count in sorted(each["tracks"].items())) or "—"
        lines.append(f"| {each['title']} | {ops} | {tracks} | {each['failed']} | {each['blocked']} | {each['remove']} | "
                     f"{each['hide']} | {each['lost']} | {each['broken'] or 'да'} |")
    for each in found:
        if each["details"]:
            lines += ["", f"## {each['title']}", ""] + [f"- {line}" for line in each["details"][:12]]
            if len(each["details"]) > 12:
                lines.append(f"- … ещё {len(each['details']) - 12}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("series", nargs="+", type=int)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    path = plan_real.copy_db(os.path.join(args.out or "/tmp", "app-copy.db"))
    real = plan_real.RealSeries(path)
    for series_id in args.series:
        found, skipped = plan_real.run(situations(real, series_id))
        text = report(series_id, found, skipped)
        if args.out:
            with open(os.path.join(args.out, f"situations_{series_id}.md"), "w") as out:
                out.write(text)
        print(text)
    print("PLAN_SITUATIONS_DONE")


if __name__ == "__main__":
    main()
