"""The answers of the window of the layout and of the plan of a series over HTTP, from a tree of code, on a copy of the service's
database, no network.

    window_json.py collect --root <tree> --db <copy of app.db> --out <answers.json> 46 40
    window_json.py compare <a.json> <b.json>
    window_json.py selftest

`collect` puts the real modules of the path of the data (catalog, settings, rules, sources, voices, composition, planner) and the
gateway on one bus over a fresh copy of the base, lets the counts of the start end, asks the routes of the layout (the whole,
with the excluded, the sources alone, the parts, the queues of several levels, the previews of the sources and of lines) and the
routes of the plan (the view, the references, the queue of the packing, and what the planner tells its neighbours over the bus:
files, seasons, the counts of the downloaded, the state of its work), and then sends the same commands of the levers, asking the
layout and the plan again after each; after them the commands of the plan («Удалить» of a file and of a track, taking it back,
the reset of the failures). It keeps a hash of the tables of the modules after the start and after each command, and the packets
of the events the window is told (`planner.series.changed` and the rest of the planner's, written as the stream of the gateway
writes them). No sync is run: nothing reaches the library or the network. `compare` is byte for byte: to find out whether a
change of code left the answers of the window as they were, collect from the tree before the change (`--root`, a commit
exported into a folder) and from the one after, and compare. `selftest` is the red control of `compare`. Nothing is written to
the base given; the copy is made again each run. A run ends with `WINDOW_JSON_DONE`; `compare` ends with `IDENTICAL` or
`DIFFERENT <n>`.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from urllib.parse import quote

TABLES = ("composition_rows", "composition_sources", "composition_state", "unknown_voices", "output_settings",
          "voice_orders", "voice_titles", "plan_assets", "plan_failures", "plan_commands", "plan_runs", "plan_outcomes",
          "plan_standing", "pack_cards")
# What the planner tells the window as events: written as the stream of the gateway writes them (the table SSE_MAP).
WATCHED = ("planner.series.changed", "planner.pack.changed", "planner.journal.changed", "work.series.state",
           "series.status.contribution", "series.downloaded.changed")


def written(value) -> str:
    """A reply or an event as text: a model as a module writes it for the window, a tuple as a list."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
                      default=lambda each: each.model_dump(mode="json"))


def tables_digest(path: str, series_id: int) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    for table in TABLES:
        rows = sorted(conn.execute(f"SELECT * FROM {table} WHERE series_id = ?", (series_id,)).fetchall(), key=repr)
        out[table] = {"rows": len(rows), "sha": hashlib.sha256(repr(rows).encode()).hexdigest()}
    conn.close()
    return out


async def quiet(bus, planner) -> None:
    """Returns when nothing is left to do: every envelope is taken apart, and no count of the view of a series is under way
    (the planner counts it beside its handlers). The signal is the end of the work, not a pause."""
    while True:
        await bus.settled()
        under_way = [task for task in planner._counting.values() if not task.done()]
        if not under_way:
            return
        await asyncio.gather(*under_way, return_exceptions=True)


async def collect(root: str, base: str, out: str, series: list[int]) -> None:
    from core.bus import Bus
    from core.db import Database
    from core.module import BaseModule, BusRequestError
    from core.runner import Runner
    from modules.catalog import CatalogModule
    from modules.composition import CompositionModule
    from modules.gateway import GatewayModule
    from modules.gateway.module import SSE_MAP
    from modules.planner import PlannerModule
    from modules.rules import RulesModule
    from modules.settings import SettingsModule
    from modules.sources import SourcesModule
    from modules.voices import VoicesModule
    import httpx

    class Probe(BaseModule):
        name = "probe"

    folder = tempfile.mkdtemp(prefix="window_json_")
    path = os.path.join(folder, "app.db")
    shutil.copy(base, path)
    db = Database(path)
    bus = Bus()
    composition = CompositionModule(bus, db)
    planner = PlannerModule(bus, db)
    probe = Probe(bus)
    gateway = GatewayModule(bus, static_dir=folder, templates_dir=folder, auth_required=False)
    modules = [CatalogModule(bus, db), SettingsModule(bus, db), RulesModule(bus, db),
               SourcesModule(bus, db, torrent_cache_dir=os.path.join(folder, "torrent_cache")), VoicesModule(bus, db),
               composition, planner, probe, gateway]
    # The packets of the events the window is told, as the stream of the gateway writes them (SSE_MAP, json.dumps).
    events: list[tuple[str, str]] = []

    def seen(env) -> None:
        if env.topic in WATCHED:
            sse = SSE_MAP.get(env.topic)
            payload = sse.form(env.payload) if sse is not None and sse.form else env.payload
            events.append((env.topic, json.dumps(payload, ensure_ascii=False)))

    untap = bus.tap(seen)
    runner = Runner(bus, modules)
    await runner.start()
    await composition.idle()
    await quiet(bus, planner)
    answers: dict[str, dict] = {}

    def record(name: str, status: int, body: bytes) -> None:
        answers[name] = {"status": status, "length": len(body), "sha": hashlib.sha256(body).hexdigest(),
                         "body": base64.b64encode(body).decode()}

    async def ask(client, name, method, url, **kwargs):
        response = await client.request(method, url, **kwargs)
        record(name, response.status_code, response.content)
        return response

    async def over_bus(name: str, topic: str, payload: dict) -> None:
        """What a module answers another: the model or the dictionary, written as text; a refusal as its words."""
        try:
            record(name, 200, written(await probe.request(topic, payload)).encode())
        except BusRequestError as exc:
            record(name, 500, str(exc).encode())

    def told(label: str) -> None:
        """The packets told since the last time, by topic: how many, which ones, the last."""
        taken = list(events)
        events.clear()
        by_topic: dict[str, list[str]] = {}
        for topic, text in taken:
            by_topic.setdefault(topic, []).append(hashlib.sha256(text.encode()).hexdigest())
        answers[f"events:{label}"] = {"events": {topic: {"count": len(each), "distinct": sorted(set(each)), "last": each[-1]}
                                                 for topic, each in sorted(by_topic.items())}}

    async def plan_window(client, label: str, sid: int) -> None:
        """The plan, its references and what the planner tells its neighbours, after the work of the planner has ended."""
        await quiet(bus, planner)
        await ask(client, f"{label}:plan", "GET", f"/api/series/{sid}/plan")
        await ask(client, f"{label}:output", "GET", f"/api/series/{sid}/output")
        told(label)

    told("start")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway.app), base_url="http://test",
                                 timeout=120) as client:
        for sid in series:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            sources = conn.execute("SELECT id, url, parser_profile_id FROM series_sources WHERE series_id=? ORDER BY id",
                                   (sid,)).fetchall()
            conn.close()
            answers[f"{sid}:tables:start"] = tables_digest(path, sid)
            await plan_window(client, f"{sid}:start", sid)
            await ask(client, f"{sid}:plan:unknown-series", "GET", "/api/series/999999/plan")
            await ask(client, f"{sid}:output:unknown-series", "GET", "/api/series/999999/output")
            await ask(client, f"{sid}:pack:queue", "GET", "/api/pack/queue")
            for topic, payload in (("planner.series.get", {"series_id": sid}), ("planner.references.get", {"series_id": sid}),
                                   ("planner.work.get", {"series_id": sid}), ("planner.files.get", {"series_id": sid}),
                                   ("planner.seasons", {"series_id": sid}), ("planner.downloaded_counts", {}),
                                   ("planner.pack.get", {})):
                await over_bus(f"{sid}:bus:{topic}", topic, payload)
            base_url = f"/api/series/{sid}/composition"
            whole = await ask(client, f"{sid}:composition", "GET", base_url)
            await ask(client, f"{sid}:composition:include_excluded", "GET", base_url + "?include_excluded=true")
            await ask(client, f"{sid}:composition:sources_only", "GET", base_url + "?sources_only=true")
            await ask(client, f"{sid}:composition:part=sources", "GET", base_url + "?part=sources")
            await ask(client, f"{sid}:composition:part=levers", "GET", base_url + "?part=levers")
            await ask(client, f"{sid}:composition:part=whole+excluded", "GET", base_url + "?part=whole&include_excluded=true")
            layout = whole.json()
            settings = layout.get("settings") or []
            episodes = [(each["season"], each["episode"]) for each in settings]
            picked = [episodes[0], episodes[len(episodes) // 2], episodes[-1]] if episodes else []
            if len(picked) >= 2:
                query = "&".join(f"episodes={s}:{e}" for s, e in picked[:2])
                await ask(client, f"{sid}:composition:part=levers+episodes", "GET", base_url + f"?part=levers&{query}")
            seasons = sorted({s for s, _ in episodes})
            queues_url = f"/api/series/{sid}/output/queues"
            await ask(client, f"{sid}:queues:series", "GET", queues_url)
            for season in seasons:
                await ask(client, f"{sid}:queues:season={season}", "GET", queues_url + f"?season={season}")
            for season, episode in picked:
                await ask(client, f"{sid}:queues:{season}x{episode}", "GET", queues_url + f"?season={season}&episode={episode}")
            for view in (layout.get("compilations") or [])[:2]:
                await ask(client, f"{sid}:queues:compilation:{view['index']}", "GET",
                          queues_url + f"?season={view['season']}&compilation={quote(view['ref']['key'])}")
            await ask(client, f"{sid}:queues:unknown-series", "GET", "/api/series/999999/output/queues?season=1")
            await ask(client, f"{sid}:composition:unknown-series", "GET", "/api/series/999999/composition")
            lines_of = None
            for source_id, url, profile in sources:
                reply = await ask(client, f"{sid}:preview:{source_id}", "POST", "/api/sources/preview",
                                  json={"url": url, "profile_id": profile, "refresh": False, "series_season": "1"})
                await ask(client, f"{sid}:preview:{source_id}:no-profile", "POST", "/api/sources/preview",
                          json={"url": url, "refresh": False})
                await ask(client, f"{sid}:profile-test:{source_id}", "POST", "/api/parser-profiles/test",
                          json={"url": url, "profile_id": profile, "series_season": "1", "refresh": False})
                if lines_of is None and reply.status_code == 200 and reply.json().get("lines"):
                    lines_of = ([each["line"] for each in reply.json()["lines"][:60]], profile)
            if lines_of is not None:
                await ask(client, f"{sid}:preview:lines", "POST", "/api/sources/preview",
                          json={"lines": lines_of[0], "profile_id": lines_of[1], "series_season": "1"})
                await ask(client, f"{sid}:preview:lines:no-profile", "POST", "/api/sources/preview",
                          json={"lines": lines_of[0]})
            await ask(client, f"{sid}:preview:empty-url", "POST", "/api/sources/preview",
                      json={"url": "https://nowhere.test/p", "refresh": False})

            # --- the commands of the levers, the layout asked again after each -----------------------------------------------
            if not picked:
                continue
            season, episode = picked[1] if len(picked) > 1 else picked[0]
            level = queues_url + f"?season={season}&episode={episode}"
            queue = (await client.get(level)).json()
            rows = queue["reference"]["rows"]
            names = [{"group": each["group"], "source_id": each["source_id"], "player": each["player"]} for each in rows]
            target = None
            if rows:
                first = rows[0]
                target = "variant:" + json.dumps([first["group"], first["source_id"], first["player"]],
                                                 ensure_ascii=False, separators=(",", ":"))
            groups = sorted({each["group"] for each in rows})
            put = "/api/series/%d/output/settings" % sid
            steps = [
                ("flag", "PUT", put, {"season": season, "episode": episode, "lever": "reference.allow", "target": target, "value": False}),
                ("order", "PUT", put, {"season": season, "episode": episode, "lever": "reference.order", "value": list(reversed(names))}),
                ("markup-flag", "PUT", put, {"season": season, "episode": episode, "lever": "markup.allow", "target": target, "value": False}),
                ("markup-order-season", "PUT", put, {"season": season, "lever": "markup.order", "value": names[:2]}),
                ("batch", "PUT", put + "/batch", {"items": [
                    {"season": season, "episode": episode, "lever": "markup.order", "value": list(reversed(names))},
                    {"season": season, "episode": episode, "lever": "reference.allow", "target": target, "value": True}]}),
                ("unknown-lever", "PUT", put, {"season": season, "lever": "no.such", "value": 1}),
                ("not-pickable", "PUT", put, {"season": season, "episode": episode, "lever": "reference.order",
                                              "value": [{"group": "no group", "source_id": 1, "player": "no player"}]}),
                ("voice-title", "PUT", f"/api/series/{sid}/output/voice-title", {"label": groups[0] if groups else "x", "title": "T"}),
                ("voice-title-back", "PUT", f"/api/series/{sid}/output/voice-title", {"label": groups[0] if groups else "x", "title": ""}),
                ("voice-order", "PUT", f"/api/series/{sid}/output/voice-order", {"season": season, "labels": list(reversed(groups))}),
                ("reset-lever", "POST", put + "/reset", {"season": season, "episode": episode, "lever": "reference.allow", "target": target}),
                ("reset-queue", "POST", put + "/reset", {"season": season, "episode": episode, "queue": "markup"}),
                ("reset-level", "POST", put + "/reset", {"season": season, "episode": episode}),
                ("reset-bad", "POST", put + "/reset", {"season": season, "lever": "no.such"}),
            ]
            for label, method, url, body in steps:
                await ask(client, f"{sid}:cmd:{label}", method, url, json=body)
                await ask(client, f"{sid}:cmd:{label}:levers", "GET", base_url + "?part=levers")
                await ask(client, f"{sid}:cmd:{label}:queues", "GET", level)
                await plan_window(client, f"{sid}:cmd:{label}", sid)
                answers[f"{sid}:cmd:{label}:tables"] = tables_digest(path, sid)
            await ask(client, f"{sid}:composition:after", "GET", base_url)

            # --- the commands of the plan: a file or a track is to be deleted, taken back; the failures are reset ----------
            assets = (await client.get(f"/api/series/{sid}/plan")).json().get("assets") or []
            failures = (await client.get(f"/api/series/{sid}/plan")).json().get("failures") or []
            if not assets:
                continue
            delete, cancel = f"/api/series/{sid}/plan/delete", f"/api/series/{sid}/plan/delete/cancel"
            video = {"kind": "video", "name": assets[0]["name"]}
            commands = [("delete-no-file", delete, {"kind": "video", "name": "no-such-file.mkv"}),
                        ("delete-no-track", delete, {"kind": "track", "name": assets[0]["name"],
                                                     "dub": {"source_id": 0, "row_key": "none", "video": ""}}),
                        ("delete-video", delete, video), ("delete-video-again", delete, video),
                        ("cancel-video", cancel, video), ("cancel-video-again", cancel, video)]
            holder = next((each for each in assets if each["tracks"]), None)
            if holder is not None:
                track = {"kind": "track", "name": holder["name"], "dub": holder["tracks"][0]["row"]}
                commands += [("delete-track", delete, track), ("cancel-track", cancel, track)]
            for label, url, body in commands:
                await ask(client, f"{sid}:plan:{label}", "POST", url, json=body)
                await plan_window(client, f"{sid}:plan:{label}", sid)
                answers[f"{sid}:plan:{label}:tables"] = tables_digest(path, sid)
            reset = f"/api/series/{sid}/failures/reset"
            levels = [("kind-cut", {"kind": "cut"})]
            if failures:
                first = failures[0]
                levels += [("episode", {"season": first["episodes"][0][0], "episode": first["episodes"][0][1]})
                           if first["episodes"] else ("kind", {"kind": first["kind"]}),
                           ("voice", {"voice": first["voice"]} if first["voice"] else {"kind": first["kind"]})]
            levels += [("all", None)]
            for label, body in levels:
                await ask(client, f"{sid}:forget:{label}", "POST", reset, **({} if body is None else {"json": body}))
                await plan_window(client, f"{sid}:forget:{label}", sid)
                answers[f"{sid}:forget:{label}:tables"] = tables_digest(path, sid)
    untap()
    await runner.stop()
    with open(out, "w", encoding="utf-8") as sink:
        json.dump({"root": root, "when": time.strftime("%Y-%m-%d %H:%M:%S"), "answers": answers}, sink)
    shutil.rmtree(folder, ignore_errors=True)
    print(f"COLLECTED {len(answers)} answers; bytes {sum(each.get('length', 0) for each in answers.values())}")
    print("WINDOW_JSON_DONE")


def compare(first: str, second: str) -> int:
    a, b = json.load(open(first, encoding="utf-8"))["answers"], json.load(open(second, encoding="utf-8"))["answers"]
    keys = sorted(set(a) | set(b))
    different = []
    same_bytes = 0
    for key in keys:
        x, y = a.get(key), b.get(key)
        if x is None or y is None:
            different.append((key, "present in one only"))
        elif "sha" in x:
            if x["sha"] != y.get("sha") or x["status"] != y.get("status") or x["length"] != y.get("length"):
                at = next((i for i, (p, q) in enumerate(zip(base64.b64decode(x["body"]), base64.b64decode(y["body"]))) if p != q), None)
                different.append((key, f"status {x['status']}/{y.get('status')} length {x['length']}/{y.get('length')} first byte {at}"))
            else:
                same_bytes += x["length"]
        elif "events" in x:
            # The packets told: the same distinct packets and the same last one of each topic; how many times each came is
            # a note (a count that came while another was counted may be one more or one less).
            packets = {side: {topic: (value["distinct"], value["last"]) for topic, value in (each.get("events") or {}).items()}
                       for side, each in (("a", x), ("b", y))}
            counts = {side: {topic: value["count"] for topic, value in (each.get("events") or {}).items()}
                      for side, each in (("a", x), ("b", y))}
            if packets["a"] != packets["b"]:
                different.append((key, "events differ"))
            elif counts["a"] != counts["b"]:
                print("NOTE", key, "the number of packets differs:", counts["a"], "vs", counts["b"])
        elif x != y:
            different.append((key, "tables differ"))
    answered = [key for key in keys if "sha" in (a.get(key) or {})]
    print(f"answers: {len(answered)} http or bus, {len(keys) - len(answered)} table digests and event sets; "
          f"identical bytes {same_bytes}")
    for key, why in different:
        print("DIFF", key, why)
    print("IDENTICAL" if not different else f"DIFFERENT {len(different)}")
    return 0 if not different else 1


def selftest() -> int:
    """The comparer must see one changed byte and one missing answer (a red control), and equal files as equal."""
    folder = tempfile.mkdtemp(prefix="window_json_self_")
    body = base64.b64encode(b'{"a":1}').decode()
    good = {"answers": {"x": {"status": 200, "length": 7, "sha": hashlib.sha256(b'{"a":1}').hexdigest(), "body": body}}}
    bad = {"answers": {"x": {"status": 200, "length": 7, "sha": hashlib.sha256(b'{"a":2}').hexdigest(),
                             "body": base64.b64encode(b'{"a":2}').decode()}}}
    missing = {"answers": {}}
    packet = {"events": {"planner.series.changed": {"count": 2, "distinct": ["a", "b"], "last": "b"}}}
    fewer = {"events": {"planner.series.changed": {"count": 1, "distinct": ["a", "b"], "last": "b"}}}
    other = {"events": {"planner.series.changed": {"count": 2, "distinct": ["a", "c"], "last": "c"}}}
    for name, value in (("good", good), ("bad", bad), ("missing", missing), ("packet", {"answers": {"e": packet}}),
                        ("fewer", {"answers": {"e": fewer}}), ("other", {"answers": {"e": other}})):
        json.dump(value, open(os.path.join(folder, name + ".json"), "w"))
    code_same = compare(os.path.join(folder, "good.json"), os.path.join(folder, "good.json"))
    code_bad = compare(os.path.join(folder, "good.json"), os.path.join(folder, "bad.json"))
    code_missing = compare(os.path.join(folder, "good.json"), os.path.join(folder, "missing.json"))
    code_packets_same = compare(os.path.join(folder, "packet.json"), os.path.join(folder, "fewer.json"))
    code_packets_other = compare(os.path.join(folder, "packet.json"), os.path.join(folder, "other.json"))
    shutil.rmtree(folder)
    ok = (code_same, code_bad, code_missing, code_packets_same, code_packets_other) == (0, 1, 1, 0, 1)
    print("SELFTEST_OK" if ok else "SELFTEST_FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("what", choices=("collect", "compare", "selftest"))
    parser.add_argument("rest", nargs="*")
    parser.add_argument("--root")
    parser.add_argument("--db")
    parser.add_argument("--out")
    args = parser.parse_intermixed_args()
    if args.what == "selftest":
        sys.exit(selftest())
    if args.what == "compare":
        sys.exit(compare(*args.rest))
    root = os.path.abspath(args.root)
    sys.path.insert(0, root)
    os.chdir(root)
    # No request leaves the machine: a stored table is all a count reads.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ[name] = "http://127.0.0.1:9"
    os.environ.pop("NO_PROXY", None)
    os.environ.pop("no_proxy", None)
    asyncio.run(collect(root, args.db, args.out, [int(each) for each in args.rest]))
