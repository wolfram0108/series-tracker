"""Репозиторий sources: таблица trackers (зеркала, типы аутентификации) и таблицы страниц сайтов.

Tables of pages: `release_tables` holds, per page, the last complete table — its head (with the parts its read could not
get) and its rows as one field (`rows`, kept by columns: core.native) — and the state of the read in progress. A new complete
table replaces the record in one statement. `provider_manifest` holds the last manifest of the provider of the socket
(modules/sources/provider), by its address.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cached_property
from urllib.parse import urlparse

import orjson

from core import native
from core.contracts import ReleaseTable, StoredTable, rows_field
from core.contracts.provider import Manifest
from core.contracts.torrent import order_name
from core.db import Database

from . import hosts

# The head of the last complete table of a page and its rows are one record: a reader takes both of one table.
_LAST_TABLE = ("SELECT site, names, title, site_voice, channel, read_at, complete, unread, rows, state AS read_state,"
               " reason AS read_reason FROM release_tables WHERE url = ?")

# Every write of the record of a page takes the next number of the module (`_COUNT`, then `_NEXT_WRITE` in the same
# transaction): a page written, forgotten and written again never has a number it had, so a reader that kept the number
# of a page knows whether it was written since.
_COUNT = ("UPDATE release_writes SET last = last + 1", ())
_NEXT_WRITE = "(SELECT last FROM release_writes)"

_SAVE_HEAD = (
    "INSERT INTO release_tables (url, site, names, title, site_voice, channel, read_at, unread, rows, complete, state,"
    " reason, reading_since, writes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'ready', NULL, NULL, " + _NEXT_WRITE + ")"
    " ON CONFLICT (url) DO UPDATE SET site = excluded.site, names = excluded.names, title = excluded.title,"
    " site_voice = excluded.site_voice,"
    " channel = excluded.channel, unread = excluded.unread, rows = excluded.rows,"
    " read_at = excluded.read_at, complete = 1, state = 'ready', reason = NULL, reading_since = NULL,"
    " writes = excluded.writes")

@dataclass(frozen=True)
class Stored:
    """What the page has: the last complete table (None while there is none) and how the last read ended.
    `state` is `reading`, `ready` or `failed` (None for a page never asked for); `reason` is the failure of the last read.
    `parted` is False for a table stored before its rows named their parts: a part of it cannot keep its rows.

    The table is kept as it is stored: its head (`head`, every field but the rows) and its rows (`rows`, kept by columns).
    `record` gives it on as it is; `table` is the model, made once when a reader needs it."""
    head: dict | None
    rows: bytes | None
    state: str | None
    reason: str | None
    parted: bool = True

    @cached_property
    def table(self) -> ReleaseTable | None:
        record = self.record()
        return None if record is None else record.table()

    def record(self) -> StoredTable | None:
        """The table as it is given on: its head and its rows as they are stored."""
        return None if self.head is None else StoredTable(**self.head, rows=self.rows or rows_field([]))


def _stored(conn, url: str) -> Stored:
    head = conn.execute(_LAST_TABLE, (url,)).fetchone()
    if head is None:
        return Stored(None, None, None, None)
    table = {"url": url, "site": head["site"], "names": json.loads(head["names"]), "title": head["title"],
             "site_voice": head["site_voice"], "channel": head["channel"], "read_at": head["read_at"], "complete": True,
             "unread": json.loads(head["unread"] or "[]")} if head["complete"] else None
    return Stored(table, head["rows"] if table is not None else None, head["read_state"], head["read_reason"],
                  parted=head["unread"] is not None)


class SourcesRepository:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._cache: list[dict] | None = None
        # The writes of the record of every page made here, the only writer of the records: a reader that looked at a page
        # outside the gate of the reads tells by it whether the page was written since.
        self._versions: dict[str, int] = {}

    def version(self, url: str) -> int:
        """How many writes of the record of the page this process has made."""
        return self._versions.get(url, 0)

    def _wrote(self, *urls: str) -> None:
        for url in urls:
            self._versions[url] = self._versions.get(url, 0) + 1

    async def all_trackers(self) -> list[dict]:
        if self._cache is None:
            rows = await self._db.fetch_all("SELECT * FROM trackers")
            for row in rows:
                row["mirrors"] = json.loads(row["mirrors"] or "[]")
                row["ui_features"] = json.loads(row["ui_features"] or "{}")
            self._cache = rows
        return self._cache

    async def resolve(self, url: str) -> dict | None:
        """Определяет трекер по домену URL (префиксы доставки — hosts)."""
        domain = hosts.canonical_host(urlparse(url).netloc)
        for tracker in await self.all_trackers():
            if domain in tracker["mirrors"]:
                return tracker
        return None

    async def set_mirrors(self, tracker_id: int,
                          mirrors: list[str]) -> None:
        await self._db.execute(
            "UPDATE trackers SET mirrors=? WHERE id=?",
            (json.dumps(mirrors), tracker_id))
        self._cache = None  # список зеркал изменился

    # --- tables of site pages ----------------------------------------------------------

    async def stored(self, url: str) -> Stored:
        """The last complete table of the page and the end of the last read, from one snapshot of the database. The
        rows are made into the table on the thread of the query: a page holds thousands of them."""
        return await self._db.transaction(lambda conn: _stored(conn, url))

    async def last_table(self, url: str) -> ReleaseTable | None:
        """The last complete table of the page; None while the page has never been read completely."""
        return (await self.stored(url)).table

    async def save_table(self, table: ReleaseTable) -> None:
        """Replace the page's table in one transaction: a reader sees the old table or the new one, never a part."""
        if not table.complete or not table.read_at:
            raise ValueError("only a complete table is stored")
        # Two rows of one key are no table: a reader finds a row by its key.
        if len({row.key for row in table.rows}) != len(table.rows):
            raise ValueError("two rows of the table have one key")
        await self._db.execute_many([_COUNT, (_SAVE_HEAD, (
            table.url, table.site, json.dumps(table.names, ensure_ascii=False), table.title, table.site_voice, table.channel,
            table.read_at, json.dumps([each.model_dump(mode="json") for each in table.unread], ensure_ascii=False),
            rows_field(table.rows)))])
        self._wrote(table.url)

    async def writes(self) -> dict[str, int]:
        """The number of the last write of the record of every page."""
        return {row["url"]: row["writes"] for row in await self._db.fetch_all("SELECT url, writes FROM release_tables")}

    async def pages(self) -> list[dict]:
        """Every page the module holds a record of: its address, the end of its last complete table (none while it was
        never read completely) and how its last read stands."""
        return await self._db.fetch_all("SELECT url, read_at, state FROM release_tables")

    async def forget_pages(self, urls: list[str]) -> None:
        """The records of these pages go with their rows, in one transaction."""
        await self._db.execute_many([("DELETE FROM release_tables WHERE url = ?", (url,)) for url in urls])
        self._wrote(*urls)

    async def mark_reading(self, url: str, site: str, since: str) -> None:
        """Record that a read of the page began; the last complete table stays as it is."""
        await self._db.execute_many([_COUNT, (
            "INSERT INTO release_tables (url, site, state, reading_since, writes) VALUES (?, ?, 'reading', ?, "
            + _NEXT_WRITE + ") ON CONFLICT (url) DO UPDATE SET state = 'reading', reading_since = excluded.reading_since,"
            " reason = NULL, writes = excluded.writes",
            (url, site, since))])
        self._wrote(url)

    async def mark_failed(self, url: str, site: str, reason: str) -> None:
        """Record that the read failed; the last complete table stays as it is."""
        await self._db.execute_many([_COUNT, (
            "INSERT INTO release_tables (url, site, state, reason, writes) VALUES (?, ?, 'failed', ?, " + _NEXT_WRITE + ")"
            " ON CONFLICT (url) DO UPDATE SET state = 'failed', reason = excluded.reason, reading_since = NULL,"
            " writes = excluded.writes",
            (url, site, reason))])
        self._wrote(url)


    async def players_of_pages(self, urls: list[str]) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
        """Of the stored tables of these pages: how many pages of each site hold rows of each player ({(player, site):
        pages}), and the best height each player's rows were measured at ({player: height})."""
        if not urls:
            return {}, {}
        marks = ", ".join("?" for _ in urls)

        def read(conn) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
            # By the name of the order of sources: the releases of the torrent are one player there (order_name).
            held: dict[tuple[str, str], set[str]] = {}
            heights: dict[str, int] = {}
            for url, site, rows in conn.execute(
                    f"SELECT url, site, rows FROM release_tables WHERE complete = 1 AND url IN ({marks})", urls):
                for row in orjson.loads(native.table_json(rows)) if rows else ():
                    player = order_name(row["player"])
                    held.setdefault((player, site), set()).add(url)
                    height = max(row["heights"] or [0])
                    if height:
                        heights[player] = max(height, heights.get(player, 0))
            return {key: len(pages) for key, pages in held.items()}, heights

        return await self._db.transaction(read)

    async def pages_being_read(self) -> list[dict]:
        """Pages whose read was recorded as begun and never concluded: a stop interrupted it."""
        return await self._db.fetch_all(
            "SELECT url, site FROM release_tables WHERE state = 'reading' ORDER BY reading_since, url")

    # --- the manifest of the provider of the socket (modules/sources/provider) -----------------------------------------

    async def provider_manifest(self, url: str) -> Manifest | None:
        """The last manifest saved for the provider at this address; None when none was, or when what is stored no longer
        reads as a manifest (the provider tells it again)."""
        row = await self._db.fetch_one("SELECT body FROM provider_manifest WHERE url = ?", (url,))
        if row is None:
            return None
        try:
            return Manifest.model_validate_json(row["body"])
        except ValueError:
            return None

    async def provider_manifest_save(self, url: str, manifest: Manifest) -> None:
        await self._db.execute(
            "INSERT INTO provider_manifest (url, provider, revision, body, saved_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (url) DO UPDATE SET provider = excluded.provider, revision = excluded.revision,"
            " body = excluded.body, saved_at = excluded.saved_at",
            (url, manifest.provider.id, manifest.revision, manifest.model_dump_json(),
             datetime.now(timezone.utc).isoformat()))
