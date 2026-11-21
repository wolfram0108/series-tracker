"""Lightweight async access to SQLite for modules.

Each module works only with its own tables (ownership map: spec, section 6).
This is a convention the tool does not enforce. Blocking sqlite calls run in
a thread (to_thread) so the event loop never stalls. One connection per
operation: cheap for SQLite and sidesteps thread-affinity issues; WAL allows
parallel reads.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")

# How long to wait for the switch to WAL: PRAGMA journal_mode needs an
# exclusive lock and ignores busy-timeout.
_WAL_TIMEOUT = 5.0
_WAL_RETRY = 0.05


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._inflight: set[asyncio.Future] = set()

    async def _threaded(self, fn: Callable[..., T], *args: Any) -> T:
        """A call into sqlite on a thread. A thread cannot be called back, so the call outlives the cancellation of its
        caller; it is kept on record until it ends, and `drained` waits for the last of them."""
        future = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        self._inflight.add(future)
        future.add_done_callback(self._ended)
        return await asyncio.shield(future)

    def _ended(self, future: asyncio.Future) -> None:
        self._inflight.discard(future)
        if not future.cancelled():
            future.exception()  # the caller that left will not look at it

    async def drained(self) -> None:
        """Returns when no call into sqlite is on a thread any more: what a stopped module began has landed."""
        while self._inflight:
            await asyncio.wait(set(self._inflight))

    def _connect(self, *, enforce_fk: bool = True) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        self._ensure_wal(conn)
        if enforce_fk:
            conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _ensure_wal(conn: sqlite3.Connection) -> None:
        """Switch the journal to WAL once per file.

        PRAGMA journal_mode needs an exclusive lock and does NOT wait for
        busy-timeout: a database created by alembic lives in delete mode, and
        concurrent connections at startup fail each other with
        'database is locked'. An already switched database is a no-op.
        """
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() == "wal":
            return
        deadline = time.monotonic() + _WAL_TIMEOUT
        while time.monotonic() < deadline:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError:
                time.sleep(_WAL_RETRY)

    def _fetch_all_sync(self, query: str, params: Iterable[Any]) -> list[dict]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(query, tuple(params))]

    def _execute_sync(self, query: str, params: Iterable[Any],
                      enforce_fk: bool = True) -> int:
        with self._connect(enforce_fk=enforce_fk) as conn:
            cur = conn.execute(query, tuple(params))
            conn.commit()
            return cur.rowcount

    def _execute_many_sync(self, statements: list[tuple[str, Iterable[Any]]]) -> int:
        changed = 0
        with self._connect() as conn:
            for query, params in statements:
                changed = conn.execute(query, tuple(params)).rowcount
            conn.commit()
        return changed

    def _insert_sync(self, query: str, params: Iterable[Any]) -> int:
        with self._connect() as conn:
            cur = conn.execute(query, tuple(params))
            conn.commit()
            return cur.lastrowid

    async def insert(self, query: str, params: Iterable[Any] = ()) -> int:
        """Return the id of the inserted row, as reported by the database (lastrowid).

        Reading the "max id" instead of lastrowid returns another row's id
        when inserts run in parallel."""
        return await self._threaded(self._insert_sync, query, params)

    async def fetch_all(self, query: str, params: Iterable[Any] = ()) -> list[dict]:
        return await self._threaded(self._fetch_all_sync, query, params)

    async def fetch_one(self, query: str, params: Iterable[Any] = ()) -> dict | None:
        rows = await self.fetch_all(query, params)
        return rows[0] if rows else None

    @staticmethod
    def coerce_bools(rows: list[dict] | dict | None,
                     columns: tuple[str, ...]) -> list[dict] | dict | None:
        """SQLite stores BOOLEAN as 0/1, while the old contract (SQLAlchemy)
        returned true/false, and the frontend (Vue v-model) distinguishes them
        strictly — находка 41. Table owners coerce their boolean columns on output."""
        if rows is None:
            return None
        single = isinstance(rows, dict)
        for row in [rows] if single else rows:
            for col in columns:
                if col in row and row[col] is not None:
                    row[col] = bool(row[col])
        return rows

    async def execute_many(self,
                           statements: list[tuple[str, Iterable[Any]]]) -> int:
        """Statements in one transaction: readers never see a half-applied change.
        Returns the number of rows the last statement changed."""
        return await self._threaded(self._execute_many_sync, statements)

    def _transaction_sync(self, work: Callable[[sqlite3.Connection], T]) -> T:
        with self._connect() as conn:
            return work(conn)

    async def transaction(self, work: Callable[[sqlite3.Connection], T]) -> T:
        """`work` runs on one connection in one transaction: its statements are kept together or none is,
        and a later statement may use what an earlier one returned (the id of a row it has just inserted)."""
        return await self._threaded(self._transaction_sync, work)

    async def execute(self, query: str, params: Iterable[Any] = (), *,
                      enforce_fk: bool = True) -> int:
        """enforce_fk=False is for the event-driven delete cascade (Р-19): the parent
        row is deleted at once and its owners clean up child tables on the event;
        transient orphans are allowed (the old system did not enforce FKs at all)."""
        return await self._threaded(self._execute_sync, query, params,
                                       enforce_fk)
