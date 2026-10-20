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
