"""The answers of the window of the layout and of the plan of a series over HTTP, from a tree of code, on a copy of the service's
database, no network.

    window_json.py collect --root <tree> --db <copy of app.db> --out <answers.json> [--windows] [--owners] [--sources] [--torrents] 46 40
    window_json.py collect --root <tree> --out <answers.json> --executors
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
mappings of TMDB over a model of TMDB; the tables of the three are hashed after each command. No sync is run: nothing reaches the library or the network.
With `--torrents` (a run of its own too) the windows of `torrents`, `renaming` and `library` answer over the model of qBittorrent
(tests/qbit_model.py, on the local address) and a model of the neighbours (the page of a tracker, the open syncs, the files of the plan):
the releases the base holds (their records point into a folder of the run, never into the library), the monitor, the queue, the picture a
window that connects is told, the folder the releases download into (refused in every way, set, moved), a release downloaded, followed
to its end, updated into the same folder, held by a sync of a series with its page, unwatched, archived and taken again, lost by the
client and forgotten with its series, the registry of the old pipeline read (its rows are the run's own), the folders of the disk listed
(admitted, refused, missing), and the move of a series planned, replanned, dropped, refused, failed and made; the tables of the three
modules are hashed after each command and the files of the staging and the library folders listed. Each step ends on the event of the
work it began.
With `--executors` (a run of its own too, on the stand of the whole application, tests/app_stand.py, with its own base: the model of the
site and of the players, the model of the alignment service, no network) the windows of `downloads`, `conform` and `scan` answer: the settings
and the parameters of the alignment (refused, set, read), the limits of the downloads (set, refused, nothing named), the schedule and the
switch of the scanner (set, free, refused, empty, on, off, the pass over all series), a sync of a series of two sources to its end (the answer
of the window, the plan, the queue of the downloads, what the three modules answer over the bus), the switch of episodes and of a season, the
run that fetches nothing, a sync whose download fails for good and the card that stays until the user removes it, the refusals of a series
that is busy, the stop of a sync while the service aligns, and the cancel of the jobs of the service; the tables of the three modules and
their settings are hashed after each command, the pictures of the queues are told by their last packet. `compare` is byte for byte: to find out whether a
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
# The tables of the modules torrents and library (the releases of the torrent sources, the old pipeline's registry, the plans of the
# moves of a series): hashed whole after each command of the torrents run.
TORRENTS_OWNED = ("torrent_releases", "torrent_release_files", "torrent_release_versions", "torrents", "torrent_files",
                  "torrent_progress", "agent_tasks", "relocation_tasks")
# What the modules torrents and library tell the window in the torrents run. The monitor's pictures (`torrents.progress.changed`) and the
# flags of a status come as often as the client is asked, which is the pace of the machine: they are told apart in the run, once, at a
# moment of its own (the window that connects).
WATCHED_TORRENTS = ("torrents.release.changed", "torrents.staging.changed", "library.relocation.planned", "library.relocation.started",
                    "library.relocation.finished", "series.busy.contribution", "settings.changed")
_CLIENT_WORD = re.compile(r'"client":\s*"[^"]*"')
# What differs on every run whatever the code (the number of a picture, the seconds of a download, the port of the model of the
# provider, the folder of the run, the address of an object): filled by a run with `--sources`.
_RUN_MASKS: list[tuple[re.Pattern, str]] = []
# The clock readings of a read of a page: they differ on every run whatever the code, so their values are not compared.
_CLOCK = re.compile(r'("(?:read_at|fresh_until)":\s*)"[^"]*"')
# The stamp the base keeps (and the window shows) of a moment: `last_scan_time` after a touch.
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\.\d{6}")


def sse_packet(table, topic: str, payload):
    """What the stream of the gateway writes for an event of `topic`: its table keys the entries by the name of the topic (the trees before
    the stage 5d of the mission) or by the record of the topic (the trees after it); an event the table does not list is not written."""
    out = next((each for key, each in table.items() if getattr(key, "name", key) == topic), None)
    if out is None:
        return payload
    if hasattr(out, "packet"):
        return out.packet(payload)
    return out.form(payload) if out.form else payload


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


def torrents_digest(path: str) -> dict:
    """The tables of `torrents` and `library`, each whole: rows and a hash; the folder the releases download into, and the folders of
    the series (a move changes one). A clock stamp is `<stamp>`."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    for table in TORRENTS_OWNED:
        rows = sorted(conn.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
        out[table] = {"rows": len(rows), "sha": hashlib.sha256(masked(repr(rows)).encode()).hexdigest()}
    rows = sorted(conn.execute("SELECT key, value FROM settings WHERE key = 'torrents.staging_dir'").fetchall(), key=repr)
    out["staging"] = {"rows": len(rows), "sha": hashlib.sha256(masked(repr(rows)).encode()).hexdigest()}
    rows = sorted(conn.execute("SELECT id, save_path FROM series").fetchall(), key=repr)
    out["series:save_path"] = {"rows": len(rows), "sha": hashlib.sha256(masked(repr(rows)).encode()).hexdigest()}
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
                  sources_run: bool = False, torrents_run: bool = False) -> None:
    from core.topics import (AUTH_EXISTS, AUTH_LOGIN, AUTH_PASSWORD_SET, CATALOG_SERIES_CREATE, CATALOG_SERIES_DELETE,
                            CATALOG_SERIES_GET, CATALOG_SERIES_LIST, CATALOG_SERIES_SET_SAVE_PATH,
                            CATALOG_SERIES_TOUCH_SCAN_TIME, CATALOG_SERIES_UPDATE, CATALOG_SOURCES_BY_PROFILE,
                            CATALOG_SOURCES_LIST, GATEWAY_SSE_CLIENTS, LIBRARY_DIRECTORIES_LIST, LIBRARY_RELOCATION_APPLY,
                            LIBRARY_RELOCATION_GET, LIBRARY_RELOCATION_PLAN, METADATA_DETAILS, METADATA_MAP_GET,
                            METADATA_MAP_LIST, METADATA_MAP_SET, METADATA_SEARCH, METADATA_SEASONS_RECOMPUTE,
                            PLANNER_DOWNLOADED_COUNTS, PLANNER_FILES_GET, PLANNER_PACK_GET, PLANNER_REFERENCES_GET,
                            PLANNER_SEASONS, PLANNER_SERIES_GET, PLANNER_WORK_GET, RULES_APPLY, RULES_FORMAT_FILENAME,
                            RULES_FORMAT_TORRENT_FILE, RULES_PROFILES_EXPORT, RULES_PROFILES_LIST, RULES_RULES_LIST,
                            SCAN_RUNS_GET, SCAN_RUN_CHANGED, SERIES_BUSY_CONTRIBUTION, SERIES_DELETED, SERIES_PURGED,
                            SERIES_STATUS_CONTRIBUTION, SETTINGS_PATHS_ADD, SETTINGS_PATHS_LIST, SETTINGS_PATHS_REMOVE,
                            SETTINGS_VALUES_BY_PREFIX, SETTINGS_VALUES_SET_MANY, SETTINGS_VALUE_GET, SETTINGS_VALUE_SET,
                            SOURCES_PAGES_CHECK, SOURCES_PARSE, SOURCES_PLAYERS_LIST, SOURCES_PROVIDER_GET,
                            SOURCES_PROVIDER_MANIFEST, SOURCES_PROVIDER_STATE, SOURCES_RELEASES_GET, SOURCES_SETTINGS_GET,
                            SOURCES_SITE_RESOLVE, SOURCES_TABLES_FRESH, SOURCES_TABLE_CANCEL, SOURCES_TABLE_FAILED,
                            SOURCES_TABLE_GET, SOURCES_TABLE_PROGRESS, SOURCES_TABLE_READ, SOURCES_TABLE_READY,
                            SOURCES_TERMS_GET, SOURCES_TITLES_SEARCH, SOURCES_TORRENT_FILE_DROP, SOURCES_TORRENT_FILE_GET,
                            SOURCES_TRACKERS_LIST, SOURCES_VIDEO_FETCH, SOURCES_VIDEO_FETCH_STOP, SOURCES_VIDEO_STREAM,
                            TORRENTS_COMPOSITION, TORRENTS_DB_ACTIVE, TORRENTS_DB_DOWNLOADED_COUNTS,
                            TORRENTS_DB_FILES_FOR_SERIES, TORRENTS_DB_FILES_LIST, TORRENTS_DB_HISTORY,
                            TORRENTS_DB_PROGRESS_LIST, TORRENTS_FILES_GET, TORRENTS_INFO_GET, TORRENTS_PROGRESS_CHANGED,
                            TORRENTS_QUEUE_CHANGED, TORRENTS_QUEUE_GET, TORRENTS_RELEASES_GET, TORRENTS_RELEASE_CHANGED,
                            TORRENTS_RELEASE_DROP_FILES, TORRENTS_SEASONS, TORRENTS_STAGING_GET, TORRENTS_STAGING_SET,
                            TRACKERAUTH_CREDENTIALS_GET, TRACKERAUTH_CREDENTIALS_SET, TRACKERAUTH_FETCH,
                            TRACKERAUTH_SECRET_GET, UNPACK_EDITOR_GET, UNPACK_TABLE_GET, VOICES_GROUPS_RESOLVE,
                            VOICES_PLAYERS_GET, VOICES_SETTINGS_EXPORT, VOICES_SETTINGS_GET)
    from core.bus import Bus
    from core.db import Database
    from core.module import BaseModule, BusRequestError
    from core.runner import Runner
    from modules.auth import AuthModule
    from modules.catalog import CatalogModule
    from modules.composition import CompositionModule
    from modules.gateway import GatewayModule
    from modules.gateway.module import SSE_MAP
    from modules.library import LibraryModule
    from modules.metadata import MetadataModule
    from modules.renaming import RenamingModule
    from modules.torrents import TorrentsModule
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
            self.handle(TORRENTS_RELEASES_GET, self.on_get)

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
            self.handle(LIBRARY_RELOCATION_GET, self.on_planned)
            self.handle(SERIES_DELETED, self.on_deleted)

        async def on_planned(self, env) -> dict:
            return {"new_path": None}

        async def on_deleted(self, env) -> None:
            for owner in ("scan", "torrents", "library"):
                self.publish_event(SERIES_PURGED, {"series_id": env.payload["series_id"], "owner": owner})

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
            (re.compile(re.escape(folder)), "<tmp>"),
            # A validation error shows a long path cut in the middle, with the random end of the run's folder in it.
            (re.compile(r"window_j\.\.\.\w+(?=/)"), "window_j...<tmp>")]
    qbit = served = None
    if torrents_run:
        # No credential, token or folder of the base is used, and no folder of the library is touched: the releases of the base keep
        # their records and their files' names, in a folder of the run's own; the staging folder is not set (a download is refused
        # until the run sets it) and no page is checked by the schedule.
        conn = sqlite3.connect(path)
        conn.execute("DELETE FROM auth")
        conn.execute("DELETE FROM tracker_sessions")
        conn.execute("DELETE FROM settings WHERE key IN ('torrents.staging_dir', 'scanner_agent_enabled', 'tmdb_token', "
                     "'provider.url', 'provider.token')")
        conn.execute("UPDATE torrent_releases SET folder = replace(folder, '/nas/torrents/series-tracker', ?)",
                     (os.path.join(folder, "legacy"),))
        conn.commit()
        conn.close()
        _RUN_MASKS[:] = [(re.compile(r"127\.0\.0\.1:\d+"), "127.0.0.1:<port>"), (re.compile(r"0x[0-9a-fA-F]{6,}"), "0x<address>"),
                         (re.compile(re.escape(folder)), "<tmp>"), (re.compile(r"window_j\.\.\.\w+(?=/)"), "window_j...<tmp>")]
        # The model of qBittorrent listens on this machine: it is the one address the proxy does not take.
        sys.path.insert(0, os.path.join(root, "tests"))
        from app_stand import Served
        from qbit_model import QbitModel
        qbit = QbitModel()
        served = Served(qbit.app)
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
    pages = library = torrents = None
    if torrents_run:
        # The modules of the run: the owners of the data the torrents ask (the catalog, the settings, the rules and the credentials), the
        # three modules themselves with the model of qBittorrent for the client, and the neighbours that are not on this bus (the pages
        # of the trackers, the open syncs, the files of the plan, the reports of the cascade).
        class Pages(BaseModule):
            """What the sources, the scan and the planner answer the three modules: the page a tracker lists now, its files, the
            open syncs, the files a series lays in its folder."""
            name = "pages"

            def __init__(self, bus) -> None:
                super().__init__(bus)
                self.page: list = []
                self.files: dict[str, bytes] = {}
                self.names: list[str] = []
                self.move_fails = False

            def register(self) -> None:
                for topic, handler in ((SOURCES_RELEASES_GET, self.on_page), (SOURCES_TORRENT_FILE_GET, self.on_file),
                                       (SOURCES_SITE_RESOLVE, self.on_resolve), (SCAN_RUNS_GET, self.on_runs),
                                       (PLANNER_FILES_GET, self.on_names), (SERIES_DELETED, self.on_deleted)):
                    self.handle(topic, handler)

            async def on_page(self, env):
                from core.contracts.torrent import PageReleases
                return PageReleases(url=env.payload["url"], site="kinozal", title="Тайтл", releases=list(self.page))

            async def on_file(self, env):
                from core.contracts import TorrentFile
                return TorrentFile(content_b64=base64.b64encode(self.files[env.payload["url"]]).decode(), from_cache=False)

            async def on_resolve(self, env):
                from core.contracts import SiteResolveReply
                return SiteResolveReply(site="kinozal", canonical_url=env.payload["url"], source_type="torrent")

            async def on_runs(self, env):
                return {"runs": [], "as_of": 0}

            async def on_names(self, env):
                if self.move_fails:
                    raise RuntimeError("the plan cannot be read")
                return {"names": list(self.names)}

            async def on_deleted(self, env) -> None:
                for owner in ("scan", "composition", "planner", "sources"):
                    self.publish_event(SERIES_PURGED, {"series_id": env.payload["series_id"], "owner": owner})

        pages = Pages(bus)
        library_root = os.path.join(folder, "library")
        os.makedirs(library_root)
        library = LibraryModule(bus, db, allowed_roots=[library_root, os.path.join(folder, "staging")])
        # The idle pace of the monitor is an hour: the old pipeline's monitor does not wipe the rows the run puts in its registry, and a
        # release that moves is followed at the pace of `monitor_active`; what a run waits for is the events, not that pace.
        torrents = TorrentsModule(bus, db, qbt_url=served.url, qbt_username=qbit.username, qbt_password=qbit.password,
                                  pipeline_poll=0.02, monitor_active=0.02, monitor_idle=3600)
        modules = [CatalogModule(bus, db), SettingsModule(bus, db), RulesModule(bus, db), TrackerauthModule(bus, db), probe,
                   gateway, pages, torrents, RenamingModule(bus), library, metadata]
    # The packets of the events the window is told, as the stream of the gateway writes them (SSE_MAP, json.dumps).
    events: list[tuple[str, str]] = []

    def seen(env) -> None:
        if env.topic in WATCHED or (sources_run and env.topic in WATCHED_SOURCES) or (torrents_run and env.topic in WATCHED_TORRENTS):
            payload = sse_packet(SSE_MAP, env.topic, env.payload)
            text = masked(json.dumps(payload, ensure_ascii=False, default=plain))
            # The word of the client about a torrent taken again after it was given up is told as the tick that asks it comes: before or
            # after the answer to the one who took it. A step that takes such a torrent says so (`client_free`) and the word is left out.
            events.append((env.topic, _CLIENT_WORD.sub('"client":null', text) if client_free["on"] else text))

    client_free = {"on": False}
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

    async def over_bus(name: str, topic, payload: dict, drop_none: bool = False) -> None:
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
        # In the torrents run the news of a release come as often as the client is asked, which is the pace of the machine: which
        # of the packets between the first and the last were told does not depend on the code, so the last one stands for them.
        answers[f"events:{label}"] = {"events": {
            topic: {"count": len(each), "distinct": [each[-1]] if torrents_run and topic == "torrents.release.changed" else sorted(set(each)),
                    "last": each[-1]} for topic, each in sorted(by_topic.items())}}

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
        await over_bus("rules:profiles:default", RULES_PROFILES_LIST, {})
        await over_bus("rules:profiles:torrent", RULES_PROFILES_LIST, {"kind": "torrent"})
        torrent = [each["id"] for each in await probe.request(RULES_PROFILES_LIST, {"kind": "torrent"})]
        for profile in sorted(used) + torrent[:6]:
            await ask(client, f"rules:rules:{profile}", "GET", f"/api/parser-profiles/{profile}/rules")
        await over_bus("rules:rules:unknown", RULES_RULES_LIST, {"profile_id": 999999})
        await over_bus("rules:export", RULES_PROFILES_EXPORT, {"profile_ids": sorted(used)})
        await over_bus("rules:export:torrent", RULES_PROFILES_EXPORT, {"profile_ids": torrent[:6]})
        await over_bus("rules:export:unknown", RULES_PROFILES_EXPORT, {"profile_ids": [999999]})
        for source_id, profile, lines in samples:
            if not profile:
                continue
            body = [{"key": f"k{each}", "text": text} for each, text in enumerate(lines)]
            await over_bus(f"rules:apply:{source_id}", RULES_APPLY, {"profile_id": profile, "lines": body, "series_season": "1"})
            await over_bus(f"rules:apply:{source_id}:no-season", RULES_APPLY, {"profile_id": profile, "lines": body})
            kept = (await probe.request(RULES_PROFILES_EXPORT, {"profile_ids": [profile]}))["profiles"][profile]
            await over_bus(f"rules:apply:{source_id}:snapshot", RULES_APPLY,
                           {"profile_id": profile, "lines": body, "series_season": "2", "rules": kept})
            await over_bus(f"rules:apply:{source_id}:keyed", RULES_APPLY, {"profile_id": profile, "series_season": "1", "lines": [
                {"key": f"k{each}", "text": text, "player": "kodik", "voice": "Студия"} for each, text in enumerate(lines[:20])]})
        if samples and samples[0][1]:
            profile, line = samples[0][1], samples[0][2][0]
            await over_bus("rules:apply:repeated-keys", RULES_APPLY, {"profile_id": profile, "lines": [
                {"key": "same", "text": line}, {"key": "same", "text": line}]})
            await over_bus("rules:apply:empty", RULES_APPLY, {"profile_id": profile, "lines": []})
        await over_bus("rules:apply:unknown-profile", RULES_APPLY, {"profile_id": 999999, "lines": [{"key": "a", "text": "x"}]})

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
        await over_bus("rules:cmd:apply", RULES_APPLY, body)
        await ask(client, "rules:cmd:rule-update", "PUT", f"/api/parser-rules/{ids[0]}", json=rule("первое-новое", "сер", "серия "))
        await ask(client, "rules:cmd:rule-update:invalid", "PUT", f"/api/parser-rules/{ids[0]}",
                  json={"name": "x", "action_pattern": "нет", "conditions": []})
        await after("rules:cmd:rule-update")
        await ask(client, "rules:cmd:reorder", "POST", "/api/parser-rules/reorder", json=list(reversed(ids)))
        await after("rules:cmd:reorder")
        await over_bus("rules:cmd:apply:reordered", RULES_APPLY, body)
        await over_bus("rules:cmd:export", RULES_PROFILES_EXPORT, {"profile_ids": [profile]})
        await ask(client, "rules:cmd:profile-rename", "PUT", f"/api/parser-profiles/{profile}", json={"name": "прибор: новое имя"})
        await ask(client, "rules:cmd:profile-rename:unknown", "PUT", "/api/parser-profiles/999999", json={"name": "а"})
        await after("rules:cmd:profile-rename")
        await ask(client, "rules:cmd:rule-delete", "DELETE", f"/api/parser-rules/{ids[1]}")
        await after("rules:cmd:rule-delete")
        await over_bus("rules:cmd:apply:after-delete", RULES_APPLY, body)
        if used:
            await ask(client, "rules:cmd:profile-delete:in-use", "DELETE", f"/api/parser-profiles/{sorted(used)[0]}")
            await after("rules:cmd:profile-delete:in-use")
        await ask(client, "rules:cmd:profile-delete", "DELETE", f"/api/parser-profiles/{profile}")
        await ask(client, "rules:cmd:profile-delete:again", "DELETE", f"/api/parser-profiles/{profile}")
        await after("rules:cmd:profile-delete")
        await over_bus("rules:cmd:apply:deleted", RULES_APPLY, body)
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
                    await over_bus(f"rules:name:{sid}:{number}:{kind}", RULES_FORMAT_FILENAME,
                                   {"series": card, "media_item": item, **extra})
            for each in [*torrent[:3], *sorted(used)[:2]]:
                for basename in ("Show.S01E03.1080p.mkv", "[Group] Show - 07 [1080p].mkv", "Specials.mkv", "Show E12 WEB-DL 720p.mkv"):
                    await over_bus(f"rules:torrent-name:{sid}:{each}:{basename}", RULES_FORMAT_TORRENT_FILE, {
                        "series": card, "profile_id": each, "file_basename": basename, "original_path": f"Show/{basename}"})
            await over_bus(f"rules:torrent-name:{sid}:no-profile", RULES_FORMAT_TORRENT_FILE, {
                "series": card, "file_basename": "a.mkv", "original_path": "Show/a.mkv"})
            await over_bus(f"rules:torrent-name:{sid}:unknown-profile", RULES_FORMAT_TORRENT_FILE, {
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
        await over_bus("unpack:first-open:profiles", RULES_PROFILES_LIST, {"kind": "torrent"})
        for page in pages:
            await over_bus(f"unpack:{page}:table", UNPACK_TABLE_GET, {"url": page})
            await over_bus(f"unpack:{page}:editor", UNPACK_EDITOR_GET, {"url": page})
            await ask(client, f"unpack:{page}:http", "POST", "/api/torrent/editor", json={"url": page})
        await over_bus("unpack:unknown:table", UNPACK_TABLE_GET, {"url": "https://nowhere.test/p"})
        await ask(client, "unpack:unknown:http", "POST", "/api/torrent/editor", json={"url": "https://nowhere.test/p"})
        page = max(pages, key=lambda each: each.endswith("6878393"))
        view = (await probe.request(UNPACK_EDITOR_GET, {"url": page}))["view"]
        tabs = view["tabs"]

        def pick(tab: str, count: int = 1, kind: str | None = None) -> list[str]:
            return [each["item"] for each in tabs.get(tab, []) if kind is None or each.get("kind") == kind][:count]

        videos, voices, subs = pick("files", 4, "video"), pick("voices", 2), pick("subtitles", 2)
        streams, sub_streams = pick("voice_streams", 2), pick("subtitle_streams", 2)

        async def command(label: str, route: str, body: dict) -> None:
            await ask(client, f"unpack:cmd:{label}", "POST", f"/api/torrent/editor{route}", json={"url": page, **body})
            await after(f"unpack:cmd:{label}")
            await over_bus(f"unpack:cmd:{label}:table", UNPACK_TABLE_GET, {"url": page})
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
        for record_ in (VOICES_SETTINGS_GET, VOICES_SETTINGS_EXPORT, VOICES_PLAYERS_GET):
            await over_bus(f"voices:bus:{record_.name}", record_, {})
        view = (await client.get("/api/voices/settings")).json()
        groups = view["voice_groups"]
        names = [*groups, *(each for triggers in groups.values() for each in triggers), "Неизвестный голос", "Студия / Дубляж", ""]
        await over_bus("voices:groups", VOICES_GROUPS_RESOLVE, {"names": names})
        for key, value in (("voices.groups", json.dumps({"Группа": ["один", "два"]})), ("voices.groups", "{сломано"),
                           ("voices.source_priority", json.dumps(["vk", "kodik"])), ("voices.source_priority", "[]"),
                           ("voices.languages", json.dumps({"Группа": "xx"})), ("voices.players_off", "нет"),
                           ("voices.cut_max_height", "-1"), ("voices.player_groups", json.dumps({"Плеер": ["vk", "cvh"]}))):
            await over_bus(f"voices:values:{key}:{value}", VOICES_SETTINGS_GET, {"values": {key: value}})
            await over_bus(f"voices:resolve:{key}:{value}", VOICES_GROUPS_RESOLVE, {"names": names, "values": {key: value}})
        draft = {key: view[key] for key in ("priority", "players_off", "voice_groups", "voice_languages")}
        answers["voices:owned:start"] = owned_digest(path)

        async def save(label: str, body: dict) -> None:
            await ask(client, f"voices:cmd:{label}", "PUT", "/api/voices/settings", json=body)
            await after(f"voices:cmd:{label}")
            await ask(client, f"voices:cmd:{label}:settings", "GET", "/api/voices/settings")
            await over_bus(f"voices:cmd:{label}:export", VOICES_SETTINGS_EXPORT, {})

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

        async def listed(name: str, topic, payload: dict, field: str) -> None:
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
        await listed("owners:bus:list", CATALOG_SERIES_LIST, {}, "series")
        for each in ids:
            await ask(client, f"owners:card:{each}", "GET", f"/api/series/{each}")
            await over_bus(f"owners:bus:get:{each}", CATALOG_SERIES_GET, {"series_id": each})
            await ask(client, f"owners:sources:{each}", "GET", f"/api/series/{each}/sources")
            await listed(f"owners:bus:sources:{each}", CATALOG_SOURCES_LIST, {"series_id": each}, "sources")
        await ask(client, "owners:card:unknown", "GET", "/api/series/999999")
        await ask(client, "owners:sources:unknown", "GET", "/api/series/999999/sources")
        await over_bus("owners:bus:get:unknown", CATALOG_SERIES_GET, {"series_id": 999999})
        await listed("owners:bus:sources:unknown", CATALOG_SOURCES_LIST, {"series_id": 999999}, "sources")
        for each in [*profiles, 999999]:
            await listed(f"owners:bus:by-profile:{each}", CATALOG_SOURCES_BY_PROFILE, {"profile_id": each}, "sources")
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
            await over_bus(f"owners:create:refused:{label}", CATALOG_SERIES_CREATE, {"data": data})
        await ask(client, "owners:create:refused:http-no-sources", "POST", "/api/series", json=base_data)
        await ask(client, "owners:create:refused:http-removed-field", "POST", "/api/series",
                  json={**base_data, "url": "x", "sources": [page]})
        await settled("owners:create:refused")
        # What the owner normalises on the way in: the names that become paths, the path itself.
        second = plain(await probe.request(CATALOG_SERIES_CREATE, {"data": {
            **base_data, "name_en": "a:b/c?", "quality_override": "x|y", "save_path": "/media/a:b/ c",
            "sources": [{**page, "url": "https://proto.invalid/s"}]}}))["id"]
        await settled("owners:create:sanitized")
        await over_bus("owners:create:sanitized:card", CATALOG_SERIES_GET, {"series_id": second})

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
            await over_bus(f"owners:update:bus:{label}", CATALOG_SERIES_UPDATE, {"series_id": sid, "fields": data})
            await settled(f"owners:update:bus:{label}")
        await over_bus("owners:update:bus:unknown", CATALOG_SERIES_UPDATE, {"series_id": 999999, "fields": {"name": "x"}})
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
        await over_bus("owners:save-path", CATALOG_SERIES_SET_SAVE_PATH, {"series_id": sid, "save_path": "/media/proto3"})
        await over_bus("owners:save-path:relative", CATALOG_SERIES_SET_SAVE_PATH, {"series_id": sid, "save_path": "x/y"})
        await settled("owners:save-path")
        probe.send_command(CATALOG_SERIES_TOUCH_SCAN_TIME, {"series_id": sid})
        await settled("owners:touch")
        await ask(client, "owners:touch:card", "GET", card_url)

        # --- viewing, the contributions to the status, a busy series, the windows gone ---------------------------------------
        for label, state in (("viewing", ["viewing"]), ("closed", [])):
            await ask(client, f"owners:state:{label}", "POST", f"{card_url}/state", json={"state": state})
            await settled(f"owners:state:{label}")
        probe.publish_event(SERIES_STATUS_CONTRIBUTION, {"series_id": sid, "source": "probe", "flags": {"downloading": True}})
        await settled("owners:contribution")
        probe.publish_event(SERIES_STATUS_CONTRIBUTION, {"series_id": sid, "source": "probe", "flags": {"bogus": True}})
        await settled("owners:contribution:unknown-flag")
        probe.publish_event(SERIES_STATUS_CONTRIBUTION, {"series_id": sid, "source": "probe", "flags": {"downloading": False}})
        await settled("owners:contribution:cleared")
        await ask(client, "owners:state:viewing-again", "POST", f"{card_url}/state", json={"state": ["viewing"]})
        probe.publish_event(GATEWAY_SSE_CLIENTS, {"count": 1})
        await settled("owners:clients:one")
        probe.publish_event(GATEWAY_SSE_CLIENTS, {"count": 0})
        await settled("owners:clients:none")
        probe.publish_event(SERIES_BUSY_CONTRIBUTION, {"series_id": sid, "source": "probe", "busy": True})
        await settled("owners:busy")
        await ask(client, "owners:busy:card", "GET", card_url)
        await ask(client, "owners:busy:update", "POST", card_url, json={"name": "занят"})
        await ask(client, "owners:busy:source-add", "POST", f"{card_url}/sources",
                  json={"url": "https://proto.invalid/z", "source_type": "web_video"})
        await settled("owners:busy:refused")
        probe.publish_event(SERIES_BUSY_CONTRIBUTION, {"series_id": sid, "source": "probe", "busy": False})
        await settled("owners:busy:over")
        probe.publish_event(SERIES_BUSY_CONTRIBUTION, {"series_id": sid, "source": "probe", "busy": False})
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
        await over_bus("owners:delete:bus", CATALOG_SERIES_DELETE, {"series_id": second})
        await settled("owners:delete:bus")
        await ask(client, "owners:delete:list", "GET", "/api/series")
        await listed("owners:delete:bus:list", CATALOG_SERIES_LIST, {}, "series")

        # --- the settings: by key and by prefix, written one and many, a secret, the saved paths -------------------------------
        for key in ("voices.source_priority", "debug_enabled_catalog", "scanner_agent_enabled", "no.such.key"):
            await over_bus(f"owners:settings:get:{key}", SETTINGS_VALUE_GET, {"key": key})
        for prefix in ("voices.", "debug_enabled_", "conform", "tmdb", "no.such.", ""):
            await over_bus(f"owners:settings:prefix:{prefix}", SETTINGS_VALUES_BY_PREFIX, {"prefix": prefix})
        for label, topic, payload in (("set", SETTINGS_VALUE_SET, {"key": "probe.one", "value": "1"}),
                                      ("set-again", SETTINGS_VALUE_SET, {"key": "probe.one", "value": "1"}),
                                      ("set-empty", SETTINGS_VALUE_SET, {"key": "probe.one", "value": ""}),
                                      ("set-many", SETTINGS_VALUES_SET_MANY,
                                       {"values": {"probe.a": "x", "probe.b": 2, "probe.c": True}}),
                                      ("set-many-empty", SETTINGS_VALUES_SET_MANY, {"values": {}}),
                                      ("secret", SETTINGS_VALUE_SET, {"key": "tmdb_token", "value": "probe-token"}),
                                      ("secret-many", SETTINGS_VALUES_SET_MANY,
                                       {"values": {"provider.token": "probe-provider", "probe.d": "1"}})):
            await over_bus(f"owners:settings:{label}", topic, payload)
            await settled(f"owners:settings:{label}")
        for key in ("tmdb_token", "provider.token", "probe.one"):
            await over_bus(f"owners:settings:after:{key}", SETTINGS_VALUE_GET, {"key": key})
        for prefix in ("probe.", "provider.", "tmdb"):
            await over_bus(f"owners:settings:after-prefix:{prefix}", SETTINGS_VALUES_BY_PREFIX, {"prefix": prefix})
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
        await over_bus("owners:paths:bus:add", SETTINGS_PATHS_ADD, {"path": "/media/c"})
        await over_bus("owners:paths:bus:list", SETTINGS_PATHS_LIST, {})
        await over_bus("owners:paths:bus:remove", SETTINGS_PATHS_REMOVE, {"id": "1"})
        await settled("owners:paths:bus")

        # --- auth: the admin read, a known password set, the login right and wrong, the session, the lock ----------------------
        await over_bus("owners:auth:exists", AUTH_EXISTS, {})
        await over_bus("owners:auth:set:empty", AUTH_PASSWORD_SET, {"username": "", "password": "x"})
        await over_bus("owners:auth:set:no-password", AUTH_PASSWORD_SET, {"username": "proto"})
        answers["owners:auth:refused:owners"] = owners_digest(path)
        await over_bus("owners:auth:set", AUTH_PASSWORD_SET, {"username": "proto", "password": "proto-password-1"})
        answers["owners:auth:set:owners"] = owners_digest(path)
        await over_bus("owners:auth:exists:after", AUTH_EXISTS, {})
        for label, payload in (("right", {"username": "proto", "password": "proto-password-1"}),
                               ("padded", {"username": " proto ", "password": "proto-password-1"}),
                               ("wrong", {"username": "proto", "password": "nope"}),
                               ("unknown", {"username": "ghost", "password": "proto-password-1"}), ("empty", {})):
            await over_bus(f"owners:auth:login:{label}", AUTH_LOGIN, payload)
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
        await over_bus("owners:auth:login:weak-wrong", AUTH_LOGIN, {"username": "proto", "password": "nope"})
        answers["owners:auth:weak-wrong:owners"] = owners_digest(path)
        await over_bus("owners:auth:login:weak", AUTH_LOGIN, {"username": "proto", "password": "proto-password-3"})
        answers["owners:auth:rehashed:owners"] = owners_digest(path)
        await over_bus("owners:auth:login:rehashed", AUTH_LOGIN, {"username": "proto", "password": "proto-password-3"})
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
            await over_bus(f"sources:table:{url}", SOURCES_TABLE_GET, {"url": url, "refresh": False})
        for label, payload in (("absent", {"url": "https://1.ruchime.org/release/never-read", "refresh": False}),
                               ("no-site", {"url": "https://example.invalid/page", "refresh": False}),
                               ("provider-card", {"url": card, "refresh": False}), ("no-url", {}), ("empty", {"url": ""})):
            await over_bus(f"sources:table:{label}", SOURCES_TABLE_GET, payload)

        # --- an address and the site that reads it: every page, its other spellings, and none -----------------------------------
        for url in urls:
            await over_bus(f"sources:resolve:{url}", SOURCES_SITE_RESOLVE, {"url": url})
            await ask(client, f"sources:resolve:http:{url}", "POST", "/api/sources/resolve", json={"url": url})
        for label, url in (("kinozal-dl", "https://dl.kinozal.me/details.php?id=2034617"), ("kinozal-www", "https://www.kinozal.tv/details.php?id=1"),
                           ("rutracker", "https://rutracker.org/forum/viewtopic.php?t=1"), ("anilibria", "https://anilibria.top/anime/releases/release/x/episodes"),
                           ("vk", "https://vkvideo.ru/@chan|тайтл"), ("none", "https://example.invalid/x"), ("empty", ""),
                           ("card", card), ("not-a-url", "хочу смотреть")):
            await over_bus(f"sources:resolve:{label}", SOURCES_SITE_RESOLVE, {"url": url})
            await ask(client, f"sources:resolve:http:{label}", "POST", "/api/sources/resolve", json={"url": url})
        await over_bus("sources:resolve:no-url", SOURCES_SITE_RESOLVE, {})
        await ask(client, "sources:resolve:http:no-url", "POST", "/api/sources/resolve", json={})

        # --- the players, the trackers and the settings of the sources ----------------------------------------------------------
        for label, payload in (("none", {}), ("all", {"urls": urls}), ("some", {"urls": urls[:5]}), ("unknown", {"urls": ["https://example.invalid/x"]}),
                               ("twice", {"urls": urls[:2] + urls[:2]})):
            await over_bus(f"sources:players:{label}", SOURCES_PLAYERS_LIST, payload)
        await ask(client, "sources:settings", "GET", "/api/sources/settings")
        await over_bus("sources:settings:bus", SOURCES_SETTINGS_GET, {})
        await ask(client, "sources:trackers", "GET", "/api/trackers")
        await over_bus("sources:trackers:bus", SOURCES_TRACKERS_LIST, {})
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
        await over_bus("sources:terms:bus", SOURCES_TERMS_GET, {})
        await ask(client, "sources:fresh", "POST", "/api/sources/fresh", json={"urls": urls})
        await over_bus("sources:fresh:bus", SOURCES_TABLES_FRESH, {"urls": [*urls[:3], "https://example.invalid/x", card]})
        await over_bus("sources:fresh:bus:none", SOURCES_TABLES_FRESH, {"urls": []})
        await over_bus("sources:fresh:bus:no-urls", SOURCES_TABLES_FRESH, {})
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
            await over_bus(f"trackerauth:get:empty:{service}", TRACKERAUTH_CREDENTIALS_GET, {"service": service})
            await over_bus(f"trackerauth:secret:empty:{service}", TRACKERAUTH_SECRET_GET, {"service": service})
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
            await over_bus(f"trackerauth:get:{service}", TRACKERAUTH_CREDENTIALS_GET, {"service": service})
            await over_bus(f"trackerauth:secret:{service}", TRACKERAUTH_SECRET_GET, {"service": service})
        for label, payload in (("unchanged", {"service": "kinozal", "username": "probe-kz", "password": ""}),
                               ("no-password", {"service": "rutracker", "username": " probe-rt ", "url": "https://rutracker.org"}),
                               ("changed", {"service": "rutracker", "username": "probe-rt", "password": "probe-rt-pass-2"}),
                               ("empty-name", {"service": "astar", "username": "", "password": None}), ("no-service", {"username": "x"})):
            await over_bus(f"trackerauth:set:{label}", TRACKERAUTH_CREDENTIALS_SET, payload)
            await done(f"trackerauth:set:{label}")
            await over_bus(f"trackerauth:set:{label}:get", TRACKERAUTH_CREDENTIALS_GET, {"service": payload.get("service", "kinozal")})
            await over_bus(f"trackerauth:set:{label}:secret", TRACKERAUTH_SECRET_GET, {"service": payload.get("service", "kinozal")})
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
            await over_bus(f"trackerauth:fetch:{label}", TRACKERAUTH_FETCH, payload, drop_none=True)
            await done(f"trackerauth:fetch:{label}")
        trackers["kinozal_in"] = False  # the session of the tracker expired: the login is wanted again, within its rate limit
        await over_bus("trackerauth:fetch:rate-limit", TRACKERAUTH_FETCH, {"service": "kinozal", "url": kz}, drop_none=True)
        await done("trackerauth:fetch:rate-limit")
        trackers["kinozal_in"] = True

        # --- the releases of a tracker page, the page parsed, and the file of a release in the cache -------------------------------------
        for label, url in (("kinozal", kz), ("rutracker", "https://rutracker.org/forum/viewtopic.php?t=1"),
                           ("unknown", "https://example.invalid/x"), ("not-a-page", "хочу")):
            await over_bus(f"sources:releases:{label}", SOURCES_RELEASES_GET, {"url": url})
            await ask(client, f"sources:releases:http:{label}", "POST", "/api/torrent/page", json={"url": url})
        await over_bus("sources:parse:anilibria", SOURCES_PARSE, {"url": "https://anilibria.top/anime/releases/release/x/episodes"})
        await over_bus("sources:parse:unknown", SOURCES_PARSE, {"url": "https://example.invalid/x"})
        await over_bus("sources:parse:no-url", SOURCES_PARSE, {})
        dl = "https://dl.kinozal.me/download.php?id=2034617"
        for label, payload in (("miss", {"url": dl, "torrent_id": "t1"}), ("hit", {"url": dl, "torrent_id": "t1"}),
                               ("unknown-tracker", {"url": "https://example.invalid/x.torrent", "torrent_id": "t2"}),
                               ("no-id", {"url": dl})):
            await over_bus(f"sources:torrent-file:{label}", SOURCES_TORRENT_FILE_GET, payload)
        probe.send_command(SOURCES_TORRENT_FILE_DROP, {"torrent_id": "t1"})
        probe.send_command(SOURCES_TORRENT_FILE_DROP, {"torrent_id": "never-cached"})
        await done("sources:torrent-file:drop")
        await over_bus("sources:torrent-file:after-drop", SOURCES_TORRENT_FILE_GET, {"url": dl, "torrent_id": "t1"})

        # --- a page read again when its table is not current: the term, the reads that fail over the model of the sites, the user's read --
        await over_bus("sources:refresh:fresh", refresh_topic, refreshing(newest))
        await over_bus("sources:refresh:stale", refresh_topic, refreshing(oldest))
        await done("sources:refresh:stale")
        await over_bus("sources:refresh:stale:get", SOURCES_TABLE_GET, {"url": oldest, "refresh": False})
        await over_bus("sources:refresh:stale:again", refresh_topic, refreshing(oldest, rows="5"))
        await done("sources:refresh:stale:again")
        await over_bus("sources:refresh:no-site", refresh_topic, refreshing("https://example.invalid/page"))
        await over_bus("sources:refresh:no-url", refresh_topic, {})
        chines = next((each for each in urls if each.startswith("title://")), card)
        await over_bus("sources:refresh:provider-page", refresh_topic, refreshing(chines, by_sync=True))
        await done("sources:refresh:provider-page")
        probe.send_command(SOURCES_TABLE_READ, {"url": oldest, "rows": "-3"})
        await done("sources:read:oldest")
        probe.send_command(SOURCES_TABLE_READ, {"url": "https://example.invalid/page"})
        await done("sources:read:no-site")
        await over_bus("sources:cancel:none", SOURCES_TABLE_CANCEL, {"url": oldest})
        await ask(client, "sources:cancel:http:none", "POST", "/api/sources/cancel", json={"url": oldest})
        await over_bus("sources:cancel:no-url", SOURCES_TABLE_CANCEL, {})
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
        await over_bus("provider:none:bus", SOURCES_PROVIDER_GET, {})
        await ask(client, "provider:none:search", "POST", "/api/sources/search", json={"query": "名"})
        await over_bus("provider:none:search:bus", SOURCES_TITLES_SEARCH, {"query": "名", "page": 1})
        await over_bus("provider:none:pages", SOURCES_PAGES_CHECK, {"urls": pages_asked})
        await over_bus("provider:none:table", SOURCES_TABLE_GET, {"url": card, "refresh": False})
        probe.send_command(SOURCES_TABLE_READ, {"url": card})
        await done("provider:none:read")
        await over_bus("provider:none:stream", SOURCES_VIDEO_STREAM, {"url": link}, drop_none=True)
        await over_bus("provider:none:stream:web", SOURCES_VIDEO_STREAM, {"url": "https://example.invalid/x"}, drop_none=True)
        await over_bus("provider:none:fetch", SOURCES_VIDEO_FETCH, {"url": link, "target": os.path.join(folder, "dl", "none.mp4")}, drop_none=True)
        await over_bus("provider:none:fetch:stop", SOURCES_VIDEO_FETCH_STOP, {"target": os.path.join(folder, "dl", "none.mp4")}, drop_none=True)
        for label, body in (("not-http", {"url": "ftp://x"}), ("spaces", {"url": "http://a b"}), ("no-host", {"url": "http://"}),
                            ("no-url", {})):
            await ask(client, f"provider:set:{label}", "PUT", "/api/provider", json=body)
            await done(f"provider:set:{label}")
        sub = bus.subscribe(SOURCES_PROVIDER_STATE.name)
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
        await over_bus("provider:view:bus", SOURCES_PROVIDER_GET, {})
        await ask(client, "provider:settings", "GET", "/api/sources/settings")
        await ask(client, "provider:resolve", "POST", "/api/sources/resolve", json={"url": card})
        await over_bus("provider:players", SOURCES_PLAYERS_LIST, {"urls": [card, *urls[:3]]})
        await over_bus("provider:players:none", SOURCES_PLAYERS_LIST, {})
        await ask(client, "provider:search", "POST", "/api/sources/search", json={"query": "名"})
        await ask(client, "provider:search:page", "POST", "/api/sources/search", json={"query": "名", "page": 2})
        await over_bus("provider:search:bus", SOURCES_TITLES_SEARCH, {"query": "他", "page": 1})
        await over_bus("provider:search:no-query", SOURCES_TITLES_SEARCH, {})
        mock.search_status = 500
        await ask(client, "provider:search:fails", "POST", "/api/sources/search", json={"query": "名"})
        mock.search_status = 200
        await over_bus("provider:pages", SOURCES_PAGES_CHECK, {"urls": pages_asked})
        await over_bus("provider:pages:none", SOURCES_PAGES_CHECK, {"urls": []})
        await over_bus("provider:pages:no-urls", SOURCES_PAGES_CHECK, {})
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
        ended = bus.subscribe(SOURCES_TABLE_READY.name, SOURCES_TABLE_FAILED.name)
        await ask(client, "provider:read", "POST", "/api/sources/read", json={"url": card})
        await asyncio.wait_for(ended.queue.get(), 30)
        await done("provider:read")
        await over_bus("provider:read:table", SOURCES_TABLE_GET, {"url": card, "refresh": False})
        await over_bus("provider:read:fresh", SOURCES_TABLES_FRESH, {"urls": [card]})
        await ask(client, "provider:read:fresh:http", "POST", "/api/sources/fresh", json={"urls": [card]})
        await over_bus("provider:refresh:read", refresh_topic, refreshing(card, by_sync=True))
        clock["now"] += timedelta(days=10)
        await over_bus("provider:refresh:stale", refresh_topic, refreshing(card))
        await over_bus("provider:refresh:stale:by-sync", refresh_topic, refreshing(card, by_sync=True))
        await done("provider:refresh:stale")
        clock["now"] -= timedelta(days=10)
        await ask(client, "provider:read:rows", "POST", "/api/sources/read", json={"url": "title://mock/8/Z", "rows": "1"})
        await done("provider:read:rows")
        await over_bus("provider:read:rows:table", SOURCES_TABLE_GET, {"url": "title://mock/8/Z", "refresh": False})
        mock.read_error = json.dumps({"code": "title_unknown"})
        await ask(client, "provider:read:error", "POST", "/api/sources/read", json={"url": card})
        await done("provider:read:error")
        await over_bus("provider:read:error:table", SOURCES_TABLE_GET, {"url": card, "refresh": False})
        mock.read_error = ""
        mock.read_breaks = True
        await ask(client, "provider:read:breaks", "POST", "/api/sources/read", json={"url": card})
        await done("provider:read:breaks")
        await over_bus("provider:read:breaks:table", SOURCES_TABLE_GET, {"url": card, "refresh": False})
        mock.read_breaks = False
        mock.release.clear()
        begun = bus.subscribe(SOURCES_TABLE_PROGRESS.name)
        await ask(client, "provider:read:slow", "POST", "/api/sources/read", json={"url": card})
        await asyncio.wait_for(begun.queue.get(), 30)  # the read has said where it is: a stop that comes now meets a read that goes
        bus.unsubscribe(begun)
        await over_bus("provider:read:slow:table", SOURCES_TABLE_GET, {"url": card, "refresh": False})
        await over_bus("provider:read:slow:refresh", refresh_topic, refreshing(card, by_sync=True))
        await ask(client, "provider:cancel", "POST", "/api/sources/cancel", json={"url": card})
        await done("provider:cancel")
        mock.release.set()
        await over_bus("provider:cancel:table", SOURCES_TABLE_GET, {"url": card, "refresh": False})
        await over_bus("provider:stream", SOURCES_VIDEO_STREAM, {"url": link}, drop_none=True)
        await over_bus("provider:stream:max-height", SOURCES_VIDEO_STREAM, {"url": link, "max_height": 720}, drop_none=True)
        await over_bus("provider:stream:web", SOURCES_VIDEO_STREAM, {"url": "https://example.invalid/x"}, drop_none=True)
        await over_bus("provider:stream:foreign", SOURCES_VIDEO_STREAM, {"url": "title://other/9/X"}, drop_none=True)
        await over_bus("provider:stream:no-url", SOURCES_VIDEO_STREAM, {}, drop_none=True)
        target = os.path.join(folder, "dl", "probe.mp4")
        await over_bus("provider:fetch", SOURCES_VIDEO_FETCH, {"url": link, "target": target, "max_height": 1080, "connections": 2}, drop_none=True)
        await done("provider:fetch")
        mock.download_of(link_of("X", "tok 2"), {"type": "error", "reason": json.dumps({"code": "no_such_file"}), "kind": "final"})
        await over_bus("provider:fetch:refused", SOURCES_VIDEO_FETCH, {"url": link_of("X", "tok 2"), "target": target + ".2"}, drop_none=True)
        await over_bus("provider:fetch:not-owned", SOURCES_VIDEO_FETCH, {"url": "https://example.invalid/x", "target": target + ".3"}, drop_none=True)
        await over_bus("provider:fetch:stop", SOURCES_VIDEO_FETCH_STOP, {"target": target}, drop_none=True)
        await over_bus("provider:fetch:stop:no-target", SOURCES_VIDEO_FETCH_STOP, {}, drop_none=True)
        await over_bus("provider:fetch:no-url", SOURCES_VIDEO_FETCH, {"target": target}, drop_none=True)
        down = bus.subscribe(SOURCES_PROVIDER_STATE.name)
        await server.down()
        await socket_is(down, "down")
        await done("provider:down")
        await ask(client, "provider:down:view", "GET", "/api/provider")
        await over_bus("provider:down:pages", SOURCES_PAGES_CHECK, {"urls": pages_asked})
        await over_bus("provider:down:search", SOURCES_TITLES_SEARCH, {"query": "名", "page": 1})
        await ask(client, "provider:down:search:http", "POST", "/api/sources/search", json={"query": "名"})
        await over_bus("provider:down:table", SOURCES_TABLE_GET, {"url": card, "refresh": False})
        await over_bus("provider:down:refresh", refresh_topic, refreshing(card, by_sync=True))
        probe.send_command(SOURCES_TABLE_READ, {"url": card})
        await done("provider:down:read")
        await over_bus("provider:down:stream", SOURCES_VIDEO_STREAM, {"url": link}, drop_none=True)
        await over_bus("provider:down:fetch", SOURCES_VIDEO_FETCH, {"url": link, "target": target + ".4"}, drop_none=True)
        await server.up()
        await socket_is(down, "up")
        await done("provider:up-again")
        await ask(client, "provider:up-again:view", "GET", "/api/provider")
        await over_bus("provider:up-again:pages", SOURCES_PAGES_CHECK, {"urls": pages_asked})
        applied = bus.subscribe(SOURCES_PROVIDER_MANIFEST.name)
        mock.changed()
        await asyncio.wait_for(applied.queue.get(), 30)
        await done("provider:manifest-changed")
        mock.add_site("second", "Второй")
        await asyncio.wait_for(applied.queue.get(), 30)
        await done("provider:site-added")
        bus.unsubscribe(applied)
        await ask(client, "provider:site-added:view", "GET", "/api/provider")
        await ask(client, "provider:site-added:settings", "GET", "/api/sources/settings")
        await over_bus("provider:site-added:players", SOURCES_PLAYERS_LIST, {"urls": [card]})
        await ask(client, "provider:clear", "PUT", "/api/provider", json={"url": "", "token": ""})
        await done("provider:clear")
        await ask(client, "provider:cleared", "GET", "/api/provider")
        await ask(client, "provider:cleared:settings", "GET", "/api/sources/settings")
        await over_bus("provider:cleared:pages", SOURCES_PAGES_CHECK, {"urls": pages_asked})
        await over_bus("provider:cleared:players", SOURCES_PLAYERS_LIST, {"urls": [card]})
        await over_bus("provider:cleared:table", SOURCES_TABLE_GET, {"url": card, "refresh": False})
        await server.down()
        bus.unsubscribe(sub)
        bus.unsubscribe(down)
        bus.unsubscribe(ended)

        # --- the mappings of TMDB: the series with its mapping, the list, the search and the details over the model, the aggregate -------
        await ask(client, "metadata:http:list", "GET", "/api/series")
        for each in [*mapped[:6], sid, 999999]:
            await over_bus(f"metadata:map:get:{each}", METADATA_MAP_GET, {"series_id": each})
            await ask(client, f"metadata:http:card:{each}", "GET", f"/api/series/{each}")
        await over_bus("metadata:map:list", METADATA_MAP_LIST, {})
        await over_bus("metadata:map:get:no-id", METADATA_MAP_GET, {})
        await ask(client, "metadata:http:search", "POST", "/api/tmdb/search", json={"query": "breaking"})
        await ask(client, "metadata:http:search:empty", "POST", "/api/tmdb/search", json={"query": ""})
        await over_bus("metadata:search", METADATA_SEARCH, {"query": "breaking"})
        await over_bus("metadata:search:no-query", METADATA_SEARCH, {})
        for ident in (4638, 12, 999):
            await ask(client, f"metadata:http:details:{ident}", "GET", f"/api/tmdb/details/{ident}")
            await over_bus(f"metadata:details:{ident}", METADATA_DETAILS, {"tmdb_id": ident})
        await over_bus("metadata:details:text-id", METADATA_DETAILS, {"tmdb_id": "x"})
        await over_bus("metadata:details:no-id", METADATA_DETAILS, {})
        await ask(client, "metadata:http:update", "POST", f"/api/series/{sid}", json={"tmdb_data": {
            "tmdb_id": 4638, "tmdb_season_number": 2, "total_episodes": 7, "poster_path": "/q.jpg", "series_name": "Breaking Bad 2",
            "year": "2009"}})
        await done("metadata:http:update")
        await ask(client, "metadata:http:update:card", "GET", f"/api/series/{sid}")
        for label, payload in (("only-name", {"series_id": sid, "tmdb_data": {"series_name": "Только имя"}}),
                               ("unknown-field", {"series_id": sid, "tmdb_data": {"tmdb_id": 4638, "colour": "red"}}),
                               ("new", {"series_id": mapped[0] + 100000, "tmdb_data": {"tmdb_id": 1, "tmdb_season_number": 1}}),
                               ("no-data", {"series_id": sid})):
            await over_bus(f"metadata:map:set:{label}", METADATA_MAP_SET, payload)
            await done(f"metadata:map:set:{label}")
            await over_bus(f"metadata:map:set:{label}:get", METADATA_MAP_GET, {"series_id": payload["series_id"]})
        for each in [*multi[:4], *mapped[:3], sid, 999999]:
            await over_bus(f"metadata:recompute:{each}", METADATA_SEASONS_RECOMPUTE, {"series_id": each}, drop_none=True)
            await done(f"metadata:recompute:{each}")
        probe.send_command(METADATA_SEASONS_RECOMPUTE, {"series_id": sid})
        await done("metadata:recompute:command")
        await ask(client, "metadata:http:delete", "DELETE", f"/api/series/{sid}")
        await done("metadata:http:delete")
        await over_bus("metadata:map:get:deleted", METADATA_MAP_GET, {"series_id": sid})
        await over_bus("metadata:map:list:after", METADATA_MAP_LIST, {})
        await ask(client, "trackerauth:http:final", "GET", "/api/auth")
        await ask(client, "sources:settings:final", "GET", "/api/sources/settings")
        requests.adapters.HTTPAdapter.send = original_send

    async def torrents_window(client) -> None:
        """The windows of `torrents`, `renaming` and `library` on the model of qBittorrent: the releases of the base and what the monitor
        shows, the folder the releases download into, a release downloaded, updated, held by a sync, unwatched, archived, gone from the
        client and forgotten with its series, the registry of the old pipeline read (its rows are the run's own), the folders of the disk
        listed, and the move of a series planned, refused, failed and made. Each step ends when the work it began has: the events
        and the signals of the modules, never a pause."""
        from core.contracts.torrent import PageRelease
        from qbit_model import V1, V2, payload as content_of, release as release_of
        staging = os.path.join(folder, "staging")
        shelf = os.path.join(folder, "library")
        page_a, page_b = "https://kinozal.tv/details.php?id=900001", "https://kinozal.tv/details.php?id=900002"
        news = bus.subscribe(TORRENTS_RELEASE_CHANGED.name)
        queue_sub, progress_sub = bus.subscribe(TORRENTS_QUEUE_CHANGED.name), bus.subscribe(TORRENTS_PROGRESS_CHANGED.name)

        async def done(label: str) -> None:
            """What a command left: the work it began has ended, then the tables of the modules and the packets told."""
            await quiet(bus, planner, modules)
            answers[f"{label}:torrents"] = torrents_digest(path)
            told(label)

        def drain(*subs) -> None:
            for sub in subs:
                while not sub.queue.empty():
                    sub.queue.get_nowait()

        async def until(state: str, wanted=lambda each: True) -> dict:
            """The next news of a release in `state` that `wanted` accepts; the news before it are passed by."""
            async def wait():
                while True:
                    each = (await news.queue.get()).payload
                    if each["state"] == state and wanted(each):
                        return each
            return await asyncio.wait_for(wait(), 60)

        def lists(files, *, published: str, link: str, hold: bool = False) -> str:
            """The page lists a release whose files the swarm seeds; returns its infohash."""
            meta, infohash = qbit.seed(files, hold=hold)
            pages.files[link] = meta
            pages.page = [PageRelease(release="page", title="1-12", link=link, published=published)]
            return infohash

        async def window_connects(label: str) -> None:
            """A window connects: the picture of the queue and of the monitor come again, as the owner tells them."""
            drain(queue_sub, progress_sub)
            probe.publish_event(GATEWAY_SSE_CLIENTS, {"count": 1})
            record(f"{label}:queue", 200, written((await asyncio.wait_for(queue_sub.queue.get(), 60)).payload).encode())
            record(f"{label}:progress", 200, written((await asyncio.wait_for(progress_sub.queue.get(), 60)).payload).encode())

        async def releases_of(label: str, url: str) -> None:
            await over_bus(f"{label}:bus", TORRENTS_RELEASES_GET, {"url": url})
            await ask(client, f"{label}:http", "POST", "/api/torrent/releases", json={"url": url})

        async def files_of(label: str, *roots: str) -> None:
            """The files a folder holds, by path with their sizes: what the library or the staging folder has after a command."""
            found = sorted((os.path.relpath(os.path.join(top, name), folder), os.path.getsize(os.path.join(top, name)))
                           for each in roots for top, _dirs, names in os.walk(each) for name in names)
            record(label, 200, masked(json.dumps(found)).encode())

        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        urls = [row[0] for row in conn.execute("SELECT DISTINCT url FROM torrent_releases ORDER BY url")]
        conn.close()
        answers["torrents:start"] = torrents_digest(path)
        told("torrents:start")

        # --- what the base holds: the releases of every page, the monitor, the queue ------------------------------------------
        for url in urls:
            await releases_of(f"torrents:releases:{url}", url)
        await releases_of("torrents:releases:unknown", "https://kinozal.tv/details.php?id=1")
        await over_bus("torrents:monitor:bus", TORRENTS_DB_PROGRESS_LIST, {})
        await ask(client, "torrents:monitor:http", "GET", "/api/series/active_torrents")
        await over_bus("torrents:queue:bus", TORRENTS_QUEUE_GET, {})
        await ask(client, "torrents:queue:http", "GET", "/api/agent/queue")
        await window_connects("torrents:connect:start")

        # --- the series of the run: one with a torrent page for a source, two to lay files in a folder and to hold the old registry ---
        draft = {"name": "Торрент", "name_en": "Torrent", "site": "kinozal", "season": "1", "quality": "1080p", "auto_scan_enabled": False}
        sid_page = (await ask(client, "torrents:series:page", "POST", "/api/series", json={
            **draft, "save_path": os.path.join(shelf, "page"), "sources": [{"url": page_a, "source_type": "torrent"}]})).json()["series_id"]
        sid_move = (await ask(client, "torrents:series:move", "POST", "/api/series", json={
            **draft, "name": "Перенос", "name_en": "Move", "save_path": os.path.join(shelf, "old"),
            "sources": [{"url": "https://proto.invalid/move", "source_type": "web_video"}]})).json()["series_id"]
        sid_old = (await ask(client, "torrents:series:old", "POST", "/api/series", json={
            **draft, "name": "Старый", "name_en": "Old", "save_path": os.path.join(shelf, "kept"),
            "sources": [{"url": "https://proto.invalid/old", "source_type": "web_video"}]})).json()["series_id"]
        await done("torrents:series")

        # --- the folder the releases download into: read, refused in every way, set; a download is refused until it is set -------------
        await ask(client, "torrents:staging:get:unset", "GET", "/api/settings/torrent_staging")
        await over_bus("torrents:staging:bus:get:unset", TORRENTS_STAGING_GET, {})
        for label, body in (("relative", {"value": "relative/dir"}), ("empty", {"value": ""}), ("none", {"value": None}),
                            ("missing", {}), ("number", {"value": 123}), ("spaces", {"value": "   "})):
            await ask(client, f"torrents:staging:set:{label}", "POST", "/api/settings/torrent_staging", json=body)
        await over_bus("torrents:staging:bus:refused", TORRENTS_STAGING_SET, {"value": "rel"}, drop_none=True)
        await done("torrents:staging:refused")
        await ask(client, "torrents:download:no-staging", "POST", "/api/torrent/download", json={"url": page_a, "release": "page"})
        await ask(client, "torrents:staging:set", "POST", "/api/settings/torrent_staging", json={"value": staging})
        await done("torrents:staging:set")
        await ask(client, "torrents:staging:get", "GET", "/api/settings/torrent_staging")
        await over_bus("torrents:staging:bus:get", TORRENTS_STAGING_GET, {})
        await over_bus("torrents:staging:bus:set", TORRENTS_STAGING_SET, {"value": f" {staging} "}, drop_none=True)
        await done("torrents:staging:set-again")

        # --- a download: refused in the ways it is refused, made, followed to its end ------------------------------------------------
        await ask(client, "torrents:download:not-on-page", "POST", "/api/torrent/download", json={"url": page_a, "release": "nope"})
        pages.page = [PageRelease(release="page", title="1-12", published="d0")]
        await ask(client, "torrents:download:no-torrent", "POST", "/api/torrent/download", json={"url": page_a, "release": "page"})
        h1 = lists(release_of(V1), published="d1", link="L1", hold=True)
        drain(news)
        await ask(client, "torrents:download", "POST", "/api/torrent/download", json={"url": page_a, "release": "page"})
        await until("downloading", lambda each: bool(each["files"]))
        await done("torrents:download")
        await releases_of("torrents:download:releases", page_a)
        await ask(client, "torrents:download:again", "POST", "/api/torrent/download", json={"url": page_a, "release": "page"})
        qbit.deliver(h1)
        first = await until("ready")
        await done("torrents:ready")
        await releases_of("torrents:ready:releases", page_a)
        await files_of("torrents:ready:files", first["folder"])
        await window_connects("torrents:connect:ready")

        # --- the page, read now: nothing newer; the automatic update off and on; an update into the same folder -------------------------
        await ask(client, "torrents:check:same", "POST", "/api/torrent/check", json={"url": page_a})
        await done("torrents:check:same")
        for label, watched in (("off", False), ("on", True)):
            await ask(client, f"torrents:watch:{label}", "POST", "/api/torrent/watch", json={"url": page_a, "release": "page", "watched": watched})
            await done(f"torrents:watch:{label}")
        await ask(client, "torrents:watch:unknown", "POST", "/api/torrent/watch", json={"url": page_a, "release": "nope", "watched": True})
        h2 = lists(release_of(V2), published="d2", link="L2")
        drain(news)
        await ask(client, "torrents:check:newer", "POST", "/api/torrent/check", json={"url": page_a})
        await until("updating")
        second = await until("ready", lambda each: each["infohash"] == h2)
        await done("torrents:update")
        await releases_of("torrents:update:releases", page_a)
        await files_of("torrents:update:files", second["folder"])

        # --- an update held by a sync of a series with the page: it waits for the end of the run, then begins ---------------------------
        probe.publish_event(SCAN_RUN_CHANGED, {"series_id": sid_page, "active": True, "seq": 1})
        await quiet(bus, planner, modules)
        h3 = lists(release_of(V1), published="d3", link="L3")
        drain(news)
        await ask(client, "torrents:held:check", "POST", "/api/torrent/check", json={"url": page_a})
        await until("ready", lambda each: each["pending"])
        await done("torrents:held")
        await releases_of("torrents:held:releases", page_a)
        client_free["on"] = True  # the update takes the infohash the release had before, whose client word the module still holds
        probe.publish_event(SCAN_RUN_CHANGED, {"series_id": sid_page, "active": False, "seq": 2})
        third = await until("ready", lambda each: each["infohash"] == h3 and not each["pending"])
        await done("torrents:held:over")
        client_free["on"] = False
        await files_of("torrents:held:files", third["folder"])

        # --- a release no longer watched gives its files up; taken again it is a new download -----------------------------------------
        await ask(client, "torrents:watch:off-again", "POST", "/api/torrent/watch", json={"url": page_a, "release": "page", "watched": False})
        await ask(client, "torrents:check:unwatched", "POST", "/api/torrent/check", json={"url": page_a})
        drain(news)
        probe.send_command(TORRENTS_RELEASE_DROP_FILES, {"url": page_a, "release": "page"})
        await until("archived")
        await done("torrents:archived")
        await releases_of("torrents:archived:releases", page_a)
        await files_of("torrents:archived:files", staging)
        drain(news)
        client_free["on"] = True
        reply = await client.request("POST", "/api/torrent/download", json={"url": page_a, "release": "page"})
        taken = json.loads(reply.content)
        taken["client"] = None
        record("torrents:download:archived", reply.status_code, json.dumps(taken, ensure_ascii=False, separators=(",", ":")).encode())
        await until("ready")
        await done("torrents:download:archived")
        client_free["on"] = False
        await releases_of("torrents:download:archived:releases", page_a)

        # --- a release the client lost, and the folder of the releases moved -----------------------------------------------------------
        other = [("Other/E01.mkv", content_of("o1", 300_000)), ("Other/E02.mkv", content_of("o2", 300_000))]
        h4 = lists(other, published="e1", link="L4", hold=True)
        drain(news)
        await ask(client, "torrents:download:second-page", "POST", "/api/torrent/download", json={"url": page_b, "release": "page"})
        await until("downloading", lambda each: bool(each["files"]))
        qbit.torrents.pop(h4)
        await until("failed")
        await done("torrents:lost")
        await releases_of("torrents:lost:releases", page_b)
        moved = os.path.join(folder, "staging-moved")
        drain(news)
        await ask(client, "torrents:staging:move", "POST", "/api/settings/torrent_staging", json={"value": moved})
        await done("torrents:staging:move")
        await releases_of("torrents:staging:move:releases", page_a)
        await over_bus("torrents:staging:bus:get:moved", TORRENTS_STAGING_GET, {})
        await window_connects("torrents:connect:moved")

        # --- the registry of the old pipeline, read: the rows are the run's own (a torrent, its files, its progress) -------------------------
        rows = sqlite3.connect(path)
        rows.execute("INSERT INTO torrents (series_id, torrent_id, link, date_time, quality, episodes, is_active, qb_hash) "
                     "VALUES (?, 't1', 'https://proto.invalid/t1', '2026-01-01', '1080p', '1-12', 1, 'abc123')", (sid_old,))
        owner = rows.execute("SELECT id FROM torrents WHERE qb_hash = 'abc123'").fetchone()[0]
        for original, renamed, state, meta in (
                ("Old/E01.mkv", "Season 01/E01.mkv", "renamed", {"season": 1, "episode": 1, "media_type": "video"}),
                ("Old/E02.mkv", "Season 01/E02.mkv", "renamed", {"season": 1, "episode": 2, "media_type": "video"}),
                ("Old/E02.mka", None, "pending_rename", {"season": 1, "episode": 2, "media_type": "audio"}),
                ("Old/Extras/x.txt", None, "pending_rename", {"is_extra": True, "media_type": "extra"})):
            rows.execute("INSERT INTO torrent_files (torrent_db_id, original_path, renamed_path, status, extracted_metadata, is_excluded, "
                         "exclude_reason, qb_file_index) VALUES (?, ?, ?, ?, ?, 0, NULL, 0)", (owner, original, renamed, state, json.dumps(meta)))
        rows.execute("INSERT INTO torrent_progress (qb_hash, series_id, status, progress, dlspeed, eta, error_message, created_at, updated_at) "
                     "VALUES ('abc123', ?, 'downloading', 55, 1024, 60, NULL, '2026-11-21 04:00:00.000000', '2026-11-21 04:00:00.000000')",
                     (sid_old,))
        rows.commit()
        rows.close()
        for label, topic, body in (("counts", TORRENTS_DB_DOWNLOADED_COUNTS, {}), ("for-series", TORRENTS_DB_FILES_FOR_SERIES, {"series_id": sid_old}),
                                   ("files", TORRENTS_DB_FILES_LIST, {"qb_hash": "abc123"}), ("files:unknown", TORRENTS_DB_FILES_LIST, {"qb_hash": "no"}),
                                   ("history", TORRENTS_DB_HISTORY, {"series_id": sid_old}), ("seasons", TORRENTS_SEASONS, {"series_id": sid_old}),
                                   ("composition", TORRENTS_COMPOSITION, {"series_id": sid_old}), ("active", TORRENTS_DB_ACTIVE, {"series_id": sid_old}),
                                   ("info", TORRENTS_INFO_GET, {}), ("client-files", TORRENTS_FILES_GET, {"hash": "abc123"}),
                                   ("monitor", TORRENTS_DB_PROGRESS_LIST, {}), ("queue", TORRENTS_QUEUE_GET, {})):
            await over_bus(f"torrents:old:{label}", topic, body)
        await ask(client, "torrents:old:monitor:http", "GET", "/api/series/active_torrents")
        await ask(client, "torrents:old:filenames", "GET", f"/api/series/{sid_old}/source-filenames")
        await ask(client, "torrents:old:list", "GET", "/api/series")
        await window_connects("torrents:connect:old")
        await done("torrents:old")

        # --- the series with its page deleted: the releases of its page leave the client with their files and the folder ---------------
        drain(news)
        await ask(client, "torrents:delete:page-series", "DELETE", f"/api/series/{sid_page}")
        await done("torrents:delete:page-series")
        await releases_of("torrents:delete:releases", page_a)
        await files_of("torrents:delete:files", staging, moved)
        await ask(client, "torrents:delete:old-series", "DELETE", f"/api/series/{sid_old}")
        await done("torrents:delete:old-series")

        # --- the library: the folders of the disk listed, the move of a series planned, refused, failed and made ---------------------------
        for each in ("b_dir", "A_dir", "c_dir/inner"):
            os.makedirs(os.path.join(shelf, each), exist_ok=True)
        open(os.path.join(shelf, "file.txt"), "w").close()
        for label, query in (("shelf", shelf), ("dotted", os.path.join(shelf, "b_dir", "..", "A_dir")), ("file", os.path.join(shelf, "file.txt")),
                             ("missing", os.path.join(shelf, "none")), ("outside", "/etc"), ("root", "/"), ("staging", staging)):
            await ask(client, f"library:directories:{label}", "GET", "/api/directories", params={"path": query})
        await ask(client, "library:directories:default", "GET", "/api/directories")
        await over_bus("library:directories:bus", LIBRARY_DIRECTORIES_LIST, {"path": shelf})
        await over_bus("library:directories:bus:default", LIBRARY_DIRECTORIES_LIST, {})
        old, fresh = os.path.join(shelf, "old"), os.path.join(shelf, "fresh")
        names = ["Show/S01E01.mkv", "Show/S01E02.mkv", "Show/Extras/x.mka"]
        for each in names:
            os.makedirs(os.path.dirname(os.path.join(old, each)), exist_ok=True)
            with open(os.path.join(old, each), "wb") as sink:
                sink.write(each.encode() * 7)
        pages.names = [*names, "Show/gone.mkv"]  # a file of the plan that is not on the disk is skipped
        card = f"/api/series/{sid_move}"
        await ask(client, "library:relocation:get:none", "GET", card)
        await over_bus("library:relocation:bus:get:none", LIBRARY_RELOCATION_GET, {"series_id": sid_move})
        await ask(client, "library:relocation:plan", "POST", card, json={"save_path": fresh})
        await done("library:relocation:plan")
        await ask(client, "library:relocation:card", "GET", card)
        await over_bus("library:relocation:bus:get", LIBRARY_RELOCATION_GET, {"series_id": sid_move})
        await ask(client, "library:relocation:replan", "POST", card, json={"save_path": fresh + "-b"})
        await done("library:relocation:replan")
        await over_bus("library:relocation:bus:get:replanned", LIBRARY_RELOCATION_GET, {"series_id": sid_move})
        await ask(client, "library:relocation:drop", "POST", card, json={"save_path": old})
        await done("library:relocation:drop")
        await over_bus("library:relocation:bus:get:dropped", LIBRARY_RELOCATION_GET, {"series_id": sid_move})
        for label, new_path in (("relative", "relative/dir"), ("outside", "/nowhere/else"), ("trimmed", f" {fresh} "), ("unsafe", fresh + ":x?")):
            await over_bus(f"library:relocation:bus:plan:{label}", LIBRARY_RELOCATION_PLAN, {"series_id": sid_move, "new_path": new_path})
            await done(f"library:relocation:plan:{label}")
        await over_bus("library:relocation:bus:plan:unknown", LIBRARY_RELOCATION_PLAN, {"series_id": 999999, "new_path": fresh})
        await over_bus("library:relocation:bus:get:unknown", LIBRARY_RELOCATION_GET, {"series_id": 999999})
        await over_bus("library:relocation:bus:apply:unplanned", LIBRARY_RELOCATION_APPLY, {"series_id": sid_old})
        await over_bus("library:relocation:bus:apply:unplanned-unknown", LIBRARY_RELOCATION_APPLY, {"series_id": 999999})
        await over_bus("library:relocation:bus:plan:again", LIBRARY_RELOCATION_PLAN, {"series_id": sid_move, "new_path": fresh})
        pages.move_fails = True
        await over_bus("library:relocation:bus:apply:fails", LIBRARY_RELOCATION_APPLY, {"series_id": sid_move})
        await done("library:relocation:apply:fails")
        await ask(client, "library:relocation:card:after-fail", "GET", card)
        await files_of("library:relocation:files:after-fail", shelf)
        pages.move_fails = False
        await over_bus("library:relocation:bus:apply", LIBRARY_RELOCATION_APPLY, {"series_id": sid_move})
        await done("library:relocation:apply")
        await ask(client, "library:relocation:card:after", "GET", card)
        await over_bus("library:relocation:bus:get:after", LIBRARY_RELOCATION_GET, {"series_id": sid_move})
        await files_of("library:relocation:files:after", shelf)
        await over_bus("library:relocation:bus:apply:again", LIBRARY_RELOCATION_APPLY, {"series_id": sid_move})
        await ask(client, "library:relocation:delete", "DELETE", card)
        await done("library:relocation:delete")
        await over_bus("library:relocation:bus:get:deleted", LIBRARY_RELOCATION_GET, {"series_id": sid_move})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway.app), base_url="http://test",
                                 timeout=120) as client:
        for sid in series:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            sources = conn.execute("SELECT id, url, parser_profile_id FROM series_sources WHERE series_id=? ORDER BY id",
                                   (sid,)).fetchall()
            conn.close()
            answers[f"{sid}:tables:start"] = tables_digest(path, sid)
            cards[sid] = plain(await probe.request(CATALOG_SERIES_GET, {"series_id": sid}))
            await plan_window(client, f"{sid}:start", sid)
            await ask(client, f"{sid}:plan:unknown-series", "GET", "/api/series/999999/plan")
            await ask(client, f"{sid}:output:unknown-series", "GET", "/api/series/999999/output")
            await ask(client, f"{sid}:pack:queue", "GET", "/api/pack/queue")
            for topic, payload in ((PLANNER_SERIES_GET, {"series_id": sid}), (PLANNER_REFERENCES_GET, {"series_id": sid}),
                                   (PLANNER_WORK_GET, {"series_id": sid}), (PLANNER_FILES_GET, {"series_id": sid}),
                                   (PLANNER_SEASONS, {"series_id": sid}), (PLANNER_DOWNLOADED_COUNTS, {}),
                                   (PLANNER_PACK_GET, {})):
                await over_bus(f"{sid}:bus:{topic.name}", topic, payload)
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
        if torrents_run:
            await torrents_window(client)
    untap()
    await runner.stop()
    if served is not None:
        served.close()
    with open(out, "w", encoding="utf-8") as sink:
        json.dump({"root": root, "when": time.strftime("%Y-%m-%d %H:%M:%S"), "answers": answers}, sink)
    shutil.rmtree(folder, ignore_errors=True)
    print(f"COLLECTED {len(answers)} answers; bytes {sum(each.get('length', 0) for each in answers.values())}")
    print("WINDOW_JSON_DONE")


# The tables the executors own, and the settings they keep: hashed whole after each command of their run.
EXECUTORS_OWNED = ("download_cards", "download_done", "scan_tasks")
EXECUTORS_SETTINGS = ("scanner_agent_enabled", "schedule", "next_scan_timestamp", "download_threads_total", "download_threads_per_file",
                      "download_single_at_once", "download_provider_at_once")
# What the executors tell the window as events in their run (the table SSE_MAP names those it has; the rest is told as the bus has it).
WATCHED_EXECUTORS = ("downloads.queue.changed", "downloads.fetch.started", "downloads.fetch.finished", "conform.queue.changed",
                     "conform.settings.changed", "conform.daemon.up", "conform.job.finished", "conform.job.removed",
                     "scan.status.changed", "scan.run.changed", "work.series.state", "series.status.contribution",
                     "series.busy.contribution")
# The pictures of a whole queue come as often as the machine works (every beat of a download, every step of the service): which of
# the packets between the first and the last were told does not depend on the code, so the last one stands for them.
VOLATILE_EXECUTORS = ("downloads.queue.changed", "conform.queue.changed", "work.series.state", "series.status.contribution",
                      "series.busy.contribution")
# What differs on every run whatever the code: the seconds a stage took, the moments of the clock, the numbers of the daemon's clock.
_EXECUTORS_MASKS = [
    (re.compile(r'((?:\\)?"(?:seconds|elapsed_s|stage_pct|progress|eta|speed|speed_x|done_mb|total_mb|dlspeed|position|running|limit)'
                r'(?:\\)?":\s*)(?:[0-9.eE+-]+|null)'), r"\1<n>"),
    (re.compile(r'((?:\\)?"(?:queued_at|finished_at|created_at|updated_at)(?:\\)?":\s*)(?:\\)?"[^"]*(?:\\)?"'), r'\1"<time>"'),
    (re.compile(r'("seq":\s*)\d+'), r"\1<n>"),
    (re.compile(r"127\.0\.0\.1:\d+"), "127.0.0.1:<port>"),
    (re.compile(r"0x[0-9a-fA-F]{6,}"), "0x<address>"),
    # The key a plan gives a job (and the folder named by it) is made of the paths of the run, whose folder is random.
    (re.compile(r"(?<![0-9a-f])[0-9a-f]{12}(?![0-9a-f])"), "<key>"),
    # The state of the work of an executor named the episodes it worked on (`busy`) in the tree before stage 5g; nobody read it, and it was
    # always empty: the field is gone, and what remains is compared.
    (re.compile(r',\s*"busy":\s*\[\]'), "")]


def executors_digest(path: str) -> dict:
    """The tables the executors own and their settings, each whole: rows and a hash; the ignored seasons and episodes of the series."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    queries = {table: f"SELECT * FROM {table}" for table in EXECUTORS_OWNED}
    # What a file was when its fetch ended (the time of its change, the moment of the record) is the clock of the run.
    queries["download_done"] = "SELECT path, url, max_height FROM download_done"
    queries["settings"] = "SELECT key, value FROM settings WHERE key IN (%s)" % ",".join(repr(each) for each in EXECUTORS_SETTINGS) \
        + " OR key LIKE 'conform.%'"
    queries["series:ignored"] = "SELECT id, ignored_seasons, ignored_episodes FROM series"
    for name, query in queries.items():
        rows = sorted(conn.execute(query).fetchall(), key=repr)
        out[name] = {"rows": len(rows), "sha": hashlib.sha256(masked(repr(rows)).encode()).hexdigest()}
    conn.close()
    return out


async def collect_executors(root: str, out: str) -> None:
    """The windows of `downloads`, `conform` and `scan` on the stand of the whole application (tests/app_stand.py): the user's way through
    the API, every module real, the site, the player and the alignment service as models, no network. Every step ends on the event of the
    work it began (the end of a run, the end of a job), never on a pause."""
    from core.topics import (CONFORM_DAEMON_UP, CONFORM_JOB_GET, CONFORM_JOB_KEYS, CONFORM_WORKSPACE, DOWNLOADS_QUEUE_GET,
                            SCAN_RUNS_GET, SCAN_RUN_CHANGED, SCAN_STATUS_GET)
    sys.path.insert(0, os.path.join(root, "tests"))
    from app_stand import STAND_LIMIT_S, AppStand
    from core import notices
    from core.module import BaseModule, BusRequestError
    from modules.gateway.module import SSE_MAP

    class Probe(BaseModule):
        name = "probe"

    # The files of the stand are seconds long: the shortest an episode may be is of the scale of the stand, as the tests set it.
    notices.EPISODE_MIN_S = 0.5

    _RUN_MASKS[:] = list(_EXECUTORS_MASKS)
    folder = tempfile.mkdtemp(prefix="window_json_")
    _RUN_MASKS.append((re.compile(re.escape(folder)), "<tmp>"))
    _RUN_MASKS.append((re.compile(r"window_j\.\.\.\w+(?=/)"), "window_j...<tmp>"))
    stand = AppStand(folder)
    await stand.start()
    bus, api = stand.bus, stand.api
    answers: dict[str, dict] = {}
    events: list[tuple[str, str]] = []

    def seen(env) -> None:
        if env.topic in WATCHED_EXECUTORS:
            payload = sse_packet(SSE_MAP, env.topic, env.payload)
            events.append((env.topic, masked(json.dumps(payload, ensure_ascii=False, default=plain))))

    untap = bus.tap(seen)

    def record(name: str, status: int, body: bytes) -> None:
        body = masked(body.decode("utf-8")).encode("utf-8")
        answers[name] = {"status": status, "length": len(body), "sha": hashlib.sha256(body).hexdigest(),
                         "body": base64.b64encode(body[:65536]).decode()}

    async def ask(name: str, method: str, url: str, **kwargs):
        response = await api.request(method, url, **kwargs)
        record(name, response.status_code, response.content)
        return response

    async def over_bus(name: str, topic, payload: dict) -> None:
        """What a module answers another, written as text; the fields a model leaves none are left out on both sides."""
        try:
            record(name, 200, written(await probe.request(topic, payload), True).encode())
        except BusRequestError as exc:
            record(name, 500, str(exc).encode())

    def told(label: str) -> None:
        taken = list(events)
        events.clear()
        by_topic: dict[str, list[str]] = {}
        for topic, text in taken:
            by_topic.setdefault(topic, []).append(hashlib.sha256(text.encode()).hexdigest())
        # The pictures of the queues land in the step that was under way when the machine got to them: they are told in the steps that run work.
        answers[f"events:{label}"] = {"events": {
            topic: {"count": len(each), "distinct": [each[-1]] if topic in VOLATILE_EXECUTORS else sorted(set(each)), "last": each[-1]}
            for topic, each in sorted(by_topic.items())
            if topic not in VOLATILE_EXECUTORS or label.split(":")[0] in ("sync", "stop", "reprocess")}}

    async def after(label: str) -> None:
        """What a command left: nothing is under way in the modules, then their tables and the packets told."""
        await stand.bus.settled()
        # The scan keeps its scheduler among its tasks in the tree before the stage 5g (a loop as long as its life): it is no work to wait for.
        for module in stand.modules:
            under_way = [task for task in module.background() if not task.done()] if module.name != "scan" else []
            if under_way:
                await asyncio.gather(*under_way, return_exceptions=True)
        await stand.bus.settled()
        answers[f"{label}:tables"] = executors_digest(stand.db_path)
        told(label)

    async def run_ended(ended, series_id: int) -> None:
        await stand._run_end(ended, series_id)

    async def windows(label: str) -> None:
        for name, url in (("scanner", "/api/scanner/status"), ("queue", "/api/downloads/queue"), ("limits", "/api/settings/download_limits"),
                          ("conform", "/api/conform/settings")):
            await ask(f"{label}:{name}", "GET", url)

    probe = Probe(bus)
    await probe.start()

    # --- the settings of the service of the alignment, the limits of the downloads, the schedule ------------------------------------
    await windows("start")
    told("start")
    answers["start:tables"] = executors_digest(stand.db_path)
    work = os.path.join(folder, "work")
    connected = bus.subscribe(CONFORM_DAEMON_UP.name)
    await ask("conform:settings:refused", "PUT", "/api/conform/settings", json={"url": "ftp://x", "work_dir": "rel", "work_dir_remote": ""})
    await ask("conform:settings:set", "PUT", "/api/conform/settings",
              json={"url": stand.daemon_http.url, "work_dir": work, "work_dir_remote": work})
    await asyncio.wait_for(connected.queue.get(), STAND_LIMIT_S)
    await after("conform:settings")
    await ask("conform:params:refused", "PUT", "/api/conform/params",
              json={"audio_method": "dsp", "drift_speed_pct": 11.0, "keep_tmp": False, "fill_silence": True, "extend_edges": True})
    await ask("conform:params:set", "PUT", "/api/conform/params",
              json={"audio_method": "band", "drift_speed_pct": 2.5, "keep_tmp": True, "fill_silence": True, "extend_edges": False})
    await ask("conform:view", "GET", "/api/conform/settings")
    await after("conform:params")
    for label, body in (("set", {"single_at_once": "3"}), ("refused", {"threads_total": "0"}), ("none", {}),
                        ("back", {"single_at_once": 4})):
        await ask(f"limits:{label}", "POST", "/api/settings/download_limits", json=body)
        await ask(f"limits:{label}:get", "GET", "/api/settings/download_limits")
        await after(f"limits:{label}")
    schedule = {"syncs": ["03:00", "15:00"], "torrent_mode": "between", "torrent_free": [], "torrent_between": 2}
    for label, body in (("set", {"schedule": schedule}), ("free", {"schedule": {**schedule, "torrent_mode": "free",
                                                                                  "torrent_free": ["09:30"]}}),
                        ("refused", {"schedule": {**schedule, "torrent_mode": "no such"}}), ("empty", {"schedule": {"syncs": []}})):
        await ask(f"schedule:{label}", "POST", "/api/schedule", json=body)
        await after(f"schedule:{label}")  # the scan counts its next sync once the schedule is told: the status is read after that
        await ask(f"schedule:{label}:status", "GET", "/api/scanner/status")
    for label, enabled in (("on", True), ("off", False)):
        await ask(f"scanner:{label}", "POST", "/api/scanner/settings", json={"enabled": enabled})
        await after(f"scanner:{label}")
        await ask(f"scanner:{label}:status", "GET", "/api/scanner/status")
    await ask("scan_all", "POST", "/api/scanner/scan_all")
    await after("scan_all")

    # --- the voices, the site, a series of two sources, a sync to its end ----------------------------------------------------------
    stand.site.release("ref", "AniStar")
    stand.site.release("dub", "AnimeVost")
    stand.site.episode("ref", 1, height=720)
    stand.site.episode("dub", 1, height=480)
    saved = await api.put("/api/voices/settings", json={"priority": ["vk", "cvh", "allvideo", "kodik"],
                                                         "voice_groups": {"AniStar": [], "AnimeVost": []},
                                                         "voice_languages": {"AniStar": "rus", "AnimeVost": "rus"}})
    assert saved.json()["saved"], saved.text
    series_id = await stand.series("Сериал", ["ref", "dub"])
    ended = bus.subscribe(SCAN_RUN_CHANGED.name)
    response = await api.post(f"/api/series/{series_id}/scan")
    record("sync:first", response.status_code, response.content)
    await run_ended(ended, series_id)
    await after("sync:first")
    await windows("sync:first")
    await ask("sync:first:plan", "GET", f"/api/series/{series_id}/plan")
    await over_bus("sync:first:runs", SCAN_RUNS_GET, {})
    await over_bus("sync:first:status", SCAN_STATUS_GET, {})
    await over_bus("sync:first:queue", DOWNLOADS_QUEUE_GET, {})
    await over_bus("sync:first:workspace", CONFORM_WORKSPACE, {})
    await over_bus("sync:first:keys", CONFORM_JOB_KEYS, {})
    jobs = sorted(stand.daemon.jobs)
    for label, job_id in (("kept", jobs[0] if jobs else "none"), ("missing", "no-such-job")):
        await over_bus(f"conform:job:{label}", CONFORM_JOB_GET, {"job_id": job_id})
    await ask("conform:plot:missing", "GET", f"/api/conform/jobs/{jobs[0] if jobs else 'x'}/plot/none.png")

    # --- the switch of the output and the run that fetches nothing -----------------------------------------------------------------
    switch = f"/api/series/{series_id}/output/switch"
    for label, body in (("off", {"season": 1, "episodes": [1], "off": True}), ("season-off", {"season": 1, "off": True}),
                        ("season-on", {"season": 1, "off": False})):
        await ask(f"switch:{label}", "PUT", switch, json=body)
        await after(f"switch:{label}")
    ended = bus.subscribe(SCAN_RUN_CHANGED.name)
    await ask("reprocess", "POST", f"/api/series/{series_id}/reprocess")
    await run_ended(ended, series_id)
    await after("reprocess")
    await ask("reprocess:plan", "GET", f"/api/series/{series_id}/plan")

    # --- a download that fails for good stays as a card until the user removes it; the stop of a sync while the service aligns ---------
    stand.site.release("ref2", "AniStar")
    stand.site.release("dub2", "AnimeVost")
    stand.site.episode("ref2", 1, height=720)
    stand.site.episode("dub2", 1, height=480)
    second = await stand.series("Второй", ["ref2", "dub2"])
    stand.site.gone("dub2", 1)
    ended = bus.subscribe(SCAN_RUN_CHANGED.name)
    response = await api.post(f"/api/series/{second}/scan")
    record("sync:failed", response.status_code, response.content)
    await run_ended(ended, second)
    await after("sync:failed")
    await ask("sync:failed:queue", "GET", "/api/downloads/queue")
    cards = (await api.get("/api/downloads/queue")).json()
    for label, card in (("missing", "no-such-card"), ("kept", cards[0]["id"] if cards else "none")):
        await ask(f"card:remove:{label}", "DELETE", f"/api/downloads/cards/{card}")
        await ask(f"card:remove:{label}:queue", "GET", "/api/downloads/queue")
        await after(f"card:remove:{label}")
    await ask("queue:clear", "POST", "/api/downloads/queue/clear")
    await ask("cancel:missing", "POST", "/api/downloads/7/cancel")
    stand.site.gone("dub2", 1, False)
    stand.site.release("ref3", "AniStar")
    stand.site.release("dub3", "AnimeVost")
    stand.site.episode("ref3", 1, height=720)
    stand.site.episode("dub3", 1, height=480)
    third = await stand.series("Третий", ["ref3", "dub3"])
    stand.daemon.hold = True
    stand.daemon.made.clear()
    ended = bus.subscribe(SCAN_RUN_CHANGED.name)
    running = asyncio.create_task(api.post(f"/api/series/{third}/scan"))
    await asyncio.wait_for(asyncio.to_thread(stand.daemon.made.wait), STAND_LIMIT_S)
    await ask("busy:scan", "POST", f"/api/series/{third}/scan")
    await ask("busy:switch", "PUT", f"/api/series/{third}/output/switch", json={"season": 1, "episodes": [1], "off": True})
    await ask("busy:settings", "PUT", f"/api/series/{third}/output/settings", json={"season": 1, "lever": "no.such", "value": 1})
    await ask("stop", "POST", f"/api/series/{third}/scan/cancel")
    stopped = await running
    record("stop:sync", stopped.status_code, stopped.content)
    await run_ended(ended, third)
    stand.daemon.hold = False
    stand.daemon.release()
    await after("stop")
    await ask("stop:again", "POST", f"/api/series/{third}/scan/cancel")
    await ask("stop:queue", "GET", "/api/downloads/queue")
    for label, job_id in (("missing", "no-such-job"), ("kept", sorted(stand.daemon.jobs)[0] if stand.daemon.jobs else "none")):
        await ask(f"conform:cancel:{label}", "DELETE", f"/api/conform/jobs/{job_id}")
    await after("conform:cancel")
    untap()
    await probe.stop()
    await stand.stop()
    stand.close()
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
    # The windows of `torrents`, `renaming` and `library` need no series either and put the model of qBittorrent (tests/qbit_model.py)
    # and the model of the neighbours in place of the tracker, the client and the plan: `collect --torrents` alone is a run of its own.
    parser.add_argument("--torrents", action="store_true")
    # The windows of `downloads`, `conform` and `scan` run on the stand of the whole application (tests/app_stand.py) with its own base:
    # `collect --executors` alone is a run of its own and needs no copy of the base.
    parser.add_argument("--executors", action="store_true")
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
    if args.sources or args.torrents or args.executors:
        # The model of the provider and the model of qBittorrent listen on this machine: it is the one address the proxy does not take.
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1"
    if args.executors:
        asyncio.run(collect_executors(root, args.out))
        sys.exit(0)
    asyncio.run(collect(root, args.db, args.out, [int(each) for each in args.rest], args.windows, args.owners, args.sources,
                        args.torrents))
