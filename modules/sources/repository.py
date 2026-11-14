"""Репозиторий sources: таблица trackers (зеркала, типы аутентификации) и таблицы страниц сайтов.

Tables of pages: `release_tables` holds, per page, the head of the last complete table (with the parts its read could
not get) and the state of the read in progress; `release_rows` holds the rows of that complete table. A new complete table
replaces the rows in one transaction. `share139_folders` holds the folders of the public shares of 139 Cloud the search of
that application found, each with the number that is its card.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from core.contracts import ReleaseTable, RowState, RowVoice, TableRow, UnreadPart
from core.db import Database

from . import hosts

# The cards of the folders the search of 139 Cloud found begin here: the cards of the shares the owner connected by hand
# (the profile) have small numbers of their own.
SHARE_CARDS_FROM = 1_000_000

# One statement is one snapshot: the head and the rows of a page cannot come from two different tables.
_LAST_TABLE = (
    'SELECT t.site, t.names, t.title, t.site_voice, t.channel, t.read_at, t.complete, t.unread, t.state AS read_state, t.reason AS read_reason, r.seq, r."key" AS row_key, r.player, r.player_label,'
    " r.grp, r.text, r.voice_name, r.voice_kind, r.heights, r.state AS row_state, r.reason AS row_reason,"
    " r.duration_s, r.published, r.link, r.video, r.site_fields, r.part"
    " FROM release_tables t LEFT JOIN release_rows r ON r.url = t.url WHERE t.url = ? ORDER BY r.seq")

_SAVE_HEAD = (
    "INSERT INTO release_tables (url, site, names, title, site_voice, channel, read_at, unread, complete, state, reason,"
    " reading_since) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'ready', NULL, NULL)"
    " ON CONFLICT (url) DO UPDATE SET site = excluded.site, names = excluded.names, title = excluded.title,"
    " site_voice = excluded.site_voice,"
    " channel = excluded.channel, unread = excluded.unread,"
    " read_at = excluded.read_at, complete = 1, state = 'ready', reason = NULL, reading_since = NULL")

_SAVE_ROW = (
    'INSERT INTO release_rows (url, seq, "key", player, player_label, grp, text, voice_name, voice_kind, heights,'
    " state, reason, duration_s, published, link, video, site_fields, part)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")


@dataclass(frozen=True)
class Stored:
    """What the page has: the last complete table (None while there is none) and how the last read ended.
    `state` is `reading`, `ready` or `failed` (None for a page never asked for); `reason` is the failure of the last read.
    `parted` is False for a table stored before its rows named their parts: a part of it cannot keep its rows."""
    table: ReleaseTable | None
    state: str | None
    reason: str | None
    parted: bool = True


def _row_of(record: dict) -> TableRow:
    return TableRow(
        key=record["row_key"], player=record["player"], player_label=record["player_label"], group=record["grp"],
        text=record["text"], voice=RowVoice(name=record["voice_name"], kind=record["voice_kind"]),
        heights=json.loads(record["heights"]), state=RowState(record["row_state"]), reason=record["row_reason"],
        duration_s=record["duration_s"], published=record["published"], link=record["link"], video=record["video"],
        site_fields=json.loads(record["site_fields"]), part=record["part"])


def _stored(conn, url: str) -> Stored:
    records = conn.execute(_LAST_TABLE, (url,)).fetchall()
    if not records:
        return Stored(None, None, None)
    head = records[0]
    table = ReleaseTable(
        url=url, site=head["site"], names=json.loads(head["names"]), title=head["title"], site_voice=head["site_voice"],
        channel=head["channel"],
        read_at=head["read_at"], complete=True,
        rows=[_row_of(record) for record in records if record["seq"] is not None],
        unread=[UnreadPart.model_validate(each) for each in json.loads(head["unread"] or "[]")]) if head["complete"] else None
    return Stored(table, head["read_state"], head["read_reason"], parted=head["unread"] is not None)


class SourcesRepository:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._cache: list[dict] | None = None

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
        statements: list[tuple[str, tuple]] = [
            (_SAVE_HEAD, (table.url, table.site, json.dumps(table.names, ensure_ascii=False), table.title, table.site_voice,
                          table.channel, table.read_at,
                          json.dumps([each.model_dump(mode="json") for each in table.unread], ensure_ascii=False))),
            ("DELETE FROM release_rows WHERE url = ?", (table.url,))]
        for seq, row in enumerate(table.rows):
            statements.append((_SAVE_ROW, (
                table.url, seq, row.key, row.player, row.player_label, row.group, row.text, row.voice.name,
                row.voice.kind, json.dumps(row.heights), row.state.value, row.reason, row.duration_s, row.published,
                row.link, row.video, json.dumps(row.site_fields, ensure_ascii=False), row.part)))
        await self._db.execute_many(statements)

    async def pages(self) -> list[dict]:
        """Every page the module holds a record of: its address, the end of its last complete table (none while it was
        never read completely) and how its last read stands."""
        return await self._db.fetch_all("SELECT url, read_at, state FROM release_tables")

    async def forget_pages(self, urls: list[str]) -> None:
        """The records of these pages go with their rows, in one transaction."""
        await self._db.execute_many([statement for url in urls for statement in (
            ("DELETE FROM release_rows WHERE url = ?", (url,)), ("DELETE FROM release_tables WHERE url = ?", (url,)))])

    async def mark_reading(self, url: str, site: str, since: str) -> None:
        """Record that a read of the page began; the last complete table stays as it is."""
        await self._db.execute(
            "INSERT INTO release_tables (url, site, state, reading_since) VALUES (?, ?, 'reading', ?)"
            " ON CONFLICT (url) DO UPDATE SET state = 'reading', reading_since = excluded.reading_since, reason = NULL",
            (url, site, since))

    async def mark_failed(self, url: str, site: str, reason: str) -> None:
        """Record that the read failed; the last complete table stays as it is."""
        await self._db.execute(
            "INSERT INTO release_tables (url, site, state, reason) VALUES (?, ?, 'failed', ?)"
            " ON CONFLICT (url) DO UPDATE SET state = 'failed', reason = excluded.reason, reading_since = NULL",
            (url, site, reason))


    async def players_of_pages(self, urls: list[str]) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
        """Of the stored tables of these pages: how many pages of each site hold rows of each player ({(player, site):
        pages}), and the best height each player's rows were measured at ({player: height})."""
        if not urls:
            return {}, {}
        marks = ", ".join("?" for _ in urls)

        def read(conn) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
            pages = {(player, site): count for player, site, count in conn.execute(
                f"SELECT r.player, t.site, COUNT(DISTINCT t.url) FROM release_rows r JOIN release_tables t ON t.url = r.url"
                f" WHERE t.url IN ({marks}) GROUP BY r.player, t.site", urls)}
            heights = {player: height for player, height in conn.execute(
                f"SELECT r.player, MAX(CAST(j.value AS INTEGER)) FROM release_rows r, json_each(r.heights) j"
                f" WHERE r.url IN ({marks}) GROUP BY r.player", urls) if height}
            return pages, heights

        return await self._db.transaction(read)

    async def pages_being_read(self) -> list[dict]:
        """Pages whose read was recorded as begun and never concluded: a stop interrupted it."""
        return await self._db.fetch_all(
            "SELECT url, site FROM release_tables WHERE state = 'reading' ORDER BY reading_since, url")

    # --- the folders of the public shares of 139 Cloud (apps/share139.py) -------------------------------------------

    async def share_folder_register(self, link_id: str, folder_id: str, *, password: str = "", title: str = "",
                                    publisher_url: str = "", published_at: str = "", found_by: str = "") -> int:
        """The card of a folder of a share: the number it has, or the next one when it is found for the first time. What is
        known of it again (a title, the code the post gives) replaces what was; an empty value does not. How it was found is
        how it was found first."""
        now = datetime.now(timezone.utc).isoformat()

        def write(conn) -> int:
            held = conn.execute("SELECT card, password, title, publisher_url, published_at, found_by FROM share139_folders"
                                " WHERE link_id = ? AND folder_id = ?", (link_id, folder_id)).fetchone()
            if held is not None:
                conn.execute("UPDATE share139_folders SET password = ?, title = ?, publisher_url = ?, published_at = ?,"
                             " found_by = ?, seen_at = ? WHERE card = ?",
                             (password or held["password"], title or held["title"], publisher_url or held["publisher_url"],
                              published_at or held["published_at"], held["found_by"] or found_by, now, held["card"]))
                return held["card"]
            card = conn.execute("SELECT COALESCE(MAX(card), ?) + 1 FROM share139_folders", (SHARE_CARDS_FROM - 1,)).fetchone()[0]
            conn.execute("INSERT INTO share139_folders (card, link_id, folder_id, password, title, publisher_url, published_at,"
                         " found_by, found_at, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (card, link_id, folder_id, password, title, publisher_url, published_at, found_by, now, now))
            return card

        return await self._db.transaction(write)

    async def share_folder(self, card: int) -> dict | None:
        return await self._db.fetch_one("SELECT * FROM share139_folders WHERE card = ?", (card,))

    async def share_folders(self) -> list[dict]:
        """Every folder found so far, the latest seen first."""
        return await self._db.fetch_all("SELECT * FROM share139_folders ORDER BY seen_at DESC, card DESC")
