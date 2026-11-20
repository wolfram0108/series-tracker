"""Тесты статусной модели (Р-11): агрегатор и модуль catalog.

Семантика — от замысла (разбор этапа 4), не дифф со старым кодом:
старый StatusManager хранил статус на трёх этажах и пересчитывал его
поллингом; новый — вычисляет из свёрток и публикует только изменения.
"""
import asyncio
import sqlite3

import pytest

from core.bus import Bus
from core.module import BaseModule
from core.runner import Runner
from core.db import Database
from modules.catalog import CatalogModule
from series_factory import insert_series
from schema_db import migrated


# --- агрегатор: чистая логика ----------------------------------------------------

# --- модуль через шину --------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    migrated(path)
    with sqlite3.connect(path) as conn:
        insert_series(conn, id=1, url="http://x", state="error, scanning")
        conn.commit()
    return str(path)


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
async def system(db_path):
    bus = Bus()
    catalog = CatalogModule(bus, Database(db_path))
    probe = Probe(bus)
    runner = Runner(bus, [catalog, probe])
    await runner.start()
    yield bus, probe
    await runner.stop()


async def _next_change(sub, timeout=2.0):
    env = await asyncio.wait_for(sub.queue.get(), timeout)
    return env.payload


@pytest.mark.asyncio
async def test_viewing_commands_and_sse_guard(system):
    bus, probe = system
    sub = bus.subscribe("series.status.changed")

    probe.send_command("catalog.viewing.start", {"series_id": 1})
    assert await _next_change(sub) == {"series_id": 1,
                                       "statuses": ["viewing", "waiting"],
                                       "is_busy": False}

    # последний SSE-клиент отключился — viewing сброшен без heartbeat'ов
    probe.publish_event("gateway.sse.clients", {"count": 0})
    assert await _next_change(sub) == {"series_id": 1,
                                       "statuses": ["waiting"],
                                       "is_busy": False}
