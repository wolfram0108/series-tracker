"""Лёгкий асинхронный доступ модулей к SQLite.

Каждый модуль работает ТОЛЬКО со своими таблицами (карта владения — ТЗ,
раздел 6); это соглашение, инструмент его не навязывает. Блокирующие
вызовы sqlite уходят в поток (to_thread) — событийный цикл не виснет.
Соединение на операцию: дёшево для SQLite и снимает вопросы потоков;
WAL позволяет параллельное чтение.
"""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Any, Iterable


class Database:
    def __init__(self, path: str) -> None:
        self._path = path

    def _connect(self, *, enforce_fk: bool = True) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        if enforce_fk:
            conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _fetch_all_sync(self, query: str, params: Iterable[Any]) -> list[dict]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(query, tuple(params))]

    def _execute_sync(self, query: str, params: Iterable[Any],
                      enforce_fk: bool = True) -> int:
        with self._connect(enforce_fk=enforce_fk) as conn:
            cur = conn.execute(query, tuple(params))
            conn.commit()
            return cur.rowcount

    async def fetch_all(self, query: str, params: Iterable[Any] = ()) -> list[dict]:
        return await asyncio.to_thread(self._fetch_all_sync, query, params)

    async def fetch_one(self, query: str, params: Iterable[Any] = ()) -> dict | None:
        rows = await self.fetch_all(query, params)
        return rows[0] if rows else None

    async def execute(self, query: str, params: Iterable[Any] = (), *,
                      enforce_fk: bool = True) -> int:
        """enforce_fk=False — для событийного каскада удаления (Р-19):
        родительская строка удаляется сразу, дочерние таблицы дочищают
        их владельцы по событию; мгновенные сироты допустимы (в старой
        системе FK не принуждались вовсе)."""
        return await asyncio.to_thread(self._execute_sync, query, params,
                                       enforce_fk)
