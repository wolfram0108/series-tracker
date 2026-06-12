"""Тесты блока «серии» этапа 5 (Р-19): CRUD catalog, событийный каскад
удаления, HTTP-роуты gateway (формы старого контракта routes/series.py)."""
import asyncio
import json
import sqlite3
import subprocess
import sys

import httpx
import pytest

from core import BaseModule, Bus, Runner
from core.db import Database
from modules.catalog import CatalogModule
from modules.gateway import GatewayModule
from modules.library import LibraryModule
from modules.metadata import MetadataModule
from modules.renaming import RenamingModule
from modules.scan.repository import ScanRepository
from modules.slicing.repository import SlicingRepository


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
            "(1, 'http://kinozal.tv/details.php?id=7', 'Тайтл', 'Title', "
            "'kinozal', '/media/t', 'waiting', 'torrent', 0, 'search')")
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode) VALUES "
            "(2, 'u|q', 'ВК', 'Show', 'vk', '/media/s', 'waiting', "
            "'vk_video', 0, 'get_all')")
        # данные для каскада удаления серии 2
        conn.execute(
            "INSERT INTO media_items (series_id, unique_id, episode_start, "
            "plan_status, status, is_ignored_by_user, source_url, "
            "publication_date, final_filename, slicing_status, "
            "is_available) VALUES (2, 'uid1', 1, 'in_plan_single', "
            "'completed', 0, 'http://v/1', '2026-01-01 00:00:00', "
            "'Show s01e01.mp4', 'none', 1)")
        conn.execute(
            "INSERT INTO sliced_files (series_id, "
            "source_media_item_unique_id, episode_number, file_path, "
            "status) VALUES (2, 'uid1', 1, '/media/s/e1.mp4', 'completed')")
        conn.execute(
            "INSERT INTO renaming_tasks (series_id, status, task_type) "
            "VALUES (2, 'error', 'mass_vk_reprocess')")
        conn.execute(
            "INSERT INTO series_tmdb_mappings (series_id, tmdb_id, "
            "tmdb_season_number, total_episodes) VALUES (2, 99, 1, 12)")
        conn.commit()
    return str(path)


class Neighbours(BaseModule):
    """Фейки соседей gateway: то, что в проде отвечают scan/torrents/
    sources/downloads — здесь фиксированные ответы + журнал вызовов."""
    name = "neighbours"

    def __init__(self, bus):
        self.calls: list[tuple[str, dict]] = []
        super().__init__(bus)

    def register(self):
        async def counts_vk(env):
            return {"counts": {2: 5}}

        async def counts_torrent(env):
            return {"counts": {1: 3}}

        async def resolve(env):
            self.calls.append(("resolve", env.payload))
            return {"tracker": {"canonical_name": "kinozal"}}

        async def history(env):
            return [{"id": 1, "torrent_id": "abc", "is_active": 1}]

        async def queue(env):
            return {"count": 1, "tasks": [
                {"torrent_hash": "h1", "series_id": 1, "stage": "renaming"}]}

        async def progress(env):
            return {"tasks": [{"task_key": "h1", "series_id": 1,
                               "series_name": "Тайтл", "status": "downloading",
                               "progress": 55, "dlspeed": 1024, "eta": 60}]}

        async def fs_sync(env):
            self.calls.append(("fs.sync", env.payload))
            return {"adopted": 0, "lost": 0}

        async def fs_verify(env):
            self.calls.append(("fs.verify", env.payload))
            return {"missing": 0}

        async def reprocess(env):
            self.calls.append(("reprocess", env.payload))
            return {"ok": True}

        async def relocate(env):
            self.calls.append(("relocate", env.payload))
            return {"task_id": 7}

        async def db_add(env):
            self.calls.append(("torrents.db.add", env.payload))
            return {"added": len(env.payload["torrents"])}

        self.handle("scan.media.downloaded_counts", counts_vk)
        self.handle("torrents.db.downloaded_counts", counts_torrent)
        self.handle("sources.tracker.resolve", resolve)
        self.handle("torrents.db.history", history)
        self.handle("torrents.queue.get", queue)
        self.handle("torrents.db.progress.list", progress)
        self.handle("downloads.fs.sync", fs_sync)
        self.handle("torrents.fs.verify", fs_verify)
        self.handle("renaming.reprocess", reprocess)
        self.handle("library.relocate", relocate)
        self.handle("torrents.db.add", db_add)


@pytest.fixture
async def system(db_path, tmp_path):
    bus = Bus()
    db = Database(db_path)
    gateway = GatewayModule(bus, static_dir=str(tmp_path),
                            templates_dir=str(tmp_path))
    neighbours = Neighbours(bus)
    runner = Runner(bus, [gateway, CatalogModule(bus, db),
                          MetadataModule(bus, db), neighbours])
    await runner.start()
    transport = httpx.ASGITransport(app=gateway.app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        yield bus, db, client, neighbours
    await runner.stop()


# --- GET: список и карточка -------------------------------------------------------


@pytest.mark.asyncio
async def test_series_list_shape(system):
    _, _, client, _ = system
    resp = await client.get("/api/series")
    assert resp.status_code == 200
    data = {s["id"]: s for s in resp.json()}
    assert set(data) == {1, 2}
    # форма карточки старого контракта
    torrent, vk = data[1], data[2]
    assert torrent["downloaded_episodes_count"] == 3   # счётчик torrents
    assert vk["downloaded_episodes_count"] == 5        # счётчик scan
    assert vk["tmdb_info"]["tmdb_id"] == 99
    assert torrent["tmdb_info"] is None
    assert torrent["is_busy"] is False
    assert torrent["statuses"] == ["waiting"]
    assert "state" not in torrent  # колонка похоронена (Р-11)


@pytest.mark.asyncio
async def test_series_details_and_404(system):
    _, _, client, neighbours = system
    resp = await client.get("/api/series/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tracker_info"] == {"canonical_name": "kinozal"}
    assert ("resolve", {"url": body["url"]}) in neighbours.calls

    resp = await client.get("/api/series/777")
    assert resp.status_code == 404
    assert resp.json() == {"error": "Сериал не найден"}


# --- создание и удаление ----------------------------------------------------------


@pytest.mark.asyncio
async def test_add_series_with_torrents_and_event(system):
    bus, _, client, neighbours = system
    sub = bus.subscribe("series.added")
    resp = await client.post("/api/series", json={
        "url": "http://kinozal.tv/details.php?id=9", "name": "Новый",
        "name_en": "New", "site": "kinozal", "save_path": "/media/n",
        "source_type": "torrent",
        "tmdb_data": {"tmdb_id": 5, "tmdb_season_number": 1,
                      "total_episodes": 8},
        "torrents": [{"torrent_id": "t9", "link": "http://kinozal.tv/9",
                      "date_time": "2026-01-01 00:00:00"}]})
    assert resp.status_code == 200
    series_id = resp.json()["series_id"]
    env = await asyncio.wait_for(sub.queue.get(), 3)
    assert env.payload["id"] == series_id
    assert env.payload["name"] == "Новый"
    assert env.payload["statuses"] == ["waiting"]
    assert ("torrents.db.add",
            {"series_id": series_id,
             "torrents": [{"torrent_id": "t9",
                           "link": "http://kinozal.tv/9",
                           "date_time": "2026-01-01 00:00:00"}]}) \
        in neighbours.calls
    # TMDB-маппинг записан владельцем
    details = (await client.get(f"/api/series/{series_id}")).json()
    assert details["tmdb_info"]["tmdb_id"] == 5


@pytest.mark.asyncio
async def test_delete_cascade_by_owners(db_path, tmp_path):
    """series.deleted: каждый владелец чистит свои таблицы (Р-19)."""
    bus = Bus()
    db = Database(db_path)
    gateway = GatewayModule(bus, static_dir=str(tmp_path),
                            templates_dir=str(tmp_path))
    # реальные владельцы без внешних зависимостей; scan требует соседей
    # только в работе, подписка на series.deleted — чистая БД
    from modules.scan import ScanModule
    modules = [gateway, CatalogModule(bus, db), MetadataModule(bus, db),
               ScanModule(bus, db, scheduler_tick=None), RenamingModule(bus, db),
               LibraryModule(bus, db)]
    runner = Runner(bus, modules)
    await runner.start()
    try:
        transport = httpx.ASGITransport(app=gateway.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            sub = bus.subscribe("series.deleted")
            resp = await client.delete("/api/series/2")
            assert resp.status_code == 200
            env = await asyncio.wait_for(sub.queue.get(), 3)
            assert env.payload == {"series_id": 2, "delete_from_qb": False}

        async def _empty(table, where="series_id=2"):
            rows = await db.fetch_all(
                f"SELECT * FROM {table} WHERE {where}")
            return not rows

        for _ in range(100):  # каскад событийный — дожидаемся владельцев
            done = (await _empty("series", "id=2")
                    and await _empty("media_items")
                    and await _empty("renaming_tasks")
                    and await _empty("series_tmdb_mappings"))
            if done:
                break
            await asyncio.sleep(0.02)
        assert await _empty("series", "id=2")
        assert await _empty("media_items")
        assert await _empty("renaming_tasks")
        assert await _empty("series_tmdb_mappings")
    finally:
        await runner.stop()


# --- обновление свойств -----------------------------------------------------------


@pytest.mark.asyncio
async def test_update_without_path_change_triggers_reprocess(system):
    _, db, client, neighbours = system
    resp = await client.post("/api/series/1", json={
        "name": "Переименован", "save_path": "/media/t"})
    assert resp.status_code == 200
    row = await db.fetch_one("SELECT name, save_path FROM series WHERE id=1")
    assert row["name"] == "Переименован"
    assert row["save_path"] == "/media/t"
    for _ in range(100):  # команда фоновая — дожидаемся
        if ("reprocess", {"series_id": 1}) in neighbours.calls:
            break
        await asyncio.sleep(0.02)
    assert ("reprocess", {"series_id": 1}) in neighbours.calls
    assert all(c[0] != "relocate" for c in neighbours.calls)


@pytest.mark.asyncio
async def test_update_with_path_change_calls_relocate(system):
    _, db, client, neighbours = system
    resp = await client.post("/api/series/1", json={
        "name": "Тайтл", "save_path": "/media/new"})
    assert resp.status_code == 200
    assert ("relocate", {"series_id": 1, "new_path": "/media/new"}) \
        in neighbours.calls
    # save_path пишет library после перемещения — gateway его не трогает
    row = await db.fetch_one("SELECT save_path FROM series WHERE id=1")
    assert row["save_path"] == "/media/t"
    assert all(c[0] != "reprocess" for c in neighbours.calls)


# --- мелкие точки ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_auto_scan_and_update_event(system):
    bus, db, client, _ = system
    sub = bus.subscribe("series.updated")
    resp = await client.post("/api/series/1/toggle_auto_scan",
                             json={"enabled": True})
    assert resp.status_code == 200
    env = await asyncio.wait_for(sub.queue.get(), 3)
    # дельта несёт применённые поля + обязательные statuses/is_busy
    assert env.payload["auto_scan_enabled"] is True
    assert env.payload["is_busy"] is False
    assert env.payload["statuses"] == ["waiting"]
    row = await db.fetch_one(
        "SELECT auto_scan_enabled FROM series WHERE id=1")
    assert row["auto_scan_enabled"] == 1

    resp = await client.post("/api/series/1/toggle_auto_scan", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_state_route_drives_viewing(system):
    bus, _, client, neighbours = system
    sub = bus.subscribe("series.status.changed")
    resp = await client.post("/api/series/1/state",
                             json={"state": ["viewing"]})
    assert resp.status_code == 200
    env = await asyncio.wait_for(sub.queue.get(), 3)
    assert env.payload["series_id"] == 1
    assert "viewing" in env.payload["statuses"]
    # открытие модалки торрент-серии запускает сверку файлов (Р-23)
    for _ in range(100):
        if ("fs.verify", {"series_id": 1}) in neighbours.calls:
            break
        await asyncio.sleep(0.02)
    assert ("fs.verify", {"series_id": 1}) in neighbours.calls
    resp = await client.post("/api/series/1/state", json={"state": []})
    assert resp.status_code == 200
    env = await asyncio.wait_for(sub.queue.get(), 3)
    assert "viewing" not in env.payload["statuses"]


@pytest.mark.asyncio
async def test_small_endpoints(system):
    _, db, client, _ = system
    resp = await client.post("/api/series/1/ignored-seasons",
                             json={"seasons": [0, 3]})
    assert resp.status_code == 200
    row = await db.fetch_one(
        "SELECT ignored_seasons FROM series WHERE id=1")
    assert json.loads(row["ignored_seasons"]) == [0, 3]

    resp = await client.put("/api/series/2/vk-quality-priority",
                            json={"priority": ["1080p", "720p"]})
    assert resp.status_code == 200
    resp = await client.put("/api/series/2/vk-quality-priority",
                            json={"priority": "не список"})
    assert resp.status_code == 400

    resp = await client.get("/api/series/1/torrents/history")
    assert resp.json()[0]["torrent_id"] == "abc"

    # прогресс торрентов (Р-23: источник — download_tasks, не конвейер)
    resp = await client.get("/api/series/active_torrents")
    body = resp.json()
    assert body[0]["task_key"] == "h1"
    assert body[0]["series_name"] == "Тайтл"
    assert body[0]["progress"] == 55
