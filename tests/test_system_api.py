"""Тесты блока «скан и очереди» этапа 5 (Р-20): статус планировщика,
настройки сканера (пересчёт расписания вместо немедленного скана),
scan_all/series-scan через HTTP, очереди агентов."""
import asyncio
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from core import BaseModule, Bus, Runner
from core.db import Database
from modules.catalog import CatalogModule
from modules.gateway import GatewayModule
from modules.scan import ScanModule
from modules.settings import SettingsModule


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{path}",
                        "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode) VALUES "
            "(1, 'http://x', 'Т', 'T', 'kinozal', '/media/t', 'waiting', "
            "'torrent', 0, 'search')")
        conn.commit()
    return str(path)


class Queues(BaseModule):
    """Фейки очередей torrents/downloads для HTTP-роутов."""
    name = "queues"

    def register(self):
        async def torrents_queue(env):
            return {"count": 1, "tasks": [
                {"torrent_hash": "h1", "series_id": 1, "stage": "renaming"}]}

        async def dl_queue(env):
            return {"tasks": [{"id": 5, "task_key": "uid", "progress": 40}],
                    "count": 1}

        async def dl_clear(env):
            return {"deleted": 3}

        self.handle("torrents.queue.get", torrents_queue)
        self.handle("downloads.queue.get", dl_queue)
        self.handle("downloads.queue.clear", dl_clear)


@pytest.fixture
async def system(db_path, tmp_path):
    bus = Bus()
    db = Database(db_path)
    gateway = GatewayModule(bus, static_dir=str(tmp_path),
                            templates_dir=str(tmp_path))
    scan = ScanModule(bus, db, scheduler_tick=None)
    runner = Runner(bus, [gateway, CatalogModule(bus, db),
                          SettingsModule(bus, db), scan, Queues(bus)])
    await runner.start()
    transport = httpx.ASGITransport(app=gateway.app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        yield bus, db, client, scan
    await runner.stop()


@pytest.mark.asyncio
async def test_scanner_status_shape(system):
    _, _, client, _ = system
    resp = await client.get("/api/scanner/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"scanner_enabled": False, "scan_interval": 60,
                    "is_scanning": False, "is_awaiting_tasks": False,
                    "next_scan_time": None}


@pytest.mark.asyncio
async def test_settings_reschedule_instead_of_immediate_scan(system):
    """Р-20: смена настроек НЕ запускает полный скан — пересчитывает
    next_scan_timestamp от текущего момента."""
    bus, db, client, scan = system
    sub = bus.subscribe("scan.status.changed")
    before = datetime.now(timezone.utc)
    resp = await client.post("/api/scanner/settings",
                             json={"enabled": True, "interval": 30})
    assert resp.status_code == 200

    next_ts = None
    for _ in range(100):  # пересчёт — реакция на событие, дожидаемся
        row = await db.fetch_one(
            "SELECT value FROM settings WHERE key='next_scan_timestamp'")
        if row and row["value"]:
            next_ts = datetime.fromisoformat(row["value"])
            break
        await asyncio.sleep(0.02)
    assert next_ts is not None
    delta = next_ts - before
    assert timedelta(minutes=29) < delta < timedelta(minutes=31)
    assert scan._scan_all_running is False  # немедленного скана нет
    env = await asyncio.wait_for(sub.queue.get(), 3)  # статус опубликован
    assert env.payload["scanner_enabled"] in (True, False)


@pytest.mark.asyncio
async def test_scan_all_409_when_running(system):
    _, _, client, scan = system
    scan._scan_all_running = True
    resp = await client.post("/api/scanner/scan_all", json={})
    assert resp.status_code == 409
    assert resp.json()["error"] == "Сканирование уже запущено."


@pytest.mark.asyncio
async def test_series_scan_409_when_running(system):
    _, _, client, scan = system
    scan._running.add(1)
    resp = await client.post("/api/series/1/scan")
    assert resp.status_code == 409
    assert "уже запущен" in resp.json()["error"]


@pytest.mark.asyncio
async def test_sse_client_triggers_status_publish(system):
    bus, _, _, _ = system
    sub = bus.subscribe("scan.status.changed")
    bus_probe = bus  # подключение SSE-клиента имитируем событием gateway
    from core.envelope import Envelope
    bus_probe.publish(Envelope(topic="gateway.sse.clients", kind="event",
                               payload={"count": 1}))
    env = await asyncio.wait_for(sub.queue.get(), 3)
    assert "scanner_enabled" in env.payload


@pytest.mark.asyncio
async def test_queue_endpoints(system):
    _, _, client, _ = system
    resp = await client.get("/api/agent/queue")
    assert resp.json()[0]["hash"] == "h1"

    resp = await client.get("/api/downloads/queue")
    assert resp.json() == [{"id": 5, "task_key": "uid", "progress": 40}]

    resp = await client.post("/api/downloads/queue/clear")
    assert resp.json() == {"success": True,
                           "message": "Удалено 3 задач из очереди."}

    # /api/agent/reset удалён (находка 23) — точки больше нет
    resp = await client.post("/api/agent/reset")
    assert resp.status_code in (404, 405)
