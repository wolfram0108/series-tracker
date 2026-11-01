"""Журнал SQLite: переключение в WAL не должно ронять параллельные
подключения.

PRAGMA journal_mode требует эксклюзивной блокировки и не ждёт
busy-timeout: база, созданная alembic, живёт в режиме delete, и первые
подключения при старте системы конфликтуют.
"""
import asyncio
import sqlite3

import pytest

from core.db import Database


@pytest.mark.asyncio
async def test_parallel_connect_switches_to_wal(tmp_path):
    """Пока база занята записи, подключения ждут и переключают журнал."""
    path = tmp_path / "race.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.close()

    blocker = sqlite3.connect(path, timeout=30)
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("INSERT INTO t VALUES (1)")

    db = Database(str(path))

    async def release() -> None:
        await asyncio.sleep(0.3)
        blocker.rollback()
        blocker.close()

    async def probe() -> list[dict]:
        return await db.fetch_all("SELECT 1")

    releaser = asyncio.create_task(release())
    results = await asyncio.gather(*(probe() for _ in range(4)),
                                   return_exceptions=True)
    await releaser

    assert not any(isinstance(r, Exception) for r in results), results
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


@pytest.mark.asyncio
async def test_a_transaction_keeps_its_statements_together_and_lets_a_later_one_use_an_earlier_id(tmp_path):
    path = tmp_path / "tx.db"
    setup = sqlite3.connect(path)
    setup.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY AUTOINCREMENT)")
    setup.execute("CREATE TABLE child (parent_id INTEGER NOT NULL UNIQUE)")
    setup.commit()
    setup.close()
    db = Database(str(path))

    def write(conn):
        parent_id = conn.execute("INSERT INTO parent DEFAULT VALUES").lastrowid
        conn.execute("INSERT INTO child VALUES (?)", (parent_id,))
        return parent_id

    assert await db.transaction(write) == 1
    assert await db.fetch_all("SELECT parent_id FROM child") == [{"parent_id": 1}]


@pytest.mark.asyncio
async def test_a_failed_statement_takes_back_the_whole_transaction(tmp_path):
    path = tmp_path / "tx.db"
    setup = sqlite3.connect(path)
    setup.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY AUTOINCREMENT)")
    setup.execute("CREATE TABLE child (parent_id INTEGER NOT NULL UNIQUE)")
    setup.commit()
    setup.close()
    db = Database(str(path))

    def write(conn):
        parent_id = conn.execute("INSERT INTO parent DEFAULT VALUES").lastrowid
        conn.execute("INSERT INTO child VALUES (?)", (parent_id,))
        conn.execute("INSERT INTO child VALUES (?)", (parent_id,))

    with pytest.raises(sqlite3.IntegrityError):
        await db.transaction(write)
    assert await db.fetch_all("SELECT id FROM parent") == []
