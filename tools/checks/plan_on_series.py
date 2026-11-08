"""The planner of the table of an episode (core/plan) on a real series: what it would do with the series as it is now.

Read only. The rows come from the layout the real rules of the constructor made (`GET /api/series/{id}/composition`),
the priorities of players and the groups from the settings of voice-overs (`GET /api/voices/settings`), the asset from
the database of the service (media_items, voice_dubs) opened read-only. A series that is settled should come out with an
almost empty plan: every step in it is either real work not done yet or a place where the model and the system differ.

Usage: plan_on_series.py <series id>... [--out DIR]   (the report holds the user's data: keep it out of git)
Prints `PLAN_ON_SERIES_DONE` last."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from core.plan.planner import plan  # noqa: E402
from core.plan.tables import Choices, Container, Inputs, OnlineRow, RowId, Snapshot, Track, Video  # noqa: E402

API = "http://127.0.0.1:5000"


def login() -> str:
    env = dict(line.strip().split("=", 1) for line in open(os.path.join(ROOT, ".env")) if "=" in line and not line.startswith("#"))
    request = urllib.request.Request(API + "/api/login", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"username": env["ST_ADMIN_USER"], "password": env["ST_ADMIN_PASSWORD"]}).encode())
    with urllib.request.urlopen(request, timeout=20) as reply:
        return reply.headers.get("Set-Cookie").split(";", 1)[0]


def get(cookie: str, path: str):
    with urllib.request.urlopen(urllib.request.Request(API + path, headers={"Cookie": cookie}), timeout=120) as reply:
        return json.loads(reply.read())


def inputs_of(series_id: int, cookie: str, db: sqlite3.Connection) -> tuple[Inputs, Counter]:
    """The tables of the planner for a real series; `skipped` counts what the model of the simulator does not hold yet."""
    skipped: Counter = Counter()
    layout = get(cookie, f"/api/series/{series_id}/composition")
    settings = get(cookie, "/api/voices/settings")
    places = {each["source_id"]: " / ".join(each.get("place") or []) or each["url"] for each in layout["sources"]}
    online, by_key, by_link = [], {}, {}
    for row in layout["rows"]:
        if row["excluded"] or row["season"] is None or row["episode_start"] is None:
            continue
        if row["part"] is not None:
            skipped["строка с частью серии"] += 1
            continue
        made = OnlineRow(RowId(row["source_id"], row["row_key"], row.get("video") or ""), row["season"],
                         row["episode_start"], row["episode_end"] or row["episode_start"], row["player"],
                         row["voice_group"], max(row["heights"] or [0]), places.get(row["source_id"], ""), row["link"])
        online.append(made)
        by_key[(row["source_id"], row["row_key"])] = made
        if row["link"]:
            by_link[row["link"]] = made

    def row_id(source_id: int, row_key: str | None, link: str = "") -> RowId:
        found = by_key.get((source_id, row_key)) if row_key else by_link.get(link)
        return found.id if found is not None else RowId(source_id, row_key or link, "")

    def player_of(source_id: int, row_key: str | None) -> str:
        found = by_key.get((source_id, row_key))
        return found.player if found is not None else "?"

    db.row_factory = sqlite3.Row
    items = db.execute("SELECT * FROM media_items WHERE series_id=? AND status='completed' AND final_filename IS NOT NULL "
                       "AND plan_status='in_plan_single'", (series_id,)).fetchall()
    dubs = db.execute("SELECT * FROM voice_dubs WHERE series_id=? AND status='placed'", (series_id,)).fetchall()
    tracks_of: dict[tuple, list[Track]] = {}
    for dub in dubs:
        if dub["part"]:
            skipped["озвучка с частью серии"] += 1
            continue
        chain = json.loads(dub["chain"] or "[]")
        if not chain:
            skipped["озвучка без цепочки"] += 1
            continue
        candidate = chain[min(dub["attempt"], len(chain) - 1)]
        source_id = candidate.get("source_id")
        dub_id = row_id(source_id, candidate.get("row_key"), candidate.get("url", ""))
        aligned = dub["aligned_to"] or ""
        a_source, _, a_key = aligned.partition(":")
        aligned_to = row_id(int(a_source), a_key) if a_source.isdigit() else RowId(-1, aligned, "")
        snapshot = Snapshot(places.get(source_id, ""), candidate.get("player", "?"), dub["voice_label"], 720,
                            candidate.get("url", ""))
        tracks_of.setdefault((dub["season"], dub["episode"]), []).append(
            Track(dub_id, snapshot, (dub["season"], dub["episode"]), aligned_to))
    orders = {each["season"]: [voice["label"] for voice in each["voices"]] for each in layout.get("voice_orders", [])}
    order = [label for season in sorted(orders) for label in orders[season]]
    containers = []
    for item in items:
        if item["part"] is not None:
            skipped["файл части серии"] += 1
            continue
        slot = (item["season"], item["episode_start"])
        cut = json.loads(item["cut"]) if item["cut"] else None
        own = row_id(item["source_id"], item["row_key"])
        snapshot = Snapshot(places.get(item["source_id"], ""), player_of(item["source_id"], item["row_key"]),
                            item["voiceover_tag"], item["resolution"] or 0, item["source_url"] or "")
        if cut:
            compilation = row_id(cut["source_id"], None) if False else RowId(cut["source_id"], cut.get("key", ""), "")
            match = next((row for row in online if row.compilation and row.id.source_id == cut["source_id"]
                          and (row.start, row.end) == (cut.get("start"), cut.get("end"))), None)
            compilation = match.id if match is not None else compilation
            video = Video(compilation, Snapshot(places.get(cut["source_id"], ""), cut.get("player", "?"),
                                                cut.get("voice_group"), cut.get("height") or 0), slot, own, cut_of=compilation)
        else:
            video = Video(own, snapshot, slot, own)
        mine = tuple(sorted(tracks_of.get(slot, ()), key=lambda track: (order.index(track.group) if track.group in order
                                                                        else len(order), track.group or "")))
        containers.append(Container(item["final_filename"], video, mine, bool(item["is_ignored_by_user"])))
    # An episode switched off is told by the choice of the reference (mark `off`), also one that has no file.
    off = {(each["season"], each["episode"]) for each in get(cookie, f"/api/series/{series_id}/output")
           if each.get("mark") == "off" and each.get("part") is None}
    choices = Choices(players=tuple(settings["priority"]), sources=tuple(each["source_id"] for each in layout["sources"]),
                      groups=tuple(label for season in sorted(orders) for label in orders[season]),
                      off_episodes=frozenset(off))
    return Inputs(tuple(online), tuple(containers), choices), skipped


def namer_of(series: dict):
    """The series' own rule of names (modules/rules/formatter), as the container of an episode is named: an .mkv; a
    switched-off one hidden with `.off`."""
    from modules.rules import formatter

    def name(slot, video, off: bool) -> str:
        snapshot = video.snapshot
        item = {"season": slot[0], "episode_start": slot[1], "episode_end": None, "part": None,
                "resolution": snapshot.height, "voiceover_tag": snapshot.group, "source_url": snapshot.link}
        if video.kind == "cut" or (video.container is not None and video.container.video.cut_of is not None):
            item["cut"] = json.dumps({"height": snapshot.height, "voice_group": snapshot.group})
        made = formatter.format_name(series, formatter.build_metadata(series, item))
        return os.path.splitext(made)[0] + ".mkv" + (".off" if off else "")
    return name


def report(series_id: int, inputs: Inputs, skipped: Counter, series: dict) -> str:
    made = plan(inputs, namer_of(series))
    kinds = Counter(op.kind for op in made.ops)
    current = {each.name: each for each in inputs.containers}
    lines = [f"# Сериал {series_id}", "",
             f"Онлайн-строк: {len(inputs.online)}; файлов серий: {len(inputs.containers)}; "
             f"дорожек: {sum(len(each.tracks) for each in inputs.containers)}; целей: {len(made.goals)}.", ""]
    if skipped:
        lines += ["Не учтено моделью симулятора: " + ", ".join(f"{name} — {count}" for name, count in skipped.items()), ""]
    lines += ["## Операции плана", "", "| Вид | Число |", "|---|---|"]
    lines += [f"| {kind} | {count} |" for kind, count in sorted(kinds.items())] or ["| — | 0 |"]
    renames = [(slot, old, made.goal(slot).name) for group in made.groups for slot, old in group.kept
               if made.goal(slot).name != old]
    removes = [name for group in made.groups for name in group.remove]
    hides = [pair for group in made.groups for pair in group.hide]
    lost = [pair for group in made.groups for pair in group.lost]
    lines += ["", f"Переименований: {len(renames)}; уходит файлов: {len(removes)}; скрывается: {len(hides)}; "
                  f"теряется дорожек по команде: {len(lost)}.", "", "## Серии с работой", "",
              "| Серия | Видео | Дорожки: путь | Сейчас файл |", "|---|---|---|---|"]
    for goal in made.goals:
        if goal.video.kind == "asset" and all(way.kind == "reuse" and not way.own for way in goal.tracks) and \
                goal.video.container.name == goal.name:
            continue
        ways = Counter(way.kind + (" (свой звук)" if way.own else "") for way in goal.tracks)
        now = next((name for name, each in current.items() if (each.video.episode == goal.slot)), "—")
        lines.append(f"| {goal.slot[1]} | {goal.video.kind} {goal.video.snapshot.height}p {goal.video.snapshot.group} | "
                     + ", ".join(f"{kind}: {count}" for kind, count in sorted(ways.items())) + f" | {now} |")
    if renames:
        lines += ["", "## Переименования", ""] + [f"- {old} → {new}" for _, old, new in renames[:40]]
    if hides:
        lines += ["", "## Скрываются (ничто их не несёт)", ""] + [f"- {old}" for old, _ in hides[:40]]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("series", nargs="+", type=int)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    cookie = login()
    db = sqlite3.connect(f"file:{os.path.join(ROOT, 'app.db')}?mode=ro", uri=True)
    for series_id in args.series:
        inputs, skipped = inputs_of(series_id, cookie, db)
        db.row_factory = sqlite3.Row
        series = dict(db.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone())
        text = report(series_id, inputs, skipped, series)
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            with open(os.path.join(args.out, f"plan_series_{series_id}.md"), "w") as out:
                out.write(text)
        print(text)
    print("PLAN_ON_SERIES_DONE")


if __name__ == "__main__":
    main()
