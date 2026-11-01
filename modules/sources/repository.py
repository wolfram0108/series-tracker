"""Репозиторий sources: таблица trackers (зеркала, типы аутентификации) и таблицы страниц сайтов.

Tables of pages: `release_tables` holds, per page, the head of the last complete table and the state of the read in
progress; `release_rows` holds the rows of that complete table. A new complete table replaces the rows in one
transaction.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

from core.contracts import ReleaseTable, RowState, RowVoice, TableRow
from core.db import Database

from . import hosts

# One statement is one snapshot: the head and the rows of a page cannot come from two different tables.
_LAST_TABLE = (
    'SELECT t.site, t.names, t.site_voice, t.read_at, t.complete, r.seq, r."key" AS row_key, r.player, r.player_label,'
    " r.grp, r.text, r.voice_name, r.voice_kind, r.heights, r.state AS row_state, r.reason AS row_reason,"
    " r.duration_s, r.published, r.link, r.site_fields"
    " FROM release_tables t LEFT JOIN release_rows r ON r.url = t.url WHERE t.url = ? ORDER BY r.seq")

_SAVE_HEAD = (
    "INSERT INTO release_tables (url, site, names, site_voice, read_at, complete, state, reason, reading_since)"
    " VALUES (?, ?, ?, ?, ?, 1, 'ready', NULL, NULL)"
    " ON CONFLICT (url) DO UPDATE SET site = excluded.site, names = excluded.names, site_voice = excluded.site_voice,"
    " read_at = excluded.read_at, complete = 1, state = 'ready', reason = NULL, reading_since = NULL")

_SAVE_ROW = (
    'INSERT INTO release_rows (url, seq, "key", player, player_label, grp, text, voice_name, voice_kind, heights,'
    " state, reason, duration_s, published, link, site_fields)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")


def _row_of(record: dict) -> TableRow:
    return TableRow(
        key=record["row_key"], player=record["player"], player_label=record["player_label"], group=record["grp"],
        text=record["text"], voice=RowVoice(name=record["voice_name"], kind=record["voice_kind"]),
        heights=json.loads(record["heights"]), state=RowState(record["row_state"]), reason=record["row_reason"],
        duration_s=record["duration_s"], published=record["published"], link=record["link"],
        site_fields=json.loads(record["site_fields"]))


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

    async def last_table(self, url: str) -> ReleaseTable | None:
        """The last complete table of the page; None while the page has never been read completely."""
        records = await self._db.fetch_all(_LAST_TABLE, (url,))
        if not records or not records[0]["complete"]:
            return None
        head = records[0]
        return ReleaseTable(
            url=url, site=head["site"], names=json.loads(head["names"]), site_voice=head["site_voice"],
            read_at=head["read_at"], complete=True,
            rows=[_row_of(record) for record in records if record["seq"] is not None])

    async def save_table(self, table: ReleaseTable) -> None:
        """Replace the page's table in one transaction: a reader sees the old table or the new one, never a part."""
        if not table.complete or not table.read_at:
            raise ValueError("only a complete table is stored")
        statements: list[tuple[str, tuple]] = [
            (_SAVE_HEAD, (table.url, table.site, json.dumps(table.names, ensure_ascii=False), table.site_voice,
                          table.read_at)),
            ("DELETE FROM release_rows WHERE url = ?", (table.url,))]
        for seq, row in enumerate(table.rows):
            statements.append((_SAVE_ROW, (
                table.url, seq, row.key, row.player, row.player_label, row.group, row.text, row.voice.name,
                row.voice.kind, json.dumps(row.heights), row.state.value, row.reason, row.duration_s, row.published,
                row.link, json.dumps(row.site_fields, ensure_ascii=False))))
        await self._db.execute_many(statements)

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

    async def pages_being_read(self) -> list[dict]:
        """Pages whose read was recorded as begun and never concluded: a stop interrupted it."""
        return await self._db.fetch_all(
            "SELECT url, site FROM release_tables WHERE state = 'reading' ORDER BY reading_since, url")
