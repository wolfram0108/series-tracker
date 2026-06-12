"""Тесты перемещения сериалов (Р-17): catalog настоящий (busy-механика
проверяется заодно), остальные соседи — фейк, ФС настоящая."""
import asyncio
import os
import sqlite3
import subprocess
import sys

import pytest

from core import BaseModule, Bus, BusRequestError, Runner
from core.db import Database
from modules.catalog import CatalogModule
from modules.library import LibraryModule


class FakeNeighbours(BaseModule):
    name = "fake_neighbours"

    def __init__(self, bus):
        self.media_items: list[dict] = []
        self.sliced: list[dict] = []
        self.active_torrents: list[dict] = []
        self.set_location_calls: list[dict] = []
        self.reprocess_calls: list[int] = []
        self.fail_set_location = False
        super().__init__(bus)

    def register(self):
        self.handle("scan.media.list", self.on_media)
        self.handle("slicing.files.list", self.on_sliced)
        self.handle("torrents.db.active", self.on_active)
        self.handle("torrents.set_location", self.on_set_location)
        self.handle("renaming.reprocess", self.on_reprocess)

    async def on_media(self, env):
        return self.media_items

    async def on_sliced(self, env):
        return self.sliced

    async def on_active(self, env):
        return self.active_torrents

    async def on_set_location(self, env):
        if self.fail_set_location:
            raise RuntimeError("qBittorrent не смог переместить (имитация)")
        self.set_location_calls.append(env.payload)

    async def on_reprocess(self, env):
        self.reprocess_calls.append(env.payload["series_id"])
        return {"renamed": 0}


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
async def system(tmp_path):
    db_path = tmp_path / "test.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{db_path}",
                        "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode) VALUES "
            "(2, 'u|q', 'Т', 'Show', 'vk', ?, 'waiting', 'vk_video', 0, "
            "'get_all')", (str(old_dir),))
        conn.commit()
    db = Database(str(db_path))
    bus = Bus()
    fake = FakeNeighbours(bus)
    catalog = CatalogModule(bus, db)
    library = LibraryModule(bus, db)
    probe = Probe(bus)
    runner = Runner(bus, [fake, catalog, library, probe])
    await runner.start()
    yield bus, fake, probe, str(db_path), str(old_dir), str(new_dir)
    await runner.stop()


async def _wait(predicate, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


def _save_path(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT save_path FROM series WHERE id=2").fetchone()[0]


def _tasks(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT * FROM relocation_tasks")]


@pytest.mark.asyncio
async def test_vk_relocation_moves_files_and_chains_renaming(system):
    bus, fake, probe, db_path, old_dir, new_dir = system
    fake.media_items = [{"unique_id": "u1",
                         "final_filename": "Show s01e01.mp4"}]
    fake.sliced = [{"id": 1, "file_path": "Show s01e02.mp4"}]
    for f in ("Show s01e01.mp4", "Show s01e02.mp4"):
        open(os.path.join(old_dir, f), "wb").close()
    sub_fin = bus.subscribe("library.relocation.finished")
    sub_busy = bus.subscribe("series.busy.changed")

    reply = await probe.request("library.relocate", {
        "series_id": 2, "new_path": new_dir}, timeout=10)
    assert "task_id" in reply

    env = await asyncio.wait_for(sub_fin.queue.get(), 3)
    assert env.payload["success"] is True
    for f in ("Show s01e01.mp4", "Show s01e02.mp4"):
        assert os.path.exists(os.path.join(new_dir, f))
        assert not os.path.exists(os.path.join(old_dir, f))
    assert _save_path(db_path) == new_dir
    assert _tasks(db_path) == []
    assert fake.reprocess_calls == [2]  # цепочка «переместили → переименовали»
    # busy: true на время работы, false по завершении (false приходит
    # после finished — дожидаемся)
    busy_seq = []
    for _ in range(100):
        while not sub_busy.queue.empty():
            busy_seq.append(sub_busy.queue.get_nowait().payload["is_busy"])
        if busy_seq == [True, False]:
            break
        await asyncio.sleep(0.02)
    assert busy_seq == [True, False]


@pytest.mark.asyncio
async def test_torrent_relocation_uses_set_location(system):
    bus, fake, probe, db_path, old_dir, new_dir = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE series SET source_type='torrent' WHERE id=2")
        conn.commit()
    fake.active_torrents = [{"qb_hash": "h1"}, {"qb_hash": "h2"}]
    sub = bus.subscribe("library.relocation.finished")

    await probe.request("library.relocate", {
        "series_id": 2, "new_path": new_dir}, timeout=10)
    env = await asyncio.wait_for(sub.queue.get(), 3)
    assert env.payload["success"] is True
    assert [c["hash"] for c in fake.set_location_calls] == ["h1", "h2"]
    assert all(c["location"] == new_dir for c in fake.set_location_calls)
    assert _save_path(db_path) == new_dir


@pytest.mark.asyncio
async def test_error_keeps_carrier_and_releases_busy(system):
    bus, fake, probe, db_path, _, new_dir = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE series SET source_type='torrent' WHERE id=2")
        conn.commit()
    fake.active_torrents = [{"qb_hash": "h1"}]
    fake.fail_set_location = True
    sub = bus.subscribe("library.relocation.finished")

    await probe.request("library.relocate", {
        "series_id": 2, "new_path": new_dir}, timeout=10)
    env = await asyncio.wait_for(sub.queue.get(), 3)
    assert env.payload["success"] is False
    assert "не смог переместить" in env.payload["message"]
    tasks = _tasks(db_path)
    assert tasks[0]["status"] == "error"
    # busy снят несмотря на ошибку (находка 36) — карточка доступна
    series = await probe.request("catalog.series.get", {"series_id": 2},
                                 timeout=5)
    assert series["is_busy"] is False
    assert _save_path(db_path) != new_dir  # путь не переключён


@pytest.mark.asyncio
async def test_rejects_same_path_and_double_task(system):
    _, _, probe, db_path, old_dir, new_dir = system
    with pytest.raises(BusRequestError, match="не изменился"):
        await probe.request("library.relocate", {
            "series_id": 2, "new_path": old_dir}, timeout=5)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO relocation_tasks (series_id, new_path, "
                     "status) VALUES (2, '/x', 'pending')")
        conn.commit()
    with pytest.raises(BusRequestError, match="уже выполняется"):
        await probe.request("library.relocate", {
            "series_id": 2, "new_path": new_dir}, timeout=5)
