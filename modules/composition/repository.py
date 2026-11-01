"""All SQL of the composition module: the layout of a series, its sources, the numbers of unknown voice-overs, the
verdicts of failed reads and the output settings.

A series is read and written whole and in one transaction, so a reader never sees rows of one revision with the
revision number of another.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from core.contracts import CompositionRow, SettingRecord, SourceSummary
from core.db import Database

_ROW_COLUMNS = ("series_id", "source_id", "row_key", "seq", "excluded", "exclusion", "season", "episode_start",
                "episode_end", "part", "voice_group", "voice_label", "voice_unknown", "player", "player_label", "grp",
                "text", "heights", "state", "published", "link")


@dataclass(frozen=True)
class StoredSource:
    """What the window shows about a source and the canonical address of its page (None before the first answer)."""

    source_id: int
    position: int
    table_url: str | None
    summary: SourceSummary


@dataclass
class Stored:
    """A series as the module last wrote it; a series never counted has revision 0 and nothing else."""

    rev: int = 0
    rows: list[CompositionRow] = field(default_factory=list)
    sources: list[StoredSource] = field(default_factory=list)
    numbering: dict[str, int] = field(default_factory=dict)
    records: list[SettingRecord] = field(default_factory=list)


class CompositionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- reads ----------------------------------------------------------------------------

    async def stored(self, series_id: int) -> Stored:
        return await self._db.transaction(lambda conn: _read(conn, series_id))

    async def pages(self) -> list[tuple[int, int, str]]:
        """(series id, source id, canonical page address) of every source whose page is known."""
        rows = await self._db.fetch_all(
            "SELECT series_id, source_id, table_url FROM composition_sources WHERE table_url IS NOT NULL")
        return [(row["series_id"], row["source_id"], row["table_url"]) for row in rows]

    async def series_ids(self) -> list[int]:
        rows = await self._db.fetch_all(
            "SELECT series_id FROM composition_state UNION SELECT series_id FROM composition_sources ORDER BY 1")
        return [row["series_id"] for row in rows]

    async def failures(self, urls: list[str]) -> dict[str, str]:
        if not urls:
            return {}
        marks = ", ".join("?" for _ in urls)
        rows = await self._db.fetch_all(
            f"SELECT table_url, reason FROM composition_failures WHERE table_url IN ({marks})", urls)
        return {row["table_url"]: row["reason"] for row in rows}

    # --- writes ---------------------------------------------------------------------------

    async def write(self, series_id: int, *, rows: list[CompositionRow] | None = None,
                    sources: list[StoredSource] | None = None, numbering: dict[str, int] | None = None,
                    records: list[SettingRecord] | None = None, rev: int | None = None) -> None:
        """Replaces the parts given, in one transaction; a part left out stays as it is."""
        await self._db.transaction(lambda conn: _write(conn, series_id, rows, sources, numbering, records, rev))

    async def set_failure(self, url: str, reason: str) -> None:
        await self._db.execute(
            "INSERT INTO composition_failures (table_url, reason) VALUES (?, ?)"
            " ON CONFLICT (table_url) DO UPDATE SET reason = excluded.reason", (url, reason))

    async def clear_failure(self, url: str) -> None:
        await self._db.execute("DELETE FROM composition_failures WHERE table_url = ?", (url,))

    async def forget_unreferenced_failures(self) -> None:
        """Verdicts of pages no source of ours reads any more; only safe while no event of a page is coming."""
        await self._db.execute(
            "DELETE FROM composition_failures WHERE table_url NOT IN"
            " (SELECT table_url FROM composition_sources WHERE table_url IS NOT NULL)")

    async def purge(self, series_id: int) -> list[int]:
        """Takes everything the module keeps of a series; returns the sources it had."""
        return await self._db.transaction(lambda conn: _purge(conn, series_id))


def _read(conn: sqlite3.Connection, series_id: int) -> Stored:
    state = conn.execute("SELECT rev FROM composition_state WHERE series_id = ?", (series_id,)).fetchone()
    rows = conn.execute("SELECT * FROM composition_rows WHERE series_id = ? ORDER BY seq", (series_id,)).fetchall()
    sources = conn.execute(
        "SELECT source_id, position, table_url, summary FROM composition_sources WHERE series_id = ?"
        " ORDER BY position, source_id", (series_id,)).fetchall()
    numbering = conn.execute("SELECT name_key, number FROM unknown_voices WHERE series_id = ?", (series_id,)).fetchall()
    records = conn.execute(
        "SELECT * FROM output_settings WHERE series_id = ?"
        " ORDER BY IFNULL(season, -1), IFNULL(episode, -1), lever, IFNULL(target, '')", (series_id,)).fetchall()
    return Stored(
        rev=state["rev"] if state is not None else 0,
        rows=[_row_of(row) for row in rows],
        sources=[StoredSource(row["source_id"], row["position"], row["table_url"],
                              SourceSummary.model_validate_json(row["summary"])) for row in sources],
        numbering={row["name_key"]: row["number"] for row in numbering},
        records=[SettingRecord(series_id=row["series_id"], season=row["season"], episode=row["episode"],
                               lever=row["lever"], target=row["target"], value=json.loads(row["value"]))
                 for row in records])


def _row_of(row: sqlite3.Row) -> CompositionRow:
    fields = {name: row[name] for name in _ROW_COLUMNS if name not in ("seq", "grp", "excluded", "heights")}
    return CompositionRow(**fields, group=row["grp"], excluded=bool(row["excluded"]),
                          heights=json.loads(row["heights"]))


def _write(conn: sqlite3.Connection, series_id: int, rows, sources, numbering, records, rev) -> None:
    if rows is not None:
        conn.execute("DELETE FROM composition_rows WHERE series_id = ?", (series_id,))
        conn.executemany(
            f"INSERT INTO composition_rows ({', '.join(_ROW_COLUMNS)}) VALUES ({', '.join('?' for _ in _ROW_COLUMNS)})",
            [_row_values(seq, row) for seq, row in enumerate(rows)])
    if sources is not None:
        conn.execute("DELETE FROM composition_sources WHERE series_id = ?", (series_id,))
        conn.executemany(
            "INSERT INTO composition_sources (source_id, series_id, position, table_url, summary) VALUES (?, ?, ?, ?, ?)",
            [(each.source_id, series_id, each.position, each.table_url, each.summary.model_dump_json())
             for each in sources])
    if numbering:
        conn.executemany(
            "INSERT INTO unknown_voices (series_id, name_key, number) VALUES (?, ?, ?)"
            " ON CONFLICT (series_id, name_key) DO UPDATE SET number = excluded.number",
            [(series_id, key, number) for key, number in numbering.items()])
    if records is not None:
        conn.execute("DELETE FROM output_settings WHERE series_id = ?", (series_id,))
        conn.executemany(
            "INSERT INTO output_settings (series_id, season, episode, lever, target, value) VALUES (?, ?, ?, ?, ?, ?)",
            [(series_id, each.season, each.episode, each.lever, each.target,
              json.dumps(each.value, ensure_ascii=False)) for each in records])
    if rev is not None:
        conn.execute("INSERT INTO composition_state (series_id, rev) VALUES (?, ?)"
                     " ON CONFLICT (series_id) DO UPDATE SET rev = excluded.rev", (series_id, rev))


def _row_values(seq: int, row: CompositionRow) -> tuple:
    values = {**row.model_dump(mode="json"), "seq": seq, "grp": row.group, "excluded": int(row.excluded),
              "heights": json.dumps(row.heights)}
    return tuple(values[name] for name in _ROW_COLUMNS)


def _purge(conn: sqlite3.Connection, series_id: int) -> list[int]:
    sources = [row["source_id"] for row in conn.execute(
        "SELECT source_id FROM composition_sources WHERE series_id = ?", (series_id,))]
    for table in ("composition_rows", "composition_sources", "composition_state", "unknown_voices", "output_settings"):
        conn.execute(f"DELETE FROM {table} WHERE series_id = ?", (series_id,))
    return sources
