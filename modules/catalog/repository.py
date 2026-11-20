"""The repository of the catalog: the tables series and series_sources (the schema of the production database).

The column `state` and the table `series_statuses` are deliberately neither read nor written (Р-11: the status is a computed
value of the aggregator); they stay in the schema physically until it is cleaned up after the switch.

The address, kind and profile of a series live in its sources (series_sources, Д-38); a series is read together with them,
its row a dictionary of its columns and its `sources` a tuple of `SeriesSource`.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from core.contracts import WRITABLE_FIELDS, SeriesSource
from core.db import Database
from core.reasons import reason


# Every live column of series EXCEPT state (see the docstring).
_COLUMNS = ("id, name, name_en, site, save_path, season, quality, "
            "last_scan_time, auto_scan_enabled, quality_override, "
            "resolution_override, ignored_seasons, ignored_episodes")

# The columns that may be written from outside (no id, and no last_scan_time: only touch_scan_time updates it).
_WRITABLE = frozenset(WRITABLE_FIELDS)

_SOURCE_COLUMNS = ("id, series_id, position, url, source_type, parser_profile_id, "
                   "vibix_season, players_off, rows_per_line, color")

# The colours of the palette of the interface a source may take.
COLORS = 16

# A new source takes the first colour no source of its series has; when every one is taken, the colours go round.
_EVERY_COLOR = " UNION ALL ".join(f"SELECT {color} AS color" for color in range(COLORS))
_FREE_COLOR = ("(SELECT COALESCE(MIN(color), (SELECT COUNT(*) FROM series_sources WHERE series_id=?) % "
               f"{COLORS}) FROM ({_EVERY_COLOR}) WHERE color NOT IN (SELECT color FROM series_sources WHERE series_id=?))")

_ADD_SOURCE = ("INSERT INTO series_sources (series_id, position, url, source_type, parser_profile_id, "
               "vibix_season, players_off, rows_per_line, color) "
               "VALUES (?, COALESCE(?, (SELECT MAX(position) + 1 FROM series_sources WHERE series_id=?), 0), "
               f"?, ?, ?, ?, ?, ?, {_FREE_COLOR})")


def _source(row: dict) -> SeriesSource:
    """A stored source in the shape of the contract."""
    return SeriesSource.model_validate({**row, "players_off": json.loads(row["players_off"])})


def _stored(values: dict) -> dict:
    return {name: json.dumps(value, ensure_ascii=False) if name == "players_off" else value
            for name, value in values.items()}


def _add_source_args(series_id: int, values: dict) -> tuple:
    stored = _stored(values)
    return (series_id, stored.get("position"), series_id, stored["url"], stored["source_type"],
            stored["parser_profile_id"], stored["vibix_season"], stored["players_off"], stored["rows_per_line"],
            series_id, series_id)


def _refusal(exc: sqlite3.IntegrityError, values: dict) -> Exception:
    """A broken constraint of a source as the refusal it means; anything else is not ours to name."""
    if exc.sqlite_errorname == "SQLITE_CONSTRAINT_UNIQUE":
        return ValueError(reason("source_exists", url=values.get("url")))
    if exc.sqlite_errorname == "SQLITE_CONSTRAINT_FOREIGNKEY" and values.get("parser_profile_id") is not None:
        return ValueError(reason("profile_not_found", profile_id=values["parser_profile_id"]))
    return exc


class CatalogRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    _BOOLS = ("auto_scan_enabled",)

    # --- series ----------------------------------------------------------------

    @staticmethod
    def _with_sources(row: dict, sources: list[SeriesSource]) -> dict:
        return {**row, "sources": tuple(sources)}

    async def all_series(self) -> list[dict]:
        rows = await self._db.fetch_all(
            f"SELECT {_COLUMNS} FROM series ORDER BY id")
        rows = self._db.coerce_bools(rows, self._BOOLS)
        by_series: dict[int, list[SeriesSource]] = {}
        for source in await self._all_sources():
            by_series.setdefault(source.series_id, []).append(source)
        return [self._with_sources(row, by_series.get(row["id"], [])) for row in rows]

    async def series_exists(self, series_id: int) -> bool:
        return await self._db.fetch_one("SELECT 1 FROM series WHERE id=?", (series_id,)) is not None

    async def get_series(self, series_id: int) -> dict | None:
        row = await self._db.fetch_one(
            f"SELECT {_COLUMNS} FROM series WHERE id=?", (series_id,))
        if row is None:
            return None
        row = self._db.coerce_bools(row, self._BOOLS)
        return self._with_sources(row, await self.list_sources(series_id))

    async def set_save_path(self, series_id: int, save_path: str) -> None:
        await self._db.execute(
            "UPDATE series SET save_path=? WHERE id=?",
            (save_path, series_id))

    async def create_series(self, data: dict, sources: list[dict]) -> int:
        """A series with its sources in one transaction: a source that is refused leaves no series behind."""
        fields = {k: v for k, v in data.items() if k in _WRITABLE}
        # the defaults of the old ORM model (NOT NULL columns without a database default)
        fields.setdefault("auto_scan_enabled", False)
        fields.setdefault("ignored_seasons", "[]")
        fields.setdefault("ignored_episodes", "{}")
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        refused: list[dict] = []

        def write(conn: sqlite3.Connection) -> int:
            series_id = conn.execute(f"INSERT INTO series ({cols}) VALUES ({marks})", tuple(fields.values())).lastrowid
            for draft in sources:
                refused[:] = [draft]
                conn.execute(_ADD_SOURCE, _add_source_args(series_id, draft))
            return series_id

        try:
            return await self._db.transaction(write)
        except sqlite3.IntegrityError as exc:
            raise _refusal(exc, refused[0] if refused else {}) from exc

    async def update_series(self, series_id: int, data: dict) -> dict:
        """Updates only the live columns; returns the fields applied."""
        fields = {k: v for k, v in data.items() if k in _WRITABLE}
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            await self._db.execute(
                f"UPDATE series SET {sets} WHERE id=?",
                (*fields.values(), series_id))
        return fields

    async def delete_series(self, series_id: int) -> None:
        # series_statuses is a dead table (Р-11), but an orphan row left after the series is deleted is no use to it either:
        # it is cleaned together with the row of series.
        await self._db.execute(
            "DELETE FROM series_statuses WHERE series_id=?", (series_id,))
        # The sources are ours and go before their parent, so the foreign key holds.
        await self._db.execute(
            "DELETE FROM series_sources WHERE series_id=?", (series_id,))
        # enforce_fk=False: the child tables are cleaned by their owners on the event series.deleted (Р-19), so an orphan
        # that lives for a moment is allowed.
        await self._db.execute(
            "DELETE FROM series WHERE id=?", (series_id,), enforce_fk=False)

    async def touch_scan_time(self, series_id: int) -> str:
        # naive UTC with microseconds: the storage format of the production database
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        await self._db.execute(
            "UPDATE series SET last_scan_time=? WHERE id=?",
            (now, series_id))
        return now

    # --- sources ---------------------------------------------------------------

    async def _all_sources(self) -> list[SeriesSource]:
        rows = await self._db.fetch_all(
            f"SELECT {_SOURCE_COLUMNS} FROM series_sources ORDER BY series_id, position, id")
        return [_source(row) for row in rows]

    async def list_sources(self, series_id: int) -> list[SeriesSource]:
        rows = await self._db.fetch_all(
            f"SELECT {_SOURCE_COLUMNS} FROM series_sources WHERE series_id=? ORDER BY position, id",
            (series_id,))
        return [_source(row) for row in rows]

    async def sources_by_profile(self, profile_id: int) -> list[SeriesSource]:
        rows = await self._db.fetch_all(
            f"SELECT {_SOURCE_COLUMNS} FROM series_sources WHERE parser_profile_id=? ORDER BY series_id, position, id",
            (profile_id,))
        return [_source(row) for row in rows]

    async def get_source(self, source_id: int) -> SeriesSource | None:
        row = await self._db.fetch_one(
            f"SELECT {_SOURCE_COLUMNS} FROM series_sources WHERE id=?", (source_id,))
        return _source(row) if row else None

    async def add_source(self, series_id: int, values: dict) -> int:
        """The source goes to the end of the series' list unless it brings a position."""
        try:
            return await self._db.insert(_ADD_SOURCE, _add_source_args(series_id, values))
        except sqlite3.IntegrityError as exc:
            raise _refusal(exc, values) from exc

    async def update_source(self, source_id: int, values: dict) -> None:
        sets = ", ".join(f"{name}=?" for name in values)
        try:
            await self._db.execute(f"UPDATE series_sources SET {sets} WHERE id=?",
                                   (*_stored(values).values(), source_id))
        except sqlite3.IntegrityError as exc:
            raise _refusal(exc, values) from exc

    async def remove_source(self, source_id: int) -> bool:
        """False when it is the last source of its series; the check and the delete are one statement, so two
        removals at once cannot leave a series without sources."""
        removed = await self._db.execute(
            "DELETE FROM series_sources WHERE id=? AND "
            "(SELECT COUNT(*) FROM series_sources WHERE series_id="
            "(SELECT series_id FROM series_sources WHERE id=?)) > 1",
            (source_id, source_id))
        return removed > 0
