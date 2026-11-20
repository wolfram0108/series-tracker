"""The answers of the window of the layout and of the plan of a series over HTTP, from a tree of code, on a copy of the service's
database, no network.

    window_json.py collect --root <tree> --db <copy of app.db> --out <answers.json> [--windows] 46 40
    window_json.py compare <a.json> <b.json>
    window_json.py selftest

`collect` puts the real modules of the path of the data (catalog, settings, rules, sources, voices, composition, planner, unpack,
with a model of `torrents` that answers from the base) and the gateway on one bus over a fresh copy of the base, lets the counts of
the start end, asks the routes of the layout (the whole,
with the excluded, the sources alone, the parts, the queues of several levels, the previews of the sources and of lines) and the
routes of the plan (the view, the references, the queue of the packing, and what the planner tells its neighbours over the bus:
files, seasons, the counts of the downloaded, the state of its work), and then sends the same commands of the levers, asking the
layout and the plan again after each; after them the commands of the plan («Удалить» of a file and of a track, taking it back,
the reset of the failures). It keeps a hash of the tables of the modules after the start and after each command, and the packets
of the events the window is told (`planner.series.changed` and the rest of the planner's, written as the stream of the gateway
writes them). After the series come the modules rules, unpack and voices: the lists, the export and the apply of the profiles over
the bus and the lines of the sources, the check of a profile, a profile of its own made, written to, ordered and deleted, the names of
files; the editor and the table of every torrent page of the base (the first opening of a page whose profiles are gone is one of
them), the commands of the editor; the settings of the voices read, saved as they are, refused and changed; the tables of the three
modules are hashed after the start and after each command. No sync is run: nothing reaches the library or the network. `compare` is byte for byte: to find out whether a
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
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from urllib.parse import quote

TABLES = ("composition_rows", "composition_sources", "composition_state", "unknown_voices", "output_settings",
          "voice_orders", "voice_titles", "plan_assets", "plan_failures", "plan_commands", "plan_runs", "plan_outcomes",
          "plan_standing", "pack_cards")
# The tables the modules rules and unpack own, and the settings of the voices: hashed whole (not by series).
OWNED = ("parser_profiles", "parser_rules", "parser_rule_conditions", "unpack_sources", "unpack_streams", "unpack_folders",
         "unpack_assignments", "unpack_hidden", "unpack_removed")
# What the modules tell the window as events: written as the stream of the gateway writes them (the table SSE_MAP).
WATCHED = ("planner.series.changed", "planner.pack.changed", "planner.journal.changed", "work.series.state",
           "series.status.contribution", "series.downloaded.changed", "rules.profile.changed", "unpack.editor.changed",
           "unpack.table.changed", "voices.settings.changed")


# The clock readings of a read of a page: they differ on every run whatever the code, so their values are not compared.
_CLOCK = re.compile(r'("(?:read_at|fresh_until)":\s*)"[^"]*"')


def masked(text: str) -> str:
    """The text with the values of the clock fields replaced (what is there, and that it is a text, stays compared)."""
    return _CLOCK.sub(r'\1"<time>"', text)


def written(value) -> str:
    """A reply or an event as text: a model as a module writes it for the window, a tuple as a list."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
                      default=lambda each: each.model_dump(mode="json"))


# The torrent page whose five profiles are made again by the first opening of its editor (they are removed from the copy first).
FIRST_OPEN = "https://kinozal.tv/details.php?id=2155888"


def prune_profiles(path: str, url: str) -> None:
    """The copy of the base without the profiles of the tabs of one torrent page, as a page never opened has it."""
    conn = sqlite3.connect(path)
    row = conn.execute("SELECT * FROM unpack_sources WHERE url = ?", (url,)).fetchone()
    names = [column[0] for column in conn.execute("SELECT * FROM unpack_sources LIMIT 0").description]
    ids = [row[i] for i, name in enumerate(names) if name.endswith("_profile_id") and row[i]] if row else []
    conn.execute("DELETE FROM unpack_sources WHERE url = ?", (url,))
    for each in ids:
        conn.execute("DELETE FROM parser_rule_conditions WHERE rule_id IN (SELECT id FROM parser_rules WHERE profile_id = ?)", (each,))
        conn.execute("DELETE FROM parser_rules WHERE profile_id = ?", (each,))
        conn.execute("DELETE FROM parser_profiles WHERE id = ?", (each,))
    conn.commit()
    conn.close()


def tables_digest(path: str, series_id: int) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    for table in TABLES:
        rows = sorted(conn.execute(f"SELECT * FROM {table} WHERE series_id = ?", (series_id,)).fetchall(), key=repr)
        out[table] = {"rows": len(rows), "sha": hashlib.sha256(masked(repr(rows)).encode()).hexdigest()}
    conn.close()
    return out


def owned_digest(path: str) -> dict:
    """The tables of rules and unpack, and the stored settings of the voices, each whole: rows and a hash."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    for table in OWNED:
        rows = sorted(conn.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
        out[table] = {"rows": len(rows), "sha": hashlib.sha256(repr(rows).encode()).hexdigest()}
    rows = sorted(conn.execute("SELECT key, value FROM settings WHERE key LIKE 'voices.%'").fetchall(), key=repr)
    out["settings:voices"] = {"rows": len(rows), "sha": hashlib.sha256(repr(rows).encode()).hexdigest()}
    conn.close()
    return out


async def quiet(bus, planner, modules=(), sources=None) -> None:
    """Returns when nothing is left to do: every envelope is taken apart, no count of the view of a series is under way (the
    planner counts it beside its handlers) and no module works beside its handlers (a read of a page, a recount). The signal is the end of
    the work, not a pause."""
    while True:
        await bus.settled()
        under_way = [task for task in planner._counting.values() if not task.done()]
        under_way += [task for module in modules for task in module.background() if not task.done()]
        if sources is not None:  # the reads of the pages run in tasks of their own
            under_way += [task for task in list(sources.tables._reading.values()) if not task.done()]
        if not under_way:
            return
        await asyncio.gather(*under_way, return_exceptions=True)


async def collect(root: str, base: str, out: str, series: list[int], windows: bool) -> None:
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
    from modules.torrents.repository import ReleaseStore
    from modules.unpack import UnpackModule
    from modules.voices import VoicesModule
    from core.contracts.torrent import ReleaseFile, ReleaseState, ReleaseView
    import httpx

    class Probe(BaseModule):
        name = "probe"

    class Torrents(BaseModule):
        """What `torrents` answers the editor: the releases kept in the base, as `Releases.view` writes them, no client."""
        name = "torrents"

        def __init__(self, bus, db) -> None:
            self.store = ReleaseStore(db)
            super().__init__(bus)

        def register(self) -> None:
            self.handle("torrents.releases.get", self.on_get)

        async def on_get(self, env) -> dict:
            views = []
            for row in await self.store.of_page(env.payload["url"]):
                files = [ReleaseFile(**each) for each in await self.store.files(row["id"])]
                views.append(ReleaseView(url=row["url"], release=row["release"], title=row["title"], published=row["published"],
                                         infohash=row["infohash"], state=ReleaseState(row["state"]), reason=row["reason"],
                                         watched=bool(row["watched"]), folder=row["folder"], files=files,
                                         checked_at=row["checked_at"]).model_dump(mode="json"))
            return {"releases": views}

    folder = tempfile.mkdtemp(prefix="window_json_")
    path = os.path.join(folder, "app.db")
    shutil.copy(base, path)
    prune_profiles(path, FIRST_OPEN)
    db = Database(path)
    bus = Bus()
    composition = CompositionModule(bus, db)
    planner = PlannerModule(bus, db)
    probe = Probe(bus)
    gateway = GatewayModule(bus, static_dir=folder, templates_dir=folder, auth_required=False)
    # `unpack` starts last, as it does in the service: its start tells every page it keeps, and the sources read them again.
    reader = SourcesModule(bus, db, torrent_cache_dir=os.path.join(folder, "torrent_cache"))
    modules = [CatalogModule(bus, db), SettingsModule(bus, db), RulesModule(bus, db), reader, VoicesModule(bus, db),
               composition, planner, probe, gateway, Torrents(bus, db), UnpackModule(bus, db)]
    # The packets of the events the window is told, as the stream of the gateway writes them (SSE_MAP, json.dumps).
    events: list[tuple[str, str]] = []

    def seen(env) -> None:
        if env.topic in WATCHED:
            sse = SSE_MAP.get(env.topic)
            payload = sse.form(env.payload) if sse is not None and sse.form else env.payload
            events.append((env.topic, masked(json.dumps(payload, ensure_ascii=False))))

    untap = bus.tap(seen)
    runner = Runner(bus, modules)
    await runner.start()
    await composition.idle()
    await quiet(bus, planner, modules, reader)
    answers: dict[str, dict] = {}

    def record(name: str, status: int, body: bytes) -> None:
        body = masked(body.decode("utf-8")).encode("utf-8")
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
        await quiet(bus, planner, modules, reader)
        await ask(client, f"{label}:plan", "GET", f"/api/series/{sid}/plan")
        await ask(client, f"{label}:output", "GET", f"/api/series/{sid}/output")
        told(label)

    told("start")
    answers["owned:start"] = owned_digest(path)
    samples: list[tuple[int, int | None, list[str]]] = []
    used: set[int] = set()
    cards: dict[int, dict] = {}

    async def after(label: str) -> None:
        """What a command left: the work it began has ended, then the tables of the three modules and the packets told."""
        await composition.idle()
        await quiet(bus, planner, modules, reader)
        answers[f"{label}:owned"] = owned_digest(path)
        told(label)

    async def rules_window(client) -> None:
        """The profiles and rules: lists, export, apply on the lines of the sources, a profile of its own written to, the names."""
        await ask(client, "rules:profiles", "GET", "/api/parser-profiles")
        await over_bus("rules:profiles:default", "rules.profiles.list", {})
        await over_bus("rules:profiles:torrent", "rules.profiles.list", {"kind": "torrent"})
        torrent = [each["id"] for each in await probe.request("rules.profiles.list", {"kind": "torrent"})]
        for profile in sorted(used) + torrent[:6]:
            await ask(client, f"rules:rules:{profile}", "GET", f"/api/parser-profiles/{profile}/rules")
        await over_bus("rules:rules:unknown", "rules.rules.list", {"profile_id": 999999})
        await over_bus("rules:export", "rules.profiles.export", {"profile_ids": sorted(used)})
        await over_bus("rules:export:torrent", "rules.profiles.export", {"profile_ids": torrent[:6]})
        await over_bus("rules:export:unknown", "rules.profiles.export", {"profile_ids": [999999]})
        for source_id, profile, lines in samples:
            if not profile:
                continue
            body = [{"key": f"k{each}", "text": text} for each, text in enumerate(lines)]
            await over_bus(f"rules:apply:{source_id}", "rules.apply", {"profile_id": profile, "lines": body, "series_season": "1"})
            await over_bus(f"rules:apply:{source_id}:no-season", "rules.apply", {"profile_id": profile, "lines": body})
            kept = (await probe.request("rules.profiles.export", {"profile_ids": [profile]}))["profiles"][profile]
            await over_bus(f"rules:apply:{source_id}:snapshot", "rules.apply",
                           {"profile_id": profile, "lines": body, "series_season": "2", "rules": kept})
            await over_bus(f"rules:apply:{source_id}:keyed", "rules.apply", {"profile_id": profile, "series_season": "1", "lines": [
                {"key": f"k{each}", "text": text, "player": "kodik", "voice": "Студия"} for each, text in enumerate(lines[:20])]})
        if samples and samples[0][1]:
            profile, line = samples[0][1], samples[0][2][0]
            await over_bus("rules:apply:repeated-keys", "rules.apply", {"profile_id": profile, "lines": [
                {"key": "same", "text": line}, {"key": "same", "text": line}]})
            await over_bus("rules:apply:empty", "rules.apply", {"profile_id": profile, "lines": []})
        await over_bus("rules:apply:unknown-profile", "rules.apply", {"profile_id": 999999, "lines": [{"key": "a", "text": "x"}]})

        # A profile of its own: made, written to, ordered, applied, renamed and deleted; the tables are hashed after each.
        def rule(name: str, text: str, number: str) -> dict:
            return {"name": name, "continue_after_match": False,
                    "conditions": [{"condition_type": "contains", "logical_operator": "AND",
                                    "pattern": json.dumps([{"type": "text", "value": text}])}],
                    "action_pattern": json.dumps([{"action_type": "extract_single", "action_pattern": json.dumps(
                        [{"type": "text", "value": number}, {"type": "number"}])}])}

        made = await ask(client, "rules:cmd:create", "POST", "/api/parser-profiles", json={"name": "прибор: профиль"})
        profile = made.json().get("id")
        await after("rules:cmd:create")
        await ask(client, "rules:cmd:create:no-name", "POST", "/api/parser-profiles", json={})
        first = await ask(client, "rules:cmd:rule-add", "POST", f"/api/parser-profiles/{profile}/rules",
                          json=rule("первое", "сер", "серия "))
        await after("rules:cmd:rule-add")
        second = await ask(client, "rules:cmd:rule-add-2", "POST", f"/api/parser-profiles/{profile}/rules",
                           json=rule("второе", "ep", "ep "))
        await after("rules:cmd:rule-add-2")
        await ask(client, "rules:cmd:rule-add:invalid", "POST", f"/api/parser-profiles/{profile}/rules",
                  json={"name": "битое", "action_pattern": "не json", "conditions": []})
        await ask(client, "rules:cmd:rule-add:unknown-profile", "POST", "/api/parser-profiles/999999/rules",
                  json=rule("а", "б", "в"))
        await after("rules:cmd:rule-add:refused")
        ids = [first.json().get("id"), second.json().get("id")]
        await ask(client, "rules:cmd:rules", "GET", f"/api/parser-profiles/{profile}/rules")
        lines = ["серия 3", "ep 4", "Серия 5 озвучка", "без номера"]
        body = {"profile_id": profile, "lines": [{"key": f"k{each}", "text": text} for each, text in enumerate(lines)],
                "series_season": "1"}
        await over_bus("rules:cmd:apply", "rules.apply", body)
        await ask(client, "rules:cmd:rule-update", "PUT", f"/api/parser-rules/{ids[0]}", json=rule("первое-новое", "сер", "серия "))
        await ask(client, "rules:cmd:rule-update:invalid", "PUT", f"/api/parser-rules/{ids[0]}",
                  json={"name": "x", "action_pattern": "нет", "conditions": []})
        await after("rules:cmd:rule-update")
        await ask(client, "rules:cmd:reorder", "POST", "/api/parser-rules/reorder", json=list(reversed(ids)))
        await after("rules:cmd:reorder")
        await over_bus("rules:cmd:apply:reordered", "rules.apply", body)
        await over_bus("rules:cmd:export", "rules.profiles.export", {"profile_ids": [profile]})
        await ask(client, "rules:cmd:profile-rename", "PUT", f"/api/parser-profiles/{profile}", json={"name": "прибор: новое имя"})
        await ask(client, "rules:cmd:profile-rename:unknown", "PUT", "/api/parser-profiles/999999", json={"name": "а"})
        await after("rules:cmd:profile-rename")
        await ask(client, "rules:cmd:rule-delete", "DELETE", f"/api/parser-rules/{ids[1]}")
        await after("rules:cmd:rule-delete")
        await over_bus("rules:cmd:apply:after-delete", "rules.apply", body)
        if used:
            await ask(client, "rules:cmd:profile-delete:in-use", "DELETE", f"/api/parser-profiles/{sorted(used)[0]}")
            await after("rules:cmd:profile-delete:in-use")
        await ask(client, "rules:cmd:profile-delete", "DELETE", f"/api/parser-profiles/{profile}")
        await ask(client, "rules:cmd:profile-delete:again", "DELETE", f"/api/parser-profiles/{profile}")
        await after("rules:cmd:profile-delete")
        await over_bus("rules:cmd:apply:deleted", "rules.apply", body)
        # The names of files: the numbers are ready, no rule is applied; and the name of a torrent file by a profile.
        items = [{"season": 1, "episode_start": 5, "episode_end": None, "part": None, "resolution": "1080", "voiceover_tag": None},
                 {"season": 1, "episode_start": 3, "episode_end": 5, "part": 2, "resolution": "1080", "voiceover_tag": "Дубляж"},
                 {"season": None, "episode_start": 7, "episode_end": 7, "part": None, "resolution": None,
                  "voiceover_tag": "Студия / Дубляж"},
                 {"season": 2, "episode_start": 12, "episode_end": None, "part": None, "resolution": "720", "voiceover_tag": None,
                  "cut": json.dumps({"height": 480, "voice_group": "Группа"})}]
        extras = [{}, {"episode_override": 4}, {"original_filename": "Season 01/old.mp4"}]
        for sid, card in cards.items():
            for number, item in enumerate(items):
                for kind, extra in enumerate(extras):
                    await over_bus(f"rules:name:{sid}:{number}:{kind}", "rules.format_filename",
                                   {"series": card, "media_item": item, **extra})
            for each in [*torrent[:3], *sorted(used)[:2]]:
                for basename in ("Show.S01E03.1080p.mkv", "[Group] Show - 07 [1080p].mkv", "Specials.mkv", "Show E12 WEB-DL 720p.mkv"):
                    await over_bus(f"rules:torrent-name:{sid}:{each}:{basename}", "rules.format_torrent_file", {
                        "series": card, "profile_id": each, "file_basename": basename, "original_path": f"Show/{basename}"})
            await over_bus(f"rules:torrent-name:{sid}:no-profile", "rules.format_torrent_file", {
                "series": card, "file_basename": "a.mkv", "original_path": "Show/a.mkv"})
            await over_bus(f"rules:torrent-name:{sid}:unknown-profile", "rules.format_torrent_file", {
                "series": card, "profile_id": 999999, "file_basename": "a.mkv", "original_path": "Show/a.mkv"})

    async def unpack_window(client) -> None:
        """The editor and the table of every torrent page of the base, then the commands of the editor on one of them."""
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        pages = sorted({row[0] for row in conn.execute("SELECT url FROM torrent_releases")})
        conn.close()
        # The first opening of a page whose profiles are gone: the five are made, then the same again changes nothing.
        await ask(client, "unpack:first-open", "POST", "/api/torrent/editor", json={"url": FIRST_OPEN})
        await after("unpack:first-open")
        await ask(client, "unpack:first-open:again", "POST", "/api/torrent/editor", json={"url": FIRST_OPEN})
        await after("unpack:first-open:again")
        await over_bus("unpack:first-open:profiles", "rules.profiles.list", {"kind": "torrent"})
        for page in pages:
            await over_bus(f"unpack:{page}:table", "unpack.table.get", {"url": page})
            await over_bus(f"unpack:{page}:editor", "unpack.editor.get", {"url": page})
            await ask(client, f"unpack:{page}:http", "POST", "/api/torrent/editor", json={"url": page})
        await over_bus("unpack:unknown:table", "unpack.table.get", {"url": "https://nowhere.test/p"})
        await ask(client, "unpack:unknown:http", "POST", "/api/torrent/editor", json={"url": "https://nowhere.test/p"})
        page = max(pages, key=lambda each: each.endswith("6878393"))
        view = (await probe.request("unpack.editor.get", {"url": page}))["view"]
        tabs = view["tabs"]

        def pick(tab: str, count: int = 1, kind: str | None = None) -> list[str]:
            return [each["item"] for each in tabs.get(tab, []) if kind is None or each.get("kind") == kind][:count]

        videos, voices, subs = pick("files", 4, "video"), pick("voices", 2), pick("subtitles", 2)
        streams, sub_streams = pick("voice_streams", 2), pick("subtitle_streams", 2)

        async def command(label: str, route: str, body: dict) -> None:
            await ask(client, f"unpack:cmd:{label}", "POST", f"/api/torrent/editor{route}", json={"url": page, **body})
            await after(f"unpack:cmd:{label}")
            await over_bus(f"unpack:cmd:{label}:table", "unpack.table.get", {"url": page})
            await ask(client, f"unpack:cmd:{label}:editor", "POST", "/api/torrent/editor", json={"url": page})

        if videos:
            await command("assign-file", "/assign", {"tab": "files", "items": videos[:1], "season": 1, "episode": 90})
            await command("assign-in-order", "/assign",
                          {"tab": "files", "items": videos[1:3], "season": 1, "episode": 91, "in_order": True})
            await command("assign-tag", "/assign",
                          {"tab": "files", "items": videos[3:4] or videos[:1], "season": 1, "episode": 5, "tag": "DIR"})
            await command("unassign-file", "/unassign", {"tab": "files", "items": videos[:1]})
        await command("assign-bad", "/assign", {"tab": "files", "items": ["x"], "season": 1})
        await command("folder-voices", "/folder", {"tab": "voices", "name": "Студия", "language": "rus"})
        await command("folder-subtitles", "/folder",
                      {"tab": "subtitles", "name": "Надписи", "language": "eng", "subtitle_type": "signs"})
        await command("folder-streams", "/folder", {"tab": "voice_streams", "name": "Поток", "language": "jpn"})
        for tab, items, folder in (("voices", voices, "Студия"), ("subtitles", subs, "Надписи"), ("voice_streams", streams, "Поток"),
                                   ("subtitle_streams", sub_streams, "Надписи")):
            if items:
                await command(f"assign-track:{tab}", "/assign", {"tab": tab, "items": items[:1], "folder": folder})
                await command(f"remove:{tab}", "/remove", {"tab": tab, "items": items[:1]})
        await command("folder-delete", "/folder/delete", {"tab": "voices", "name": "Студия", "automatic": True, "items": voices[:1]})
        await command("folder-delete-manual", "/folder/delete", {"tab": "subtitles", "name": "Надписи"})
        await command("auto", "/auto", {"tabs": ["voices", "subtitles", "voice_streams", "subtitle_streams"]})

    async def voices_window(client) -> None:
        """The settings of the voices: read, saved as they are, refused, changed; the layout of a series after the change."""
        await ask(client, "voices:settings", "GET", "/api/voices/settings")
        await ask(client, "voices:players", "GET", "/api/voices/players")
        for topic in ("voices.settings.get", "voices.settings.export", "voices.players.get"):
            await over_bus(f"voices:bus:{topic}", topic, {})
        view = (await client.get("/api/voices/settings")).json()
        groups = view["voice_groups"]
        names = [*groups, *(each for triggers in groups.values() for each in triggers), "Неизвестный голос", "Студия / Дубляж", ""]
        await over_bus("voices:groups", "voices.groups.resolve", {"names": names})
        for key, value in (("voices.groups", json.dumps({"Группа": ["один", "два"]})), ("voices.groups", "{сломано"),
                           ("voices.source_priority", json.dumps(["vk", "kodik"])), ("voices.source_priority", "[]"),
                           ("voices.languages", json.dumps({"Группа": "xx"})), ("voices.players_off", "нет"),
                           ("voices.cut_max_height", "-1"), ("voices.player_groups", json.dumps({"Плеер": ["vk", "cvh"]}))):
            await over_bus(f"voices:values:{key}:{value}", "voices.settings.get", {"values": {key: value}})
            await over_bus(f"voices:resolve:{key}:{value}", "voices.groups.resolve", {"names": names, "values": {key: value}})
        draft = {key: view[key] for key in ("priority", "players_off", "voice_groups", "voice_languages")}
        answers["voices:owned:start"] = owned_digest(path)

        async def save(label: str, body: dict) -> None:
            await ask(client, f"voices:cmd:{label}", "PUT", "/api/voices/settings", json=body)
            await after(f"voices:cmd:{label}")
            await ask(client, f"voices:cmd:{label}:settings", "GET", "/api/voices/settings")
            await over_bus(f"voices:cmd:{label}:export", "voices.settings.export", {})

        await save("as-they-are", draft)
        await save("empty-order", {**draft, "priority": []})
        await save("unknown-language", {**draft, "voice_languages": {**draft["voice_languages"], "Группа": "xx"},
                                        "voice_groups": {**draft["voice_groups"], "Группа": ["один"]}})
        await save("group-clash", {**draft, "voice_groups": {"А": ["общий"], "Б": ["общий"], "В": []}})
        await save("unknown-player", {**draft, "priority": [*draft["priority"], "нет-такого-плеера"]})
        await save("bad-body", {"priority": "x"})
        changed = [*reversed(draft["priority"][:2]), *draft["priority"][2:]]
        await save("order", {**draft, "priority": changed})
        await save("group", {**draft, "priority": changed,
                             "voice_groups": {**draft["voice_groups"], "Прибор": ["прибор-один", " прибор-два "]},
                             "voice_languages": {**draft["voice_languages"], "Прибор": "rus"}})
        await save("group-back", {**draft, "priority": changed})
        await save("order-back", draft)
        if cards:
            sid = next(iter(cards))
            await ask(client, f"voices:composition:{sid}", "GET", f"/api/series/{sid}/composition")
            await plan_window(client, f"voices:{sid}", sid)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway.app), base_url="http://test",
                                 timeout=120) as client:
        for sid in series:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            sources = conn.execute("SELECT id, url, parser_profile_id FROM series_sources WHERE series_id=? ORDER BY id",
                                   (sid,)).fetchall()
            conn.close()
            answers[f"{sid}:tables:start"] = tables_digest(path, sid)
            cards[sid] = await probe.request("catalog.series.get", {"series_id": sid})
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
                if reply.status_code == 200 and reply.json().get("lines"):
                    samples.append((source_id, profile, [each["line"] for each in reply.json()["lines"][:40]]))
                if profile:
                    used.add(profile)
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
        if windows:
            await rules_window(client)
            await unpack_window(client)
            await voices_window(client)
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
    # The windows of rules, unpack and voices ask what the series before them left (profiles, lines, cards): put them in the run of
    # the series that has what they need; a run is kept under 180 s.
    parser.add_argument("--windows", action="store_true")
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
    asyncio.run(collect(root, args.db, args.out, [int(each) for each in args.rest], args.windows))
