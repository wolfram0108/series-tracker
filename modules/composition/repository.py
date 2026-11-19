"""All SQL of the composition module: the layout of a series, its sources, the numbers of unknown voice-overs and the
output settings.

A series is read and written whole and in one transaction, so a reader never sees rows of one revision with the
revision number of another. Its rows are two fields of its record (composition_state.rows and .subtitles, the JSON of their
models in the order of the layout): ten thousand rows are read and written as one value each.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from pydantic import TypeAdapter

from core.contracts import CompositionRow, SettingRecord, SourceSummary
from core.db import Database

ROWS = TypeAdapter(list[CompositionRow])


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
    # The rows as they are stored (the JSON of their models), for a reader that gives them on with no model made.
    rows_json: bytes = b"[]"
    subtitles_json: bytes = b"[]"
    # Every write of the series raises it, whatever it changes (`rev` moves only with what a window shows): a reader that
    # counted from the series at one count knows whether anything was written since.
    writes: int = 0
    rows: list[CompositionRow] = field(default_factory=list)
    # The rows of subtitles, apart from the rows of sound (DESIGN.md 7.1.1); one table keeps both.
    subtitles: list[CompositionRow] = field(default_factory=list)
    sources: list[StoredSource] = field(default_factory=list)
    numbering: dict[str, int] = field(default_factory=dict)
    records: list[SettingRecord] = field(default_factory=list)
    # season -> the voice-over groups in the order of the tracks of the container (stage 11)
    orders: dict[int, list[str]] = field(default_factory=dict)
    # voice-over group -> the user's name of its track in the containers
    titles: dict[str, str] = field(default_factory=dict)


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

    # --- writes ---------------------------------------------------------------------------

    async def write(self, series_id: int, *, rows: list[CompositionRow] | None = None,
                    subtitles: list[CompositionRow] | None = None,
                    sources: list[StoredSource] | None = None, numbering: dict[str, int] | None = None,
                    records: list[SettingRecord] | None = None, rev: int | None = None,
                    orders: dict[int, list[str]] | None = None, titles: dict[str, str] | None = None) -> None:
        """Replaces the parts given, in one transaction; a part left out stays as it is."""
        # The rows of sound and of subtitles are one table: written together (the caller gives both or neither).
        if (rows is None) != (subtitles is None):
            raise ValueError("the rows of sound and of subtitles are written together")
        both = None if rows is None else (rows, subtitles)
        await self._db.transaction(lambda conn: _write(conn, series_id, both, sources, numbering, records, rev, orders,
                                                       titles))

    async def prints(self) -> dict[int, str]:
        """The print of the inputs every layout was last counted from (core: none for a layout counted otherwise)."""
        return {row["series_id"]: row["inputs"] for row in await self._db.fetch_all(
            "SELECT series_id, inputs FROM composition_state WHERE inputs IS NOT NULL")}

    async def save_print(self, series_id: int, inputs: str | None) -> None:
        """The print of the inputs the layout was counted from, kept beside it; it is no content of the layout and raises
        no counter."""
        await self._db.execute("INSERT INTO composition_state (series_id, inputs) VALUES (?, ?) ON CONFLICT (series_id)"
                               " DO UPDATE SET inputs = excluded.inputs", (series_id, inputs))

    async def writes(self) -> dict[int, int]:
        """The counter of the writes of every series that has one."""
        return {row["series_id"]: row["writes"]
                for row in await self._db.fetch_all("SELECT series_id, writes FROM composition_state")}

    async def purge(self, series_id: int) -> list[int]:
        """Takes everything the module keeps of a series; returns the sources it had."""
        return await self._db.transaction(lambda conn: _purge(conn, series_id))


def _read(conn: sqlite3.Connection, series_id: int) -> Stored:
    state = conn.execute("SELECT rev, writes, rows, subtitles FROM composition_state WHERE series_id = ?",
                         (series_id,)).fetchone()
    rows_json = (state["rows"] if state is not None else None) or b"[]"
    subtitles_json = (state["subtitles"] if state is not None else None) or b"[]"
    sources = conn.execute(
        "SELECT source_id, position, table_url, summary FROM composition_sources WHERE series_id = ?"
        " ORDER BY position, source_id", (series_id,)).fetchall()
    numbering = conn.execute("SELECT name_key, number FROM unknown_voices WHERE series_id = ?", (series_id,)).fetchall()
    records = conn.execute(
        "SELECT * FROM output_settings WHERE series_id = ?"
        " ORDER BY IFNULL(season, -1), IFNULL(episode, -1), lever, IFNULL(target, '')", (series_id,)).fetchall()
    orders = conn.execute("SELECT season, labels FROM voice_orders WHERE series_id = ? ORDER BY season", (series_id,)).fetchall()
    titles = conn.execute("SELECT label, title FROM voice_titles WHERE series_id = ? ORDER BY label", (series_id,)).fetchall()
    return Stored(
        orders={row["season"]: json.loads(row["labels"]) for row in orders},
        titles={row["label"]: row["title"] for row in titles},
        rev=state["rev"] if state is not None else 0,
        writes=state["writes"] if state is not None else 0,
        rows_json=rows_json, subtitles_json=subtitles_json,
        rows=ROWS.validate_json(rows_json), subtitles=ROWS.validate_json(subtitles_json),
        sources=[StoredSource(row["source_id"], row["position"], row["table_url"],
                              SourceSummary.model_validate_json(row["summary"])) for row in sources],
        numbering={row["name_key"]: row["number"] for row in numbering},
        records=[SettingRecord(series_id=row["series_id"], season=row["season"], episode=row["episode"],
                               compilation=row["compilation"], lever=row["lever"], target=row["target"],
                               value=json.loads(row["value"]))
                 for row in records])


def _write(conn: sqlite3.Connection, series_id: int, rows: tuple[list, list] | None, sources, numbering, records, rev,
           orders=None, titles=None) -> None:
    """`rows`: the rows of sound and of subtitles, written out here, on the thread of the query."""
    # The time a table of a source was read is no content of the layout (a window is not told it either): sources that
    # differ from the stored ones by it alone are written without raising the counter.
    moved = any(part is not None for part in (rows, numbering or None, records, orders, titles)) or rev is not None or (
        sources is not None and _content(_stored_sources(conn, series_id)) != _content(
            [(each.source_id, each.position, each.table_url, each.summary.model_dump_json()) for each in sources]))
    if titles is not None:
        conn.execute("DELETE FROM voice_titles WHERE series_id = ?", (series_id,))
        conn.executemany("INSERT INTO voice_titles (series_id, label, title) VALUES (?, ?, ?)",
                         [(series_id, label, title) for label, title in titles.items()])
    if orders is not None:
        conn.execute("DELETE FROM voice_orders WHERE series_id = ?", (series_id,))
        conn.executemany("INSERT INTO voice_orders (series_id, season, labels) VALUES (?, ?, ?)",
                         [(series_id, season, json.dumps(labels, ensure_ascii=False)) for season, labels in orders.items()])
    if rows is not None:
        conn.execute("INSERT INTO composition_state (series_id, rows, subtitles) VALUES (?, ?, ?) ON CONFLICT (series_id)"
                     " DO UPDATE SET rows = excluded.rows, subtitles = excluded.subtitles",
                     (series_id, ROWS.dump_json(rows[0]), ROWS.dump_json(rows[1])))
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
            "INSERT INTO output_settings (series_id, season, episode, compilation, lever, target, value)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(series_id, each.season, each.episode, each.compilation, each.lever, each.target,
              json.dumps(each.value, ensure_ascii=False)) for each in records])
    # The one place a series is written: every write of its content raises its counter, in the transaction of the write.
    if moved:
        conn.execute("INSERT INTO composition_state (series_id, rev, writes) VALUES (?, ?, 1) ON CONFLICT (series_id) DO "
                     "UPDATE SET writes = writes + 1, rev = COALESCE(?, rev)", (series_id, rev or 0, rev))


def _stored_sources(conn: sqlite3.Connection, series_id: int) -> list[tuple]:
    return [tuple(row) for row in conn.execute(
        "SELECT source_id, position, table_url, summary FROM composition_sources WHERE series_id = ?", (series_id,))]


def _content(sources: list[tuple]) -> list[tuple]:
    """Sources as the layout tells them, without the time their tables were read, in one order."""
    out = []
    for source_id, position, table_url, summary in sources:
        told = json.loads(summary)
        told.pop("read_at", None)
        out.append((source_id, position, table_url, json.dumps(told, sort_keys=True, ensure_ascii=False)))
    return sorted(out)


def _purge(conn: sqlite3.Connection, series_id: int) -> list[int]:
    sources = [row["source_id"] for row in conn.execute(
        "SELECT source_id FROM composition_sources WHERE series_id = ?", (series_id,))]
    for table in ("composition_sources", "composition_state", "unknown_voices", "output_settings", "voice_orders",
                  "voice_titles"):
        conn.execute(f"DELETE FROM {table} WHERE series_id = ?", (series_id,))
    return sources
