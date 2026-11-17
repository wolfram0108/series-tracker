"""All SQL of the composition module: the layout of a series, its sources, the numbers of unknown voice-overs and the
output settings.

A series is read and written whole and in one transaction, so a reader never sees rows of one revision with the
revision number of another.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from core.contracts import CompositionRow, RowTrack, SettingRecord, SourceSummary
from core.db import Database

_ROW_COLUMNS = ("series_id", "source_id", "row_key", "seq", "excluded", "exclusion", "season", "episode_start",
                "episode_end", "part", "voice_group", "voice_label", "voice_name", "voice_unknown", "player",
                "player_label", "grp", "text", "heights", "state", "published", "link", "video", "stale", "timeline", "track",
                "language", "subtitle_type")


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
        both = None if rows is None else [*rows, *subtitles]
        await self._db.transaction(lambda conn: _write(conn, series_id, both, sources, numbering, records, rev, orders,
                                                       titles))

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
    orders = conn.execute("SELECT season, labels FROM voice_orders WHERE series_id = ? ORDER BY season", (series_id,)).fetchall()
    titles = conn.execute("SELECT label, title FROM voice_titles WHERE series_id = ? ORDER BY label", (series_id,)).fetchall()
    return Stored(
        orders={row["season"]: json.loads(row["labels"]) for row in orders},
        titles={row["label"]: row["title"] for row in titles},
        rev=state["rev"] if state is not None else 0,
        rows=[each for each in (_row_of(row) for row in rows) if each.track == RowTrack.SOUND],
        subtitles=[each for each in (_row_of(row) for row in rows) if each.track == RowTrack.SUBTITLES],
        sources=[StoredSource(row["source_id"], row["position"], row["table_url"],
                              SourceSummary.model_validate_json(row["summary"])) for row in sources],
        numbering={row["name_key"]: row["number"] for row in numbering},
        records=[SettingRecord(series_id=row["series_id"], season=row["season"], episode=row["episode"],
                               compilation=row["compilation"], lever=row["lever"], target=row["target"],
                               value=json.loads(row["value"]))
                 for row in records])


def _row_of(row: sqlite3.Row) -> CompositionRow:
    fields = {name: row[name] for name in _ROW_COLUMNS if name not in ("seq", "grp", "excluded", "heights", "stale")}
    return CompositionRow(**fields, group=row["grp"], excluded=bool(row["excluded"]), stale=bool(row["stale"]),
                          heights=json.loads(row["heights"]))


def _write(conn: sqlite3.Connection, series_id: int, rows, sources, numbering, records, rev, orders=None,
           titles=None) -> None:
    if titles is not None:
        conn.execute("DELETE FROM voice_titles WHERE series_id = ?", (series_id,))
        conn.executemany("INSERT INTO voice_titles (series_id, label, title) VALUES (?, ?, ?)",
                         [(series_id, label, title) for label, title in titles.items()])
    if orders is not None:
        conn.execute("DELETE FROM voice_orders WHERE series_id = ?", (series_id,))
        conn.executemany("INSERT INTO voice_orders (series_id, season, labels) VALUES (?, ?, ?)",
                         [(series_id, season, json.dumps(labels, ensure_ascii=False)) for season, labels in orders.items()])
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
            "INSERT INTO output_settings (series_id, season, episode, compilation, lever, target, value)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(series_id, each.season, each.episode, each.compilation, each.lever, each.target,
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
    for table in ("composition_rows", "composition_sources", "composition_state", "unknown_voices", "output_settings",
                  "voice_orders", "voice_titles"):
        conn.execute(f"DELETE FROM {table} WHERE series_id = ?", (series_id,))
    return sources
