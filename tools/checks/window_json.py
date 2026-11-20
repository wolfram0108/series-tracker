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
modules are hashed after the start and after each command. With `--owners` the owners of the data answer too (no series is
needed: `collect --owners` alone is a run of its own): the catalog (the list and every card of the base, the sources of every
series and of every profile over HTTP and over the bus, a series of the instrument made, changed in every field the window changes,
refused, busy, watched, given a source, changed and cleared of sources, deleted with its cascade), the settings (read and written
by key and by prefix, the paths, a secret) and auth (the login right and wrong, the session, the password changed, the lock of too
many attempts, an outdated hash renewed by a login), with the tables `series`, `series_sources`, `settings`, `saved_paths` and
`admin_user` hashed after each command and the events of the three told as the stream writes them. With `--sources` (a run of its
own too) the windows of `sources`, `trackerauth` and `metadata` answer: the stored tables of every page of the base, the address of a
page and its site, the lists of the players and of the trackers, the settings of the sources (the mirrors) and the terms of the sites,
the provider of the socket as the model of the specification (tests/provider_mock.py) plays it at an address it is pointed at (its
picture, the search of titles, the read, the stop of a read, the check of pages, the stream and the download of a link, the provider
down and back, cleared), the credentials of the trackers (synthetic ones, written by the run: no secret of the base is read or shown), the fetch of a
page through them over a transport that is a model of the trackers, the releases of a tracker page, the files of the releases, and the
mappings of TMDB over a model of TMDB; the tables of the three are hashed after each command. No sync is run: nothing reaches the library or the network. `compare` is byte for byte: to find out whether a
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
from datetime import datetime, timezone
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
           "unpack.table.changed", "voices.settings.changed", "series.added", "series.updated", "series.deleted",
           "series.status.changed", "series.busy.changed", "series.source.added", "series.source.updated",
           "series.source.removed", "settings.changed")
# The tables of the owners of the data: the series and its sources, the settings, the saved paths, the dead table of statuses.
OWNERS = ("series", "series_sources", "series_statuses", "saved_paths")


# What the modules tell the window as events in the sources run (the table SSE_MAP names them).
WATCHED_SOURCES = ("sources.table.progress", "sources.table.ready", "sources.table.fresh", "sources.table.failed",
                   "sources.settings.changed", "sources.provider.changed", "sources.video.progress", "sources.provider.state",
                   "sources.provider.manifest", "sources.pages.forgotten", "trackerauth.credentials.changed")
# The tables the three modules own, and the settings: hashed whole after each command of their run.
SOURCES_OWNED = ("release_tables", "release_rows", "trackers", "provider_manifest", "series_tmdb_mappings")
# What differs on every run whatever the code (the number of a picture, the seconds of a download, the port of the model of the
# provider, the folder of the run, the address of an object): filled by a run with `--sources`.
_RUN_MASKS: list[tuple[re.Pattern, str]] = []
# The clock readings of a read of a page: they differ on every run whatever the code, so their values are not compared.
_CLOCK = re.compile(r'("(?:read_at|fresh_until)":\s*)"[^"]*"')
# The stamp the base keeps (and the window shows) of a moment: `last_scan_time` after a touch.
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\.\d{6}")


def masked(text: str) -> str:
    """The text with the values of the clock fields replaced (what is there, and that it is a text, stays compared)."""
    text = _STAMP.sub("<stamp>", _CLOCK.sub(r'\1"<time>"', text))
    for pattern, replacement in _RUN_MASKS:
        text = pattern.sub(replacement, text)
    return text


def plain(value):
    """A reply as the plain data it is told as: a model of the new tree and a dictionary of the old one are the same here."""
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def written(value, drop_none: bool = False) -> str:
    """A reply or an event as text: a model as a module writes it for the window, a tuple as a list. `drop_none` leaves out the fields
    that are none: a reply a module keeps for the modules (not for a window) may be a model that names the fields of both its forms."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
                      default=lambda each: each.model_dump(mode="json"))
    if not drop_none:
        return text

    def clean(each):
        if isinstance(each, dict):
            return {name: clean(part) for name, part in each.items() if part is not None}
        return [clean(part) for part in each] if isinstance(each, list) else each

    return json.dumps(clean(json.loads(text)), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


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


def owners_digest(path: str) -> dict:
    """The tables of the catalog, of the settings and of auth, each whole: rows and a hash. A secret setting is hashed as the
    service reads it (a stored one differs on every write: the cipher takes a new nonce), a clock stamp as `<stamp>`, and the
    hash of the password by the parameters it was made with (its salt differs on every write)."""
    from core import crypto
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    for table in OWNERS:
        rows = sorted(conn.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
        out[table] = {"rows": len(rows), "sha": hashlib.sha256(masked(repr(rows)).encode()).hexdigest()}
    rows = sorted(((key, crypto.decrypt(value)) for key, value in conn.execute("SELECT key, value FROM settings")), key=repr)
    out["settings"] = {"rows": len(rows), "sha": hashlib.sha256(repr(rows).encode()).hexdigest()}
    rows = [(name, "$".join(each.split("$")[:4]), each.count("$")) for name, each in
            conn.execute("SELECT username, password_hash FROM admin_user")]
    out["admin_user"] = {"rows": len(rows), "sha": hashlib.sha256(repr(rows).encode()).hexdigest(), "made_with": rows}
    conn.close()
    return out


def sources_digest(path: str) -> dict:
    """The tables of `sources`, `trackerauth` and `metadata`, each whole: rows and a hash. What a cipher stores is hashed as the
    service reads it (a stored secret differs on every write: the cipher takes a new nonce), a clock stamp as `<stamp>`."""
    from core import crypto
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    for table in SOURCES_OWNED:
        if table == "release_rows":
            # The rows of every page: tens of thousands, taken in the order of the table as they come, one hash (no clock in them).
            digest, count = hashlib.sha256(), 0
            for row in conn.execute("SELECT * FROM release_rows ORDER BY url, seq"):
                digest.update(repr(row).encode())
                count += 1
            out[table] = {"rows": count, "sha": digest.hexdigest()}
            continue
        rows = sorted(conn.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
        out[table] = {"rows": len(rows), "sha": hashlib.sha256(masked(repr(rows)).encode()).hexdigest()}
    rows = sorted(((service, domain, crypto.decrypt(cookies), login, updated, agent, fingerprint) for
                   service, domain, cookies, login, updated, agent, fingerprint in conn.execute(
                       "SELECT service, domain, cookies_json, last_login_at, updated_at, user_agent, account_fingerprint "
                       "FROM tracker_sessions")), key=repr)
    out["tracker_sessions"] = {"rows": len(rows), "sha": hashlib.sha256(masked(repr(rows)).encode()).hexdigest()}
    rows = sorted(((kind, name, crypto.decrypt(secret), url) for kind, name, secret, url in conn.execute(
        "SELECT auth_type, username, password, url FROM auth")), key=repr)
    out["auth"] = {"rows": len(rows), "sha": hashlib.sha256(repr(rows).encode()).hexdigest()}
    rows = sorted(((key, crypto.decrypt(value)) for key, value in conn.execute("SELECT key, value FROM settings")), key=repr)
    out["settings"] = {"rows": len(rows), "sha": hashlib.sha256(masked(repr(rows)).encode()).hexdigest()}
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


async def collect(root: str, base: str, out: str, series: list[int], windows: bool, owners: bool = False,
                  sources_run: bool = False) -> None:
    from core.bus import Bus
    from core.db import Database
    from core.module import BaseModule, BusRequestError
    from core.runner import Runner
    from modules.auth import AuthModule
    from modules.catalog import CatalogModule
    from modules.composition import CompositionModule
    from modules.gateway import GatewayModule
    from modules.gateway.module import SSE_MAP
    from modules.metadata import MetadataModule
    from modules.planner import PlannerModule
    from modules.rules import RulesModule
    from modules.settings import SettingsModule
    from modules.sources import SourcesModule
    from modules.torrents.repository import ReleaseStore
    from modules.trackerauth import TrackerauthModule
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

    class Neighbours(BaseModule):
        """What the owners that are not on the bus answer and tell the owners of the data."""
        name = "neighbours"

        def register(self) -> None:
            self.handle("library.relocation.get", self.on_planned)
            self.handle("series.deleted", self.on_deleted)

        async def on_planned(self, env) -> dict:
            return {"new_path": None}

        async def on_deleted(self, env) -> None:
            for owner in ("scan", "torrents", "library"):
                self.publish_event("series.purged", {"series_id": env.payload["series_id"], "owner": owner})

    folder = tempfile.mkdtemp(prefix="window_json_")
    path = os.path.join(folder, "app.db")
    shutil.copy(base, path)
    prune_profiles(path, FIRST_OPEN)
    if sources_run:
        # The credentials, the token of TMDB and the address of the provider of the base are not used: the run writes its own.
        conn = sqlite3.connect(path)
        conn.execute("DELETE FROM auth")
        conn.execute("DELETE FROM tracker_sessions")
        conn.execute("DELETE FROM settings WHERE key IN ('tmdb_token', 'provider.url', 'provider.token')")
        conn.commit()
        conn.close()
        _RUN_MASKS[:] = [
            (re.compile(r'("seq":\s*)\d+'), r"\1<n>"),
            (re.compile(r'((?:\\)?"(?:seconds|longest)(?:\\)?":\s*)[0-9.]+'), r"\1<n>"),
            (re.compile(r"127\.0\.0\.1:\d+"), "127.0.0.1:<port>"),
            # The pace of a read (steps a second since its phase began) is the speed of the machine.
            (re.compile(r'("rate":\s*)(?:[0-9.]+|null)'), r"\1<rate>"),
            # The cause a provider that went down is told with is the one its last attempt met (a closed feed, then a refused
            # connection): which of them is seen at the moment of a read depends on the pace of the retries, which is no part of the answer.
            (re.compile(r'(provider_unavailable\\*",\\*"cause\\*":\\*")(?:(?!\\*"\}).)*'), r"\1<cause>"),
            (re.compile(r"0x[0-9a-fA-F]{6,}"), "0x<address>"),
            (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"), "<stamp>"),
            (re.compile(re.escape(folder)), "<tmp>")]
    db = Database(path)
    bus = Bus()
    composition = CompositionModule(bus, db)
    planner = PlannerModule(bus, db)
    probe = Probe(bus)
    gateway = GatewayModule(bus, static_dir=folder, templates_dir=folder, auth_required=False, cookie_secure=False)
    # `unpack` starts last, as it does in the service: its start tells every page it keeps, and the sources read them again.
    # In the sources run the clock of the tables is the run's own (a page is fresh or stale as the run says) and the feed of the provider
    # is opened again at once: nothing there waits for a time.
    clock = {"now": datetime(2026, 11, 20, 13, 30, tzinfo=timezone.utc)}
    reader = SourcesModule(bus, db, torrent_cache_dir=os.path.join(folder, "torrent_cache"),
                           **({"table_clock": lambda: clock["now"], "provider_reconnect": (0.05, 0.2)} if sources_run else {}))
    modules = [CatalogModule(bus, db), SettingsModule(bus, db), RulesModule(bus, db), reader, VoicesModule(bus, db),
               composition, planner, probe, gateway, Torrents(bus, db), UnpackModule(bus, db)]
    metadata = MetadataModule(bus, db)
    if owners:
        # The neighbours of the owners: the mapping of TMDB read from the base (the list of series asks it), the folder a save
        # planned (none), and the reports of the owners that are not on this bus that their data of a deleted series is gone.
        modules += [metadata, AuthModule(bus, db), Neighbours(bus)]
    if sources_run:
        modules += [metadata, TrackerauthModule(bus, db), Neighbours(bus)]
    # The packets of the events the window is told, as the stream of the gateway writes them (SSE_MAP, json.dumps).
    events: list[tuple[str, str]] = []

    def seen(env) -> None:
        if env.topic in WATCHED or (sources_run and env.topic in WATCHED_SOURCES):
            sse = SSE_MAP.get(env.topic)
            payload = sse.form(env.payload) if sse is not None and sse.form else env.payload
            events.append((env.topic, masked(json.dumps(payload, ensure_ascii=False, default=plain))))

    untap = bus.tap(seen)
    runner = Runner(bus, modules)
    await runner.start()
    await composition.idle()
    await quiet(bus, planner, modules, reader)
    answers: dict[str, dict] = {}

    def record(name: str, status: int, body: bytes) -> None:
        body = masked(body.decode("utf-8")).encode("utf-8")
        # The whole body is hashed; the first 64 KB are kept to find where two answers part (a table of a page is megabytes).
        answers[name] = {"status": status, "length": len(body), "sha": hashlib.sha256(body).hexdigest(),
                         "body": base64.b64encode(body[:65536]).decode()}

    async def ask(client, name, method, url, **kwargs):
        response = await client.request(method, url, **kwargs)
        record(name, response.status_code, response.content)
        return response

    async def over_bus(name: str, topic: str, payload: dict, drop_none: bool = False) -> None:
        """What a module answers another: the model or the dictionary, written as text; a refusal as its words."""
        try:
            record(name, 200, written(await probe.request(topic, payload), drop_none).encode())
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

    async def owners_window(client) -> None:
        """The owners of the data: the catalog (read, a series of the instrument made, changed, refused, busy, watched, given
        sources, deleted), the settings (read, written, the paths, a secret) and auth (login, session, password, lock)."""
        from argon2 import PasswordHasher
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        ids = [row[0] for row in conn.execute("SELECT id FROM series ORDER BY id")]
        profiles = [row[0] for row in conn.execute(
            "SELECT DISTINCT parser_profile_id FROM series_sources WHERE parser_profile_id IS NOT NULL ORDER BY 1")]
        conn.close()
        profile = profiles[0] if profiles else None

        async def settled(label: str) -> None:
            """What a command left: the work it began has ended, then the tables of the owners and the packets told."""
            await composition.idle()
            await quiet(bus, planner, modules, reader)
            answers[f"{label}:owners"] = owners_digest(path)
            told(label)

        async def listed(name: str, topic: str, payload: dict, field: str) -> None:
            """A list over the bus: the new tree answers a model that holds the list, the old one the list itself."""
            try:
                reply = await probe.request(topic, payload)
                record(name, 200, written(getattr(reply, field, reply)).encode())
            except BusRequestError as exc:
                record(name, 500, str(exc).encode())

        async def status_of(name: str, method: str, url: str, **kwargs) -> None:
            """Only the status: a body that holds a moment of the clock or of the lock is no part of the comparison."""
            record(name, (await client.request(method, url, **kwargs)).status_code, b"")

        answers["owners:start"] = owners_digest(path)
        told("owners:start")

        # --- the catalog: the list, every card and the sources of every series, over HTTP and over the bus --------------
        await ask(client, "owners:list", "GET", "/api/series")
        await listed("owners:bus:list", "catalog.series.list", {}, "series")
        for each in ids:
            await ask(client, f"owners:card:{each}", "GET", f"/api/series/{each}")
            await over_bus(f"owners:bus:get:{each}", "catalog.series.get", {"series_id": each})
            await ask(client, f"owners:sources:{each}", "GET", f"/api/series/{each}/sources")
            await listed(f"owners:bus:sources:{each}", "catalog.sources.list", {"series_id": each}, "sources")
        await ask(client, "owners:card:unknown", "GET", "/api/series/999999")
        await ask(client, "owners:sources:unknown", "GET", "/api/series/999999/sources")
        await over_bus("owners:bus:get:unknown", "catalog.series.get", {"series_id": 999999})
        await listed("owners:bus:sources:unknown", "catalog.sources.list", {"series_id": 999999}, "sources")
        for each in [*profiles, 999999]:
            await listed(f"owners:bus:by-profile:{each}", "catalog.sources.by_profile", {"profile_id": each}, "sources")
        told("owners:read")

        # --- a series of the instrument: made with two sources, refused in the ways a series is refused ------------------
        draft = {"name": "Прибор", "name_en": "Proto", "site": "kinozal", "save_path": "/media/proto", "season": "1",
                 "quality": "1080p", "auto_scan_enabled": False,
                 "sources": [{"url": "https://proto.invalid/a", "source_type": "web_video", "parser_profile_id": profile,
                              "players_off": ["kodik", "kodik", "vk"], "rows_per_line": " 10 "},
                             {"url": "https://proto.invalid/b", "source_type": "torrent", "vibix_season": 2}]}
        made = await ask(client, "owners:create", "POST", "/api/series", json=draft)
        sid = made.json().get("series_id")
        await settled("owners:create")
        await ask(client, "owners:create:card", "GET", f"/api/series/{sid}")
        await ask(client, "owners:create:list", "GET", "/api/series")
        base_data = {"name": "Отказ", "name_en": "Refused", "site": "kinozal", "save_path": "/media/refused"}
        page = {"url": "https://proto.invalid/r", "source_type": "web_video", "parser_profile_id": None}
        for label, data in (("no-sources", base_data), ("empty-sources", {**base_data, "sources": []}),
                            ("removed-field", {**base_data, "url": "x", "sources": [page]}),
                            ("duplicate-source", {**base_data, "sources": [page, page]}),
                            ("unknown-profile", {**base_data, "sources": [{**page, "parser_profile_id": 999999}]}),
                            ("relative-path", {**base_data, "save_path": "media/x", "sources": [page]}),
                            ("source-not-dict", {**base_data, "sources": ["x"]}),
                            ("source-field-unknown", {**base_data, "sources": [{**page, "color": 3}]}),
                            ("empty-url", {**base_data, "sources": [{**page, "url": " "}]}),
                            ("rows-invalid", {**base_data, "sources": [{**page, "rows_per_line": "0"}]}),
                            ("vibix-zero", {**base_data, "sources": [{**page, "vibix_season": 0}]}),
                            ("type-unknown", {**base_data, "sources": [{**page, "source_type": "nope"}]})):
            await over_bus(f"owners:create:refused:{label}", "catalog.series.create", {"data": data})
        await ask(client, "owners:create:refused:http-no-sources", "POST", "/api/series", json=base_data)
        await ask(client, "owners:create:refused:http-removed-field", "POST", "/api/series",
                  json={**base_data, "url": "x", "sources": [page]})
        await settled("owners:create:refused")
        # What the owner normalises on the way in: the names that become paths, the path itself.
        second = plain(await probe.request("catalog.series.create", {"data": {
            **base_data, "name_en": "a:b/c?", "quality_override": "x|y", "save_path": "/media/a:b/ c",
            "sources": [{**page, "url": "https://proto.invalid/s"}]}}))["id"]
        await settled("owners:create:sanitized")
        await over_bus("owners:create:sanitized:card", "catalog.series.get", {"series_id": second})

        # --- the change of the series: every field the window changes, and what it may not change -------------------------
        card_url = f"/api/series/{sid}"
        for label, change in (("name", {"name": "Прибор 2"}), ("season", {"season": "2", "quality": "720p"}),
                              ("overrides", {"quality_override": "a:b", "resolution_override": "720"}),
                              ("empty", {}), ("same", {"name": "Прибор 2"}), ("bad-field", {"id": 5})):
            await ask(client, f"owners:update:{label}", "POST", card_url, json=change)
            await settled(f"owners:update:{label}")
            await ask(client, f"owners:update:{label}:card", "GET", card_url)
        for label, data in (("path", {"save_path": "/media/proto2"}), ("path-relative", {"save_path": "media/x"}),
                            ("path-unsafe", {"save_path": "/media/p:q"}), ("sources", {"sources": []}),
                            ("removed-url", {"url": "x"}), ("removed-profile", {"parser_profile_id": 1}),
                            ("not-writable", {"last_scan_time": "x", "bogus": 1}), ("unsafe-name", {"name_en": "a:b?"})):
            await over_bus(f"owners:update:bus:{label}", "catalog.series.update", {"series_id": sid, "fields": data})
            await settled(f"owners:update:bus:{label}")
        await over_bus("owners:update:bus:unknown", "catalog.series.update", {"series_id": 999999, "fields": {"name": "x"}})
        await ask(client, "owners:update:http-unknown", "POST", "/api/series/999999", json={"name": "x"})
        for label, method, route, body in (("auto-scan", "POST", "toggle_auto_scan", {"enabled": True}),
                                           ("auto-scan-off", "POST", "toggle_auto_scan", {"enabled": False}),
                                           ("auto-scan-missing", "POST", "toggle_auto_scan", {}),
                                           ("ignored-seasons", "POST", "ignored-seasons", {"seasons": [1, 3]}),
                                           ("ignored-seasons-missing", "POST", "ignored-seasons", {}),
                                           ("ignored-episodes", "PUT", "ignored-episodes", {"episodes": {"1": [2, 4]}}),
                                           ("ignored-episodes-missing", "PUT", "ignored-episodes", {})):
            await ask(client, f"owners:flag:{label}", method, f"{card_url}/{route}", json=body)
            await settled(f"owners:flag:{label}")
        await ask(client, "owners:flag:card", "GET", card_url)
        await over_bus("owners:save-path", "catalog.series.set_save_path", {"series_id": sid, "save_path": "/media/proto3"})
        await over_bus("owners:save-path:relative", "catalog.series.set_save_path", {"series_id": sid, "save_path": "x/y"})
        await settled("owners:save-path")
        probe.send_command("catalog.series.touch_scan_time", {"series_id": sid})
        await settled("owners:touch")
        await ask(client, "owners:touch:card", "GET", card_url)

        # --- viewing, the contributions to the status, a busy series, the windows gone ---------------------------------------
        for label, state in (("viewing", ["viewing"]), ("closed", [])):
            await ask(client, f"owners:state:{label}", "POST", f"{card_url}/state", json={"state": state})
            await settled(f"owners:state:{label}")
        probe.publish_event("series.status.contribution", {"series_id": sid, "source": "probe", "flags": {"downloading": True}})
        await settled("owners:contribution")
        probe.publish_event("series.status.contribution", {"series_id": sid, "source": "probe", "flags": {"bogus": True}})
        await settled("owners:contribution:unknown-flag")
        probe.publish_event("series.status.contribution", {"series_id": sid, "source": "probe", "flags": {"downloading": False}})
        await settled("owners:contribution:cleared")
        await ask(client, "owners:state:viewing-again", "POST", f"{card_url}/state", json={"state": ["viewing"]})
        probe.publish_event("gateway.sse.clients", {"count": 1})
        await settled("owners:clients:one")
        probe.publish_event("gateway.sse.clients", {"count": 0})
        await settled("owners:clients:none")
        probe.publish_event("series.busy.contribution", {"series_id": sid, "source": "probe", "busy": True})
        await settled("owners:busy")
        await ask(client, "owners:busy:card", "GET", card_url)
        await ask(client, "owners:busy:update", "POST", card_url, json={"name": "занят"})
        await ask(client, "owners:busy:source-add", "POST", f"{card_url}/sources",
                  json={"url": "https://proto.invalid/z", "source_type": "web_video"})
        await settled("owners:busy:refused")
        probe.publish_event("series.busy.contribution", {"series_id": sid, "source": "probe", "busy": False})
        await settled("owners:busy:over")
        probe.publish_event("series.busy.contribution", {"series_id": sid, "source": "probe", "busy": False})
        await settled("owners:busy:over-again")

        # --- the sources of the series: added, refused, changed, cleared ----------------------------------------------------
        sources_url = f"{card_url}/sources"
        first, last = [each["id"] for each in (await client.get(sources_url)).json()][:2]
        added = await ask(client, "owners:source:add", "POST", sources_url, json={
            "url": "https://proto.invalid/c", "source_type": "vk_video", "players_off": ["x"], "rows_per_line": "5-9"})
        extra = added.json().get("id")
        await settled("owners:source:add")
        for label, series_of, body in (
                ("duplicate", sid, {"url": "https://proto.invalid/c", "source_type": "web_video"}),
                ("unknown-profile", sid, {"url": "https://proto.invalid/d", "source_type": "web_video", "parser_profile_id": 999999}),
                ("empty-url", sid, {"url": " ", "source_type": "web_video"}),
                ("rows-invalid", sid, {"url": "https://proto.invalid/d", "source_type": "web_video", "rows_per_line": "x"}),
                ("unknown-series", 999999, {"url": "https://proto.invalid/d", "source_type": "web_video"}),
                ("position", sid, {"url": "https://proto.invalid/e", "source_type": "web_video", "position": 0}),
                ("unknown-field", sid, {"url": "https://proto.invalid/f", "source_type": "web_video", "color": 3})):
            await ask(client, f"owners:source:add:{label}", "POST", f"/api/series/{series_of}/sources", json=body)
            await settled(f"owners:source:add:{label}")
        await ask(client, "owners:source:list", "GET", sources_url)
        for label, source_id, series_of, body in (
                ("url", extra, sid, {"url": "https://proto.invalid/c2"}), ("position", extra, sid, {"position": 0}),
                ("players", extra, sid, {"players_off": ["a", "a", "b"]}), ("rows", extra, sid, {"rows_per_line": "-3"}),
                ("profile", extra, sid, {"parser_profile_id": profile}),
                ("profile-cleared", extra, sid, {"parser_profile_id": None}),
                ("season", extra, sid, {"vibix_season": 3}), ("season-cleared", extra, sid, {"vibix_season": None}),
                ("same", extra, sid, {"url": "https://proto.invalid/c2", "position": 0}), ("empty", extra, sid, {}),
                ("duplicate-url", extra, sid, {"url": "https://proto.invalid/a"}), ("season-zero", extra, sid, {"vibix_season": 0}),
                ("rows-invalid", extra, sid, {"rows_per_line": "0"}), ("unknown-profile", extra, sid, {"parser_profile_id": 999999}),
                ("unknown-source", 999999, sid, {"url": "https://proto.invalid/g"}),
                ("source-of-another-series", first, second, {"url": "https://proto.invalid/g"}),
                ("unknown-field", extra, sid, {"url": "https://proto.invalid/g", "color": 2})):
            await ask(client, f"owners:source:change:{label}", "PUT", f"/api/series/{series_of}/sources/{source_id}", json=body)
            await settled(f"owners:source:change:{label}")
            await ask(client, f"owners:source:change:{label}:list", "GET", sources_url)
        for label, source_id, series_of in (("added", extra, sid), ("again", extra, sid), ("of-another-series", last, second),
                                            ("one", last, sid), ("last", first, sid)):
            await ask(client, f"owners:source:remove:{label}", "DELETE", f"/api/series/{series_of}/sources/{source_id}")
            await settled(f"owners:source:remove:{label}")
            await ask(client, f"owners:source:remove:{label}:list", "GET", f"/api/series/{series_of}/sources")

        # --- the series gone: its sources with it, the owners of the rest told by the event and answering by theirs ----------
        await ask(client, "owners:delete", "DELETE", card_url)
        await settled("owners:delete")
        await ask(client, "owners:delete:card", "GET", card_url)
        await ask(client, "owners:delete:again", "DELETE", card_url)
        await over_bus("owners:delete:bus", "catalog.series.delete", {"series_id": second})
        await settled("owners:delete:bus")
        await ask(client, "owners:delete:list", "GET", "/api/series")
        await listed("owners:delete:bus:list", "catalog.series.list", {}, "series")

        # --- the settings: by key and by prefix, written one and many, a secret, the saved paths -------------------------------
        for key in ("voices.source_priority", "debug_enabled_catalog", "scanner_agent_enabled", "no.such.key"):
            await over_bus(f"owners:settings:get:{key}", "settings.value.get", {"key": key})
        for prefix in ("voices.", "debug_enabled_", "conform", "tmdb", "no.such.", ""):
            await over_bus(f"owners:settings:prefix:{prefix}", "settings.values.by_prefix", {"prefix": prefix})
        for label, topic, payload in (("set", "settings.value.set", {"key": "probe.one", "value": "1"}),
                                      ("set-again", "settings.value.set", {"key": "probe.one", "value": "1"}),
                                      ("set-empty", "settings.value.set", {"key": "probe.one", "value": ""}),
                                      ("set-many", "settings.values.set_many",
                                       {"values": {"probe.a": "x", "probe.b": 2, "probe.c": True}}),
                                      ("set-many-empty", "settings.values.set_many", {"values": {}}),
                                      ("secret", "settings.value.set", {"key": "tmdb_token", "value": "probe-token"}),
                                      ("secret-many", "settings.values.set_many",
                                       {"values": {"provider.token": "probe-provider", "probe.d": "1"}})):
            await over_bus(f"owners:settings:{label}", topic, payload)
            await settled(f"owners:settings:{label}")
        for key in ("tmdb_token", "provider.token", "probe.one"):
            await over_bus(f"owners:settings:after:{key}", "settings.value.get", {"key": key})
        for prefix in ("probe.", "provider.", "tmdb"):
            await over_bus(f"owners:settings:after-prefix:{prefix}", "settings.values.by_prefix", {"prefix": prefix})
        flag = "/api/settings/conform_clear_finished"
        await ask(client, "owners:settings:http:flag", "GET", flag)
        await ask(client, "owners:settings:http:flag-set", "POST", flag, json={"enabled": True})
        await settled("owners:settings:http:flag-set")
        await ask(client, "owners:settings:http:flag-after", "GET", flag)
        await ask(client, "owners:settings:http:flag-nothing", "POST", flag, json={})
        debug = "/api/settings/debug_flags"
        await ask(client, "owners:settings:http:debug", "GET", debug)
        await ask(client, "owners:settings:http:debug-set", "POST", debug, json={"module": "catalog", "enabled": True})
        await settled("owners:settings:http:debug-set")
        await ask(client, "owners:settings:http:debug-after", "GET", debug)
        await ask(client, "owners:settings:http:debug-no-module", "POST", debug, json={"enabled": True})
        await ask(client, "owners:settings:http:tmdb-secret", "GET", "/api/auth/tmdb/secret")
        paths = "/api/settings/saved_paths"
        for label, method, body in (("list", "GET", None), ("add", "POST", {"path": "/media/a"}),
                                    ("add-again", "POST", {"path": "/media/a"}), ("add-unsafe", "POST", {"path": "/media/b:c/ d?"}),
                                    ("add-empty", "POST", {"path": ""}), ("add-nothing", "POST", {}), ("list-after", "GET", None),
                                    ("remove-unknown", "DELETE", {"id": 999999})):
            await ask(client, f"owners:paths:{label}", method, paths, **({} if body is None else {"json": body}))
            await settled(f"owners:paths:{label}")
        for number, each in enumerate((await client.get(paths)).json()["paths"]):
            await ask(client, f"owners:paths:remove:{number}", "DELETE", paths, json={"id": each["id"]})
            await settled(f"owners:paths:remove:{number}")
        await over_bus("owners:paths:bus:add", "settings.paths.add", {"path": "/media/c"})
        await over_bus("owners:paths:bus:list", "settings.paths.list", {})
        await over_bus("owners:paths:bus:remove", "settings.paths.remove", {"id": "1"})
        await settled("owners:paths:bus")

        # --- auth: the admin read, a known password set, the login right and wrong, the session, the lock ----------------------
        await over_bus("owners:auth:exists", "auth.exists", {})
        await over_bus("owners:auth:set:empty", "auth.password.set", {"username": "", "password": "x"})
        await over_bus("owners:auth:set:no-password", "auth.password.set", {"username": "proto"})
        answers["owners:auth:refused:owners"] = owners_digest(path)
        await over_bus("owners:auth:set", "auth.password.set", {"username": "proto", "password": "proto-password-1"})
        answers["owners:auth:set:owners"] = owners_digest(path)
        await over_bus("owners:auth:exists:after", "auth.exists", {})
        for label, payload in (("right", {"username": "proto", "password": "proto-password-1"}),
                               ("padded", {"username": " proto ", "password": "proto-password-1"}),
                               ("wrong", {"username": "proto", "password": "nope"}),
                               ("unknown", {"username": "ghost", "password": "proto-password-1"}), ("empty", {})):
            await over_bus(f"owners:auth:login:{label}", "auth.login", payload)
        answers["owners:auth:login:owners"] = owners_digest(path)
        await ask(client, "owners:auth:me:before", "GET", "/api/me")
        await ask(client, "owners:auth:status:before", "GET", "/api/auth/status")
        await ask(client, "owners:auth:http:wrong", "POST", "/api/login", json={"username": "proto", "password": "nope"})
        await ask(client, "owners:auth:http:unknown", "POST", "/api/login",
                  json={"username": "ghost", "password": "proto-password-1"})
        await ask(client, "owners:auth:password:no-session", "POST", "/api/auth/password", json={"new": "proto-password-2"})
        await ask(client, "owners:auth:http:right", "POST", "/api/login",
                  json={"username": "proto", "password": "proto-password-1"})
        await ask(client, "owners:auth:me", "GET", "/api/me")
        await ask(client, "owners:auth:status", "GET", "/api/auth/status")
        await ask(client, "owners:auth:setup", "POST", "/api/setup", json={"username": "other", "password": "other-password-1"})
        await ask(client, "owners:auth:password:short", "POST", "/api/auth/password", json={"new": "short"})
        await ask(client, "owners:auth:password", "POST", "/api/auth/password", json={"new": "proto-password-2"})
        answers["owners:auth:password:owners"] = owners_digest(path)
        await ask(client, "owners:auth:logout", "POST", "/api/logout")
        await ask(client, "owners:auth:me:after", "GET", "/api/me")
        await ask(client, "owners:auth:http:old-password", "POST", "/api/login",
                  json={"username": "proto", "password": "proto-password-1"})
        await ask(client, "owners:auth:http:new-password", "POST", "/api/login",
                  json={"username": "proto", "password": "proto-password-2"})
        await ask(client, "owners:auth:status:again", "GET", "/api/auth/status")
        # A hash made with weaker parameters than the service's own: a right password renews it, a wrong one does not.
        weak = sqlite3.connect(path)
        weak.execute("UPDATE admin_user SET password_hash = ? WHERE id = 1",
                     (PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("proto-password-3"),))
        weak.commit()
        weak.close()
        answers["owners:auth:weak:owners"] = owners_digest(path)
        await over_bus("owners:auth:login:weak-wrong", "auth.login", {"username": "proto", "password": "nope"})
        answers["owners:auth:weak-wrong:owners"] = owners_digest(path)
        await over_bus("owners:auth:login:weak", "auth.login", {"username": "proto", "password": "proto-password-3"})
        answers["owners:auth:rehashed:owners"] = owners_digest(path)
        await over_bus("owners:auth:login:rehashed", "auth.login", {"username": "proto", "password": "proto-password-3"})
        # The lock: five wrong passwords from one address, then the right one is refused too.
        for number in range(5):
            await status_of(f"owners:auth:lock:wrong:{number}", "POST", "/api/login",
                            json={"username": "proto", "password": f"nope{number}"})
        await status_of("owners:auth:lock:right", "POST", "/api/login",
                        json={"username": "proto", "password": "proto-password-3"})
        told("owners:auth")

    async def sources_window(client) -> None:
        """The windows of `sources`, `trackerauth` and `metadata`: stored tables, addresses, lists, settings, terms, the provider
        of the socket as the model of the specification plays it, the credentials of the trackers, the fetch through them, the
        releases of a tracker page, the files of the releases and the mappings of TMDB. Every transport that leaves the machine in
        the service is a model here: the trackers (`requests`), the sites and the API of anilibria (`httpx` of the module), TMDB."""
        import requests
        import requests.adapters
        from collections import Counter
        from datetime import timedelta
        from urllib.parse import urlparse
        from core.topics import TOPICS
        sys.path.insert(0, os.path.join(root, "tests"))
        from provider_mock import MockProvider, MockServer, link_of

        sample = os.path.join(root, "tests", "samples", "trackers")
        samples = {name: open(os.path.join(sample, name), encoding="utf-8").read()
                   for name in ("kinozal_page.html", "rutracker_page.html", "anilibria_release.json")}
        torrent = b"d4:infod4:name1:xee"
        # What the run asks of the trackers over `requests` (the credentials are the run's own), and how they answer: the page of
        # a release once logged in, the login form before, a file of a torrent, the API of VK.
        trackers = {"kinozal_in": False}

        def answer(request, text="", *, content=None, status=200, kind="text/html; charset=utf-8", url=None):
            made = requests.Response()
            made.url, made.status_code = url or request.url, status
            made._content = content if content is not None else text.encode("utf-8")
            made.headers.update({"Content-Type": kind})
            made.encoding, made.raw, made.request = "utf-8", None, request
            return made

        def tracker_model(self, request, **kwargs):
            host, url = urlparse(request.url).hostname or "", request.url
            if host.endswith(("kinozal.me", "kinozal.tv")):
                if "takelogin.php" in url:
                    trackers["kinozal_in"] = True
                    return answer(request, "<html>userdetails</html>", url=f"https://{host}/userdetails.php")
                if not trackers["kinozal_in"]:
                    return answer(request, '<form method="post" action="/takelogin.php">вход</form>')
                if "download.php" in url:
                    return answer(request, content=torrent, kind="application/x-bittorrent")
                return answer(request, samples["kinozal_page.html"])
            if host.endswith("rutracker.org"):
                return answer(request, samples["rutracker_page.html"])
            if host == "api.vk.com":
                return answer(request, '{"response": {"count": 0, "items": []}}', kind="application/json")
            return answer(request, "not found", status=404)

        def web_model(request: httpx.Request) -> httpx.Response:
            if request.url.host in ("anilibria.top", "aniliberty.top") and "/api/v1/anime/releases/" in request.url.path:
                return httpx.Response(200, text=samples["anilibria_release.json"], headers={"content-type": "application/json"})
            return httpx.Response(404, text="not found")

        def tmdb_model(request: httpx.Request) -> httpx.Response:
            if request.headers.get("authorization") != "Bearer probe-tmdb-token":
                return httpx.Response(401, json={"status_message": "Invalid API key"})
            ident = request.url.path.rsplit("/", 1)[-1]
            if request.url.path.endswith("/search/tv"):
                name = "Во все тяжкие" if request.url.params.get("language") == "ru-RU" else "Breaking Bad"
                return httpx.Response(200, json={"results": [
                    {"id": 4638, "name": name, "original_name": "Breaking Bad", "first_air_date": "2008-01-20",
                     "poster_path": "/p.jpg", "overview": "химия"},
                    {"id": 7, "name": "Без дат", "original_name": None, "first_air_date": None, "poster_path": None, "overview": None}]})
            if "/tv/" in request.url.path and ident != "999":
                return httpx.Response(200, json={"name": "Т", "original_name": "T", "poster_path": "/q.jpg", "status": "Ended", "seasons": [
                    {"season_number": 0, "episode_count": 10, "air_date": None, "name": "Спешлы"},
                    {"season_number": 1, "episode_count": 26, "air_date": "2008-01-20", "name": "Сезон 1"},
                    {"season_number": 2, "episode_count": 24, "air_date": "2009-01-20", "name": "Сезон 2"},
                    {"season_number": 3, "episode_count": 20, "air_date": "2010-01-20", "name": "Сезон 3"},
                    {"season_number": 4, "episode_count": None, "air_date": None, "name": "Сезон 4"}]})
            return httpx.Response(404, json={"status_message": "not found"})

        original_send = requests.adapters.HTTPAdapter.send
        requests.adapters.HTTPAdapter.send = tracker_model
        reader._http = httpx.AsyncClient(transport=httpx.MockTransport(web_model), follow_redirects=True)
        await metadata._http.aclose()
        metadata._http = httpx.AsyncClient(base_url="https://api.themoviedb.org/3", transport=httpx.MockTransport(tmdb_model),
                                           timeout=10.0)
        refresh_topic = "sources.table.refresh" if "sources.table.refresh" in TOPICS else "sources.table.get"

        def refreshing(url: str, **more) -> dict:
            """What asks the page to be read if its table is not current: the command of the new tree, the query with `refresh` of the old."""
            return {"url": url, **more} if refresh_topic != "sources.table.get" else {"url": url, "refresh": True, **more}

        async def done(label: str) -> None:
            """What a command left: the work it began has ended, then the tables of the three modules and the packets told."""
            await composition.idle()
            await quiet(bus, planner, modules, reader)
            answers[f"{label}:sources"] = sources_digest(path)
            told(label)

        async def socket_is(sub, state: str) -> None:
            """Returns when the socket of the provider stands in `state`: the event of its change is the signal."""
            while not ((connector := reader.connector) is not None and connector.state == state):
                await asyncio.wait_for(sub.queue.get(), 30)

        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        urls = [row[0] for row in conn.execute("SELECT url FROM release_tables ORDER BY url")]
        newest = conn.execute("SELECT url FROM release_tables WHERE read_at IS NOT NULL ORDER BY read_at DESC LIMIT 1").fetchone()[0]
        oldest = conn.execute("SELECT url FROM release_tables WHERE url LIKE 'http%' AND read_at IS NOT NULL "
                              "AND url NOT LIKE '%kinozal%' AND url NOT LIKE '%rutracker%' ORDER BY read_at LIMIT 1").fetchone()[0]
        trackers_stored = [row[0] for row in conn.execute("SELECT id FROM trackers ORDER BY id")]
        multi = [row[0] for row in conn.execute(
            "SELECT m.series_id FROM series_tmdb_mappings m JOIN series s ON s.id = m.series_id WHERE s.season = '' ORDER BY 1")]
        mapped = [row[0] for row in conn.execute("SELECT series_id FROM series_tmdb_mappings ORDER BY 1")]
        conn.close()
        hosts = [host for host, _ in Counter(urlparse(each).hostname or "" for each in urls if each.startswith("http")).most_common(3)]
        card = "title://mock/7/X"
        # What the start of the modules told is not the order of this run's own: the packets of the start come as the reads end.
        answers.pop("events:start", None)
        answers["sources:start"] = sources_digest(path)
        told("sources:start")

        # --- the tables of the pages the base holds, and the pages it does not ------------------------------------------------
        for url in urls:
            await over_bus(f"sources:table:{url}", "sources.table.get", {"url": url, "refresh": False})
        for label, payload in (("absent", {"url": "https://1.ruchime.org/release/never-read", "refresh": False}),
                               ("no-site", {"url": "https://example.invalid/page", "refresh": False}),
                               ("provider-card", {"url": card, "refresh": False}), ("no-url", {}), ("empty", {"url": ""})):
            await over_bus(f"sources:table:{label}", "sources.table.get", payload)

        # --- an address and the site that reads it: every page, its other spellings, and none -----------------------------------
        for url in urls:
            await over_bus(f"sources:resolve:{url}", "sources.site.resolve", {"url": url})
            await ask(client, f"sources:resolve:http:{url}", "POST", "/api/sources/resolve", json={"url": url})
        for label, url in (("kinozal-dl", "https://dl.kinozal.me/details.php?id=2034617"), ("kinozal-www", "https://www.kinozal.tv/details.php?id=1"),
                           ("rutracker", "https://rutracker.org/forum/viewtopic.php?t=1"), ("anilibria", "https://anilibria.top/anime/releases/release/x/episodes"),
                           ("vk", "https://vkvideo.ru/@chan|тайтл"), ("none", "https://example.invalid/x"), ("empty", ""),
                           ("card", card), ("not-a-url", "хочу смотреть")):
            await over_bus(f"sources:resolve:{label}", "sources.site.resolve", {"url": url})
            await ask(client, f"sources:resolve:http:{label}", "POST", "/api/sources/resolve", json={"url": url})
        await over_bus("sources:resolve:no-url", "sources.site.resolve", {})
        await ask(client, "sources:resolve:http:no-url", "POST", "/api/sources/resolve", json={})

        # --- the players, the trackers and the settings of the sources ----------------------------------------------------------
        for label, payload in (("none", {}), ("all", {"urls": urls}), ("some", {"urls": urls[:5]}), ("unknown", {"urls": ["https://example.invalid/x"]}),
                               ("twice", {"urls": urls[:2] + urls[:2]})):
            await over_bus(f"sources:players:{label}", "sources.players.list", payload)
        await ask(client, "sources:settings", "GET", "/api/sources/settings")
        await over_bus("sources:settings:bus", "sources.settings.get", {})
        await ask(client, "sources:trackers", "GET", "/api/trackers")
        await over_bus("sources:trackers:bus", "sources.trackers.list", {})
        kinozal = trackers_stored[2]
        for label, body in (("mirrors", {"mirrors": ["kinozal.me", "kinozal.guru", "kinozal.tv", "kinozal.example"]}),
                            ("same", {"mirrors": ["kinozal.me", "kinozal.guru", "kinozal.tv", "kinozal.example"]}),
                            ("empty", {"mirrors": []}), ("no-mirrors", {}), ("back", {"mirrors": ["kinozal.me", "kinozal.guru", "kinozal.tv"]})):
            await ask(client, f"sources:trackers:set:{label}", "PUT", f"/api/trackers/{kinozal}", json=body)
            await done(f"sources:trackers:set:{label}")
            await ask(client, f"sources:trackers:set:{label}:settings", "GET", "/api/sources/settings")
            await ask(client, f"sources:trackers:set:{label}:list", "GET", "/api/trackers")
        await ask(client, "sources:trackers:set:unknown", "PUT", "/api/trackers/999999", json={"mirrors": ["x.example"]})
        await done("sources:trackers:set:unknown")
        await ask(client, "sources:trackers:set:back:list", "GET", "/api/trackers")

        # --- the terms of the sites: read, set, refused, and what the tables say of them ------------------------------------------
        await ask(client, "sources:terms", "GET", "/api/settings/source_terms")
        await over_bus("sources:terms:bus", "sources.terms.get", {})
        await ask(client, "sources:fresh", "POST", "/api/sources/fresh", json={"urls": urls})
        await over_bus("sources:fresh:bus", "sources.tables.fresh", {"urls": [*urls[:3], "https://example.invalid/x", card]})
        await over_bus("sources:fresh:bus:none", "sources.tables.fresh", {"urls": []})
        await over_bus("sources:fresh:bus:no-urls", "sources.tables.fresh", {})
        for label, body in (("set", {"site": hosts[0], "minutes": 15}), ("padded", {"site": hosts[1], "minutes": " 90 "}),
                            ("same", {"site": hosts[0], "minutes": 15}), ("zero", {"site": hosts[0], "minutes": 0}),
                            ("text", {"site": hosts[0], "minutes": "abc"}), ("month", {"site": hosts[2], "minutes": 44640}),
                            ("over", {"site": hosts[2], "minutes": 44641}), ("bool", {"site": hosts[2], "minutes": True}),
                            ("no-site", {"minutes": 5}), ("no-minutes", {"site": hosts[2]}), ("none", {})):
            await ask(client, f"sources:terms:{label}", "POST", "/api/settings/source_terms", json=body)
            await done(f"sources:terms:{label}")
            await ask(client, f"sources:terms:{label}:list", "GET", "/api/settings/source_terms")
        await ask(client, "sources:terms:fresh", "POST", "/api/sources/fresh", json={"urls": urls})

        # --- the credentials of the trackers (the run's own: none of the base is read), TMDB's token among them ----------------------
        await ask(client, "trackerauth:http:empty", "GET", "/api/auth")
        for service in ("qbittorrent", "kinozal", "rutracker", "vk", "astar", "nothing"):
            await over_bus(f"trackerauth:get:empty:{service}", "trackerauth.credentials.get", {"service": service})
            await over_bus(f"trackerauth:secret:empty:{service}", "trackerauth.secret.get", {"service": service})
        await ask(client, "trackerauth:http:save", "POST", "/api/auth", json={
            "qbittorrent": {"url": "http://qb:8080", "username": "admin", "password": "probe-qb-pass"},
            "kinozal": {"username": " probe-kz ", "password": "probe-kz-pass", "url": ""},
            "rutracker": {"username": "probe-rt", "password": "probe-rt-pass"},
            "vk": {"token": "probe-vk-token"}, "tmdb": {"token": "probe-tmdb-token"}})
        await done("trackerauth:http:save")
        await ask(client, "trackerauth:http:saved", "GET", "/api/auth")
        for service in ("qbittorrent", "kinozal", "rutracker", "vk", "tmdb", "astar", "nothing"):
            await ask(client, f"trackerauth:http:secret:{service}", "GET", f"/api/auth/{service}/secret")
        for service in ("qbittorrent", "kinozal", "rutracker", "vk", "astar", "nothing"):
            await over_bus(f"trackerauth:get:{service}", "trackerauth.credentials.get", {"service": service})
            await over_bus(f"trackerauth:secret:{service}", "trackerauth.secret.get", {"service": service})
        for label, payload in (("unchanged", {"service": "kinozal", "username": "probe-kz", "password": ""}),
                               ("no-password", {"service": "rutracker", "username": " probe-rt ", "url": "https://rutracker.org"}),
                               ("changed", {"service": "rutracker", "username": "probe-rt", "password": "probe-rt-pass-2"}),
                               ("empty-name", {"service": "astar", "username": "", "password": None}), ("no-service", {"username": "x"})):
            await over_bus(f"trackerauth:set:{label}", "trackerauth.credentials.set", payload)
            await done(f"trackerauth:set:{label}")
            await over_bus(f"trackerauth:set:{label}:get", "trackerauth.credentials.get", {"service": payload.get("service", "kinozal")})
            await over_bus(f"trackerauth:set:{label}:secret", "trackerauth.secret.get", {"service": payload.get("service", "kinozal")})
        await ask(client, "trackerauth:http:after", "GET", "/api/auth")
        await ask(client, "trackerauth:http:save:no-body", "POST", "/api/auth", json={})
        await ask(client, "trackerauth:http:save:blank", "POST", "/api/auth", json={"kinozal": {"password": ""}, "vk": {}})

        # --- the fetch through them: logged in on the first request, the page, a file, VK, refused, and the login that is not repeated -----
        kz = "https://kinozal.me/details.php?id=2034617"
        for label, payload in (("kinozal", {"service": "kinozal", "url": kz}),
                               ("kinozal-again", {"service": "kinozal", "url": kz}),
                               ("kinozal-file", {"service": "kinozal", "url": "https://dl.kinozal.me/download.php?id=2034617"}),
                               ("kinozal-post", {"service": "kinozal", "url": kz, "method": "POST", "data": {"a": "b"}, "timeout": 5}),
                               ("rutracker", {"service": "rutracker", "url": "https://rutracker.org/forum/viewtopic.php?t=1"}),
                               ("rutracker-file", {"service": "rutracker", "url": "https://rutracker.org/forum/dl.php?t=1"}),
                               ("vk", {"service": "vk", "url": "https://api.vk.com/method/video.get", "params": {"owner_id": 1}}),
                               ("unknown-service", {"service": "nothing", "url": "https://example.invalid/x"}),
                               ("no-credentials", {"service": "astar", "url": "https://astar.example/x"}), ("no-url", {"service": "kinozal"})):
            await over_bus(f"trackerauth:fetch:{label}", "trackerauth.fetch", payload, drop_none=True)
            await done(f"trackerauth:fetch:{label}")
        trackers["kinozal_in"] = False  # the session of the tracker expired: the login is wanted again, within its rate limit
        await over_bus("trackerauth:fetch:rate-limit", "trackerauth.fetch", {"service": "kinozal", "url": kz}, drop_none=True)
        await done("trackerauth:fetch:rate-limit")
        trackers["kinozal_in"] = True

        # --- the releases of a tracker page, the page parsed, and the file of a release in the cache -------------------------------------
        for label, url in (("kinozal", kz), ("rutracker", "https://rutracker.org/forum/viewtopic.php?t=1"),
                           ("unknown", "https://example.invalid/x"), ("not-a-page", "хочу")):
            await over_bus(f"sources:releases:{label}", "sources.releases.get", {"url": url})
            await ask(client, f"sources:releases:http:{label}", "POST", "/api/torrent/page", json={"url": url})
        await over_bus("sources:parse:anilibria", "sources.parse", {"url": "https://anilibria.top/anime/releases/release/x/episodes"})
        await over_bus("sources:parse:unknown", "sources.parse", {"url": "https://example.invalid/x"})
        await over_bus("sources:parse:no-url", "sources.parse", {})
        dl = "https://dl.kinozal.me/download.php?id=2034617"
        for label, payload in (("miss", {"url": dl, "torrent_id": "t1"}), ("hit", {"url": dl, "torrent_id": "t1"}),
                               ("unknown-tracker", {"url": "https://example.invalid/x.torrent", "torrent_id": "t2"}),
                               ("no-id", {"url": dl})):
            await over_bus(f"sources:torrent-file:{label}", "sources.torrent_file.get", payload)
        probe.send_command("sources.torrent_file.drop", {"torrent_id": "t1"})
        probe.send_command("sources.torrent_file.drop", {"torrent_id": "never-cached"})
        await done("sources:torrent-file:drop")
        await over_bus("sources:torrent-file:after-drop", "sources.torrent_file.get", {"url": dl, "torrent_id": "t1"})

        # --- a page read again when its table is not current: the term, the reads that fail over the model of the sites, the user's read --
        await over_bus("sources:refresh:fresh", refresh_topic, refreshing(newest))
        await over_bus("sources:refresh:stale", refresh_topic, refreshing(oldest))
        await done("sources:refresh:stale")
        await over_bus("sources:refresh:stale:get", "sources.table.get", {"url": oldest, "refresh": False})
        await over_bus("sources:refresh:stale:again", refresh_topic, refreshing(oldest, rows="5"))
        await done("sources:refresh:stale:again")
        await over_bus("sources:refresh:no-site", refresh_topic, refreshing("https://example.invalid/page"))
        await over_bus("sources:refresh:no-url", refresh_topic, {})
        chines = next((each for each in urls if each.startswith("title://")), card)
        await over_bus("sources:refresh:provider-page", refresh_topic, refreshing(chines, by_sync=True))
        await done("sources:refresh:provider-page")
        probe.send_command("sources.table.read", {"url": oldest, "rows": "-3"})
        await done("sources:read:oldest")
        probe.send_command("sources.table.read", {"url": "https://example.invalid/page"})
        await done("sources:read:no-site")
        await over_bus("sources:cancel:none", "sources.table.cancel", {"url": oldest})
        await ask(client, "sources:cancel:http:none", "POST", "/api/sources/cancel", json={"url": oldest})
        await over_bus("sources:cancel:no-url", "sources.table.cancel", {})
        clock["now"] += timedelta(days=10)
        await over_bus("sources:refresh:later", refresh_topic, refreshing(newest))
        await done("sources:refresh:later")
        clock["now"] -= timedelta(days=10)

        # --- the provider of the socket: none, refused, pointed at, searched, read, stopped, down and back, cleared -----------------------
        mock = MockProvider(token="probe-provider-token")
        mock.add_site("mock", "Мок (Mock)")
        mock.title("mock", 7, ["名"], [("1", "X", "tok 1", [1080]), ("2", "X", "tok 2", [2160]), ("3", "Y", "tok 3", [])])
        mock.title("mock", 8, ["他"], [("1", "Z", "tok 4", [720])])
        mock.sites["mock"].found = [{"id": 7, "name": "名"}, {"id": 8, "name": "他"}]
        server = MockServer(mock)
        await server.up()
        link = link_of("X", "tok 1")
        pages_asked = [card, "title://mock/8/Z", kz, chines, "title://other/9/X", "https://example.invalid/x"]
        await ask(client, "provider:none", "GET", "/api/provider")
        await over_bus("provider:none:bus", "sources.provider.get", {})
        await ask(client, "provider:none:search", "POST", "/api/sources/search", json={"query": "名"})
        await over_bus("provider:none:search:bus", "sources.titles.search", {"query": "名", "page": 1})
        await over_bus("provider:none:pages", "sources.pages.check", {"urls": pages_asked})
        await over_bus("provider:none:table", "sources.table.get", {"url": card, "refresh": False})
        probe.send_command("sources.table.read", {"url": card})
        await done("provider:none:read")
        await over_bus("provider:none:stream", "sources.video.stream", {"url": link})
        await over_bus("provider:none:stream:web", "sources.video.stream", {"url": "https://example.invalid/x"})
        await over_bus("provider:none:fetch", "sources.video.fetch", {"url": link, "target": os.path.join(folder, "dl", "none.mp4")})
        await over_bus("provider:none:fetch:stop", "sources.video.fetch.stop", {"target": os.path.join(folder, "dl", "none.mp4")})
        for label, body in (("not-http", {"url": "ftp://x"}), ("spaces", {"url": "http://a b"}), ("no-host", {"url": "http://"}),
                            ("no-url", {})):
            await ask(client, f"provider:set:{label}", "PUT", "/api/provider", json=body)
            await done(f"provider:set:{label}")
        sub = bus.subscribe("sources.provider.state")
        await ask(client, "provider:set:wrong-token", "PUT", "/api/provider", json={"url": server.url, "token": "wrong"})
        await socket_is(sub, "down")
        await done("provider:set:wrong-token")
        await ask(client, "provider:wrong-token", "GET", "/api/provider")
        await ask(client, "provider:set", "PUT", "/api/provider", json={"url": server.url + "/", "token": "probe-provider-token"})
        await socket_is(sub, "up")
        await done("provider:set")
        first = await ask(client, "provider:view", "GET", "/api/provider")
        second = await ask(client, "provider:view:again", "GET", "/api/provider")
        answers["provider:view:grows"] = {"grows": second.json()["seq"] > first.json()["seq"]}
        await over_bus("provider:view:bus", "sources.provider.get", {})
        await ask(client, "provider:settings", "GET", "/api/sources/settings")
        await ask(client, "provider:resolve", "POST", "/api/sources/resolve", json={"url": card})
        await over_bus("provider:players", "sources.players.list", {"urls": [card, *urls[:3]]})
        await over_bus("provider:players:none", "sources.players.list", {})
        await ask(client, "provider:search", "POST", "/api/sources/search", json={"query": "名"})
        await ask(client, "provider:search:page", "POST", "/api/sources/search", json={"query": "名", "page": 2})
        await over_bus("provider:search:bus", "sources.titles.search", {"query": "他", "page": 1})
        await over_bus("provider:search:no-query", "sources.titles.search", {})
        mock.search_status = 500
        await ask(client, "provider:search:fails", "POST", "/api/sources/search", json={"query": "名"})
        mock.search_status = 200
        await over_bus("provider:pages", "sources.pages.check", {"urls": pages_asked})
        await over_bus("provider:pages:none", "sources.pages.check", {"urls": []})
        await over_bus("provider:pages:no-urls", "sources.pages.check", {})
        made = await ask(client, "provider:series", "POST", "/api/series", json={
            "name": "Прибор", "name_en": "Proto", "site": "kinozal", "save_path": "/media/proto", "season": "",
            "sources": [{"url": card, "source_type": "web_video", "parser_profile_id": None}],
            "tmdb_data": {"tmdb_id": 4638, "tmdb_season_number": 1, "total_episodes": 5, "poster_path": "/p.jpg",
                          "series_name": "Breaking Bad", "year": "2008"}})
        sid = made.json().get("series_id")
        await done("provider:series")
        await ask(client, "provider:view:counted", "GET", "/api/provider")
        await ask(client, "provider:terms", "GET", "/api/settings/source_terms")
        await over_bus("provider:refresh:first-read", refresh_topic, refreshing(card))
        await over_bus("provider:refresh:by-sync", refresh_topic, refreshing(card, by_sync=True))
        await done("provider:refresh:by-sync")
        ended = bus.subscribe("sources.table.ready", "sources.table.failed")
        await ask(client, "provider:read", "POST", "/api/sources/read", json={"url": card})
        await asyncio.wait_for(ended.queue.get(), 30)
        await done("provider:read")
        await over_bus("provider:read:table", "sources.table.get", {"url": card, "refresh": False})
        await over_bus("provider:read:fresh", "sources.tables.fresh", {"urls": [card]})
        await ask(client, "provider:read:fresh:http", "POST", "/api/sources/fresh", json={"urls": [card]})
        await over_bus("provider:refresh:read", refresh_topic, refreshing(card, by_sync=True))
        clock["now"] += timedelta(days=10)
        await over_bus("provider:refresh:stale", refresh_topic, refreshing(card))
        await over_bus("provider:refresh:stale:by-sync", refresh_topic, refreshing(card, by_sync=True))
        await done("provider:refresh:stale")
        clock["now"] -= timedelta(days=10)
        await ask(client, "provider:read:rows", "POST", "/api/sources/read", json={"url": "title://mock/8/Z", "rows": "1"})
        await done("provider:read:rows")
        await over_bus("provider:read:rows:table", "sources.table.get", {"url": "title://mock/8/Z", "refresh": False})
        mock.read_error = json.dumps({"code": "title_unknown"})
        await ask(client, "provider:read:error", "POST", "/api/sources/read", json={"url": card})
        await done("provider:read:error")
        await over_bus("provider:read:error:table", "sources.table.get", {"url": card, "refresh": False})
        mock.read_error = ""
        mock.read_breaks = True
        await ask(client, "provider:read:breaks", "POST", "/api/sources/read", json={"url": card})
        await done("provider:read:breaks")
        await over_bus("provider:read:breaks:table", "sources.table.get", {"url": card, "refresh": False})
        mock.read_breaks = False
        mock.release.clear()
        begun = bus.subscribe("sources.table.progress")
        await ask(client, "provider:read:slow", "POST", "/api/sources/read", json={"url": card})
        await asyncio.wait_for(begun.queue.get(), 30)  # the read has said where it is: a stop that comes now meets a read that goes
        bus.unsubscribe(begun)
        await over_bus("provider:read:slow:table", "sources.table.get", {"url": card, "refresh": False})
        await over_bus("provider:read:slow:refresh", refresh_topic, refreshing(card, by_sync=True))
        await ask(client, "provider:cancel", "POST", "/api/sources/cancel", json={"url": card})
        await done("provider:cancel")
        mock.release.set()
        await over_bus("provider:cancel:table", "sources.table.get", {"url": card, "refresh": False})
        await over_bus("provider:stream", "sources.video.stream", {"url": link})
        await over_bus("provider:stream:max-height", "sources.video.stream", {"url": link, "max_height": 720})
        await over_bus("provider:stream:web", "sources.video.stream", {"url": "https://example.invalid/x"})
        await over_bus("provider:stream:foreign", "sources.video.stream", {"url": "title://other/9/X"})
        await over_bus("provider:stream:no-url", "sources.video.stream", {})
        target = os.path.join(folder, "dl", "probe.mp4")
        await over_bus("provider:fetch", "sources.video.fetch", {"url": link, "target": target, "max_height": 1080, "connections": 2})
        await done("provider:fetch")
        mock.download_of(link_of("X", "tok 2"), {"type": "error", "reason": json.dumps({"code": "no_such_file"}), "kind": "final"})
        await over_bus("provider:fetch:refused", "sources.video.fetch", {"url": link_of("X", "tok 2"), "target": target + ".2"})
        await over_bus("provider:fetch:not-owned", "sources.video.fetch", {"url": "https://example.invalid/x", "target": target + ".3"})
        await over_bus("provider:fetch:stop", "sources.video.fetch.stop", {"target": target})
        await over_bus("provider:fetch:stop:no-target", "sources.video.fetch.stop", {})
        await over_bus("provider:fetch:no-url", "sources.video.fetch", {"target": target})
        down = bus.subscribe("sources.provider.state")
        await server.down()
        await socket_is(down, "down")
        await done("provider:down")
        await ask(client, "provider:down:view", "GET", "/api/provider")
        await over_bus("provider:down:pages", "sources.pages.check", {"urls": pages_asked})
        await over_bus("provider:down:search", "sources.titles.search", {"query": "名", "page": 1})
        await ask(client, "provider:down:search:http", "POST", "/api/sources/search", json={"query": "名"})
        await over_bus("provider:down:table", "sources.table.get", {"url": card, "refresh": False})
        await over_bus("provider:down:refresh", refresh_topic, refreshing(card, by_sync=True))
        probe.send_command("sources.table.read", {"url": card})
        await done("provider:down:read")
        await over_bus("provider:down:stream", "sources.video.stream", {"url": link})
        await over_bus("provider:down:fetch", "sources.video.fetch", {"url": link, "target": target + ".4"})
        await server.up()
        await socket_is(down, "up")
        await done("provider:up-again")
        await ask(client, "provider:up-again:view", "GET", "/api/provider")
        await over_bus("provider:up-again:pages", "sources.pages.check", {"urls": pages_asked})
        applied = bus.subscribe("sources.provider.manifest")
        mock.changed()
        await asyncio.wait_for(applied.queue.get(), 30)
        await done("provider:manifest-changed")
        mock.add_site("second", "Второй")
        await asyncio.wait_for(applied.queue.get(), 30)
        await done("provider:site-added")
        bus.unsubscribe(applied)
        await ask(client, "provider:site-added:view", "GET", "/api/provider")
        await ask(client, "provider:site-added:settings", "GET", "/api/sources/settings")
        await over_bus("provider:site-added:players", "sources.players.list", {"urls": [card]})
        await ask(client, "provider:clear", "PUT", "/api/provider", json={"url": "", "token": ""})
        await done("provider:clear")
        await ask(client, "provider:cleared", "GET", "/api/provider")
        await ask(client, "provider:cleared:settings", "GET", "/api/sources/settings")
        await over_bus("provider:cleared:pages", "sources.pages.check", {"urls": pages_asked})
        await over_bus("provider:cleared:players", "sources.players.list", {"urls": [card]})
        await over_bus("provider:cleared:table", "sources.table.get", {"url": card, "refresh": False})
        await server.down()
        bus.unsubscribe(sub)
        bus.unsubscribe(down)
        bus.unsubscribe(ended)

        # --- the mappings of TMDB: the series with its mapping, the list, the search and the details over the model, the aggregate -------
        await ask(client, "metadata:http:list", "GET", "/api/series")
        for each in [*mapped[:6], sid, 999999]:
            await over_bus(f"metadata:map:get:{each}", "metadata.map.get", {"series_id": each})
            await ask(client, f"metadata:http:card:{each}", "GET", f"/api/series/{each}")
        await over_bus("metadata:map:list", "metadata.map.list", {})
        await over_bus("metadata:map:get:no-id", "metadata.map.get", {})
        await ask(client, "metadata:http:search", "POST", "/api/tmdb/search", json={"query": "breaking"})
        await ask(client, "metadata:http:search:empty", "POST", "/api/tmdb/search", json={"query": ""})
        await over_bus("metadata:search", "metadata.search", {"query": "breaking"})
        await over_bus("metadata:search:no-query", "metadata.search", {})
        for ident in (4638, 12, 999):
            await ask(client, f"metadata:http:details:{ident}", "GET", f"/api/tmdb/details/{ident}")
            await over_bus(f"metadata:details:{ident}", "metadata.details", {"tmdb_id": ident})
        await over_bus("metadata:details:text-id", "metadata.details", {"tmdb_id": "x"})
        await over_bus("metadata:details:no-id", "metadata.details", {})
        await ask(client, "metadata:http:update", "POST", f"/api/series/{sid}", json={"tmdb_data": {
            "tmdb_id": 4638, "tmdb_season_number": 2, "total_episodes": 7, "poster_path": "/q.jpg", "series_name": "Breaking Bad 2",
            "year": "2009"}})
        await done("metadata:http:update")
        await ask(client, "metadata:http:update:card", "GET", f"/api/series/{sid}")
        for label, payload in (("only-name", {"series_id": sid, "tmdb_data": {"series_name": "Только имя"}}),
                               ("unknown-field", {"series_id": sid, "tmdb_data": {"tmdb_id": 4638, "colour": "red"}}),
                               ("new", {"series_id": mapped[0] + 100000, "tmdb_data": {"tmdb_id": 1, "tmdb_season_number": 1}}),
                               ("no-data", {"series_id": sid})):
            await over_bus(f"metadata:map:set:{label}", "metadata.map.set", payload)
            await done(f"metadata:map:set:{label}")
            await over_bus(f"metadata:map:set:{label}:get", "metadata.map.get", {"series_id": payload["series_id"]})
        for each in [*multi[:4], *mapped[:3], sid, 999999]:
            await over_bus(f"metadata:recompute:{each}", "metadata.seasons.recompute", {"series_id": each})
            await done(f"metadata:recompute:{each}")
        probe.send_command("metadata.seasons.recompute", {"series_id": sid})
        await done("metadata:recompute:command")
        await ask(client, "metadata:http:delete", "DELETE", f"/api/series/{sid}")
        await done("metadata:http:delete")
        await over_bus("metadata:map:get:deleted", "metadata.map.get", {"series_id": sid})
        await over_bus("metadata:map:list:after", "metadata.map.list", {})
        await ask(client, "trackerauth:http:final", "GET", "/api/auth")
        await ask(client, "sources:settings:final", "GET", "/api/sources/settings")
        requests.adapters.HTTPAdapter.send = original_send

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway.app), base_url="http://test",
                                 timeout=120) as client:
        for sid in series:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            sources = conn.execute("SELECT id, url, parser_profile_id FROM series_sources WHERE series_id=? ORDER BY id",
                                   (sid,)).fetchall()
            conn.close()
            answers[f"{sid}:tables:start"] = tables_digest(path, sid)
            cards[sid] = plain(await probe.request("catalog.series.get", {"series_id": sid}))
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
        if owners:
            await owners_window(client)
        if sources_run:
            await sources_window(client)
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
    # The windows of the owners of the data (catalog, settings, auth) need no series: `collect --owners` alone is a run of its own.
    parser.add_argument("--owners", action="store_true")
    # The windows of `sources`, `trackerauth` and `metadata` need no series either and keep their own copy of the base clean of the
    # credentials: `collect --sources` alone is a run of its own.
    parser.add_argument("--sources", action="store_true")
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
    if args.sources:
        # The model of the provider listens on this machine: it is the one address the proxy does not take.
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1"
    asyncio.run(collect(root, args.db, args.out, [int(each) for each in args.rest], args.windows, args.owners, args.sources))
