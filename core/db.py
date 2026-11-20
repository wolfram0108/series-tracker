"""Лёгкий асинхронный доступ модулей к SQLite.

Каждый модуль работает ТОЛЬКО со своими таблицами (карта владения — ТЗ,
раздел 6); это соглашение, инструмент его не навязывает. Блокирующие
вызовы sqlite уходят в потоки базы — событийный цикл не виснет.
У каждого потока базы одно постоянное соединение: его кэш страниц и
отображение файла в память (mmap) живут между операциями, так что поле
строк раскладки в мегабайты читается копированием из памяти, а не обходом
файла заново на каждом запросе. Соединение не покидает свой поток; WAL
позволяет параллельное чтение.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")

# Сколько ждём переключения журнала в WAL: PRAGMA journal_mode требует
# эксклюзивной блокировки и не ждёт busy-timeout.
_WAL_TIMEOUT = 5.0
_WAL_RETRY = 0.05
# The threads of a database: as many operations at once as the modules ask (readers run side by side under WAL).
THREADS = 8
# How much of the file a connection maps into memory: the whole base of the service, with room to grow.
MMAP_BYTES = 1 << 30


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._inflight: set[asyncio.Future] = set()
        self._pool = ThreadPoolExecutor(max_workers=THREADS, thread_name_prefix="db")
        self._local = threading.local()
        # A database nobody holds any more lets its threads go, and with them their connections.
        weakref.finalize(self, self._pool.shutdown, wait=False)

    async def _threaded(self, fn: Callable[..., T], *args: Any) -> T:
        """A call into sqlite on a thread of the database. A thread cannot be called back, so the call outlives the
        cancellation of its caller; it is kept on record until it ends, and `drained` waits for the last of them."""
        future = asyncio.ensure_future(asyncio.get_running_loop().run_in_executor(self._pool, fn, *args))
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
        """The connection of this thread, made on its first operation; foreign keys as the operation asks (a pragma is
        no part of a transaction, and no transaction is open between operations)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=30)
            conn.row_factory = sqlite3.Row
            self._ensure_wal(conn)
            conn.execute(f"PRAGMA mmap_size={MMAP_BYTES}")
            self._local.conn, self._local.fk = conn, None
        if self._local.fk is not enforce_fk:
            conn.execute(f"PRAGMA foreign_keys={'ON' if enforce_fk else 'OFF'}")
            self._local.fk = enforce_fk
        return conn

    @staticmethod
    def _ensure_wal(conn: sqlite3.Connection) -> None:
        """Журнал WAL переключается один раз на файл.

        PRAGMA journal_mode требует эксклюзивной блокировки и НЕ ждёт
        busy-timeout: база, созданная alembic, живёт в режиме delete,
        и параллельные подключения при старте роняют друг друга
        «database is locked». Уже переключённая база — no-op.
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
        """id вставленной строки — его выдаёт сама база (lastrowid).

        Чтение «максимального id» вместо lastrowid отдаёт id чужой
        строки при параллельной вставке."""
        return await self._threaded(self._insert_sync, query, params)

    async def fetch_all(self, query: str, params: Iterable[Any] = ()) -> list[dict]:
        return await self._threaded(self._fetch_all_sync, query, params)

    async def fetch_one(self, query: str, params: Iterable[Any] = ()) -> dict | None:
        rows = await self.fetch_all(query, params)
        return rows[0] if rows else None

    @staticmethod
    def coerce_bools(rows: list[dict] | dict | None,
                     columns: tuple[str, ...]) -> list[dict] | dict | None:
        """SQLite хранит BOOLEAN как 0/1; старый контракт (SQLAlchemy)
        отдавал true/false, и фронт (Vue v-model) различает это строго —
        находка 41. Владельцы таблиц приводят свои булевы колонки на
        выдаче наружу."""
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
        """enforce_fk=False — для событийного каскада удаления (Р-19):
        родительская строка удаляется сразу, дочерние таблицы дочищают
        их владельцы по событию; мгновенные сироты допустимы (в старой
        системе FK не принуждались вовсе)."""
        return await self._threaded(self._execute_sync, query, params,
                                       enforce_fk)
