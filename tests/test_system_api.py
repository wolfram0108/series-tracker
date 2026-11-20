"""Тесты блока «скан и очереди» этапа 5 (Р-20): статус планировщика,
настройки сканера (пересчёт расписания вместо немедленного скана),
scan_all/series-scan через HTTP, очереди агентов."""
import asyncio
import sqlite3
from datetime import datetime, timedelta

import httpx
import pytest

from core import BaseModule, Bus, Runner
from core.reasons import reason
from core.db import Database
from modules.catalog import CatalogModule
from modules.gateway import GatewayModule
from modules.scan import ScanModule
from modules.settings import SettingsModule
from series_factory import insert_series
from settle import settle
from schema_db import migrated


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    migrated(path)
    with sqlite3.connect(path) as conn:
        insert_series(conn, id=1, name="Т", name_en="T", url="http://x", source_type="torrent")
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
            return {"tasks": [{"id": "88:s06e08:dub:A", "need": {"key": "88:s06e08:dub:A", "series_id": 88, "kind": "dub"},
                               "status": "download"}], "count": 1}

        async def dl_clear(env):
            return {"deleted": 3}

        self.handle("torrents.queue.get", torrents_queue)
        self.handle("downloads.queue.get", dl_queue)
        self.handle("downloads.queue.clear", dl_clear)


@pytest.fixture
async def system(db_path, tmp_path):
    bus = Bus()
    db = Database(db_path)
    gateway = GatewayModule(bus, static_dir=str(tmp_path), auth_required=False,
                            templates_dir=str(tmp_path))
    scan = ScanModule(bus, db, scheduler=False)
    runner = Runner(bus, [gateway, CatalogModule(bus, db),
                          SettingsModule(bus, db), scan, Queues(bus)])
    await runner.start()
    transport = httpx.ASGITransport(app=gateway.app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        yield bus, db, client, scan
    await runner.stop()


@pytest.mark.asyncio
async def test_a_new_schedule_sets_the_next_sync_and_check_by_the_times_of_day_and_scans_nothing_at_once(system):
    """Р-20: a change of the settings scans nothing at once; the next sync is the first time of day of the schedule after
    now, the next check the first of the checks (one between two syncs by default)."""
    bus, db, client, scan = system
    sub = bus.subscribe("scan.status.changed")
    local = datetime.now().astimezone()
    soon, later = local + timedelta(minutes=30), local + timedelta(minutes=90)
    times = sorted({soon.strftime("%H:%M"), later.strftime("%H:%M")})
    resp = await client.post("/api/scanner/settings", json={"enabled": True})
    assert resp.status_code == 200
    resp = await client.post("/api/schedule", json={"schedule": {"syncs": times}})
    assert resp.status_code == 200 and resp.json()["schedule"]["syncs"] == times

    await settle(bus, scan)  # the recount is the reaction of scan to the event of the change
    row = await db.fetch_one("SELECT value FROM settings WHERE key='next_scan_timestamp'")
    next_ts = datetime.fromisoformat(row["value"])
    assert timedelta(minutes=28) < next_ts - local < timedelta(minutes=31)
    assert scan._scan_all_running is False  # немедленного скана нет
    status = (await client.get("/api/scanner/status")).json()
    check = datetime.fromisoformat(status["next_check_time"])
    assert timedelta(minutes=58) < check - local < timedelta(minutes=61)
    env = await asyncio.wait_for(sub.queue.get(), 3)  # статус опубликован
    assert env.payload["scanner_enabled"] in (True, False)
    refused = await client.post("/api/schedule", json={"schedule": {"syncs": ["25:00"]}})
    assert refused.status_code == 400 and "schedule_invalid" in refused.json()["error"]


@pytest.mark.asyncio
async def test_scan_all_409_when_running(system):
    _, _, client, scan = system
    scan._scan_all_running = True
    resp = await client.post("/api/scanner/scan_all", json={})
    assert resp.status_code == 409
    assert resp.json()["error"] == reason("scan_all_running")


@pytest.mark.asyncio
async def test_queue_endpoints(system):
    _, _, client, _ = system
    resp = await client.get("/api/agent/queue")
    assert resp.json()[0]["hash"] == "h1"

    resp = await client.get("/api/downloads/queue")
    [card] = resp.json()
    assert (card["id"], card["status"], card["need"]["series_id"]) == ("88:s06e08:dub:A", "download", 88)

    resp = await client.post("/api/downloads/queue/clear")
    assert resp.json() == {"success": True,
                           "message": reason("queue_tasks_removed", count=3)}

    # /api/agent/reset удалён (находка 23) — точки больше нет
    resp = await client.post("/api/agent/reset")
    assert resp.status_code in (404, 405)
