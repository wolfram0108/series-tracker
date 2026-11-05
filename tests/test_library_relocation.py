"""Тесты перемещения сериалов (Р-17): catalog настоящий (busy-механика
проверяется заодно), остальные соседи — фейк, ФС настоящая. Сохранение
карточки только записывает план, файлы двигает синхронизация (И-59)."""
import asyncio
import os
import sqlite3
import subprocess
import sys

import pytest

from core import BaseModule, Bus, BusRequestError, Runner
from core.db import Database
from core.envelope import Envelope
from modules.catalog import CatalogModule
from modules.library import LibraryModule
from series_factory import insert_series
from settle import settle, wait_until


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
async def started(tmp_path):
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
        insert_series(conn, id=2, name="Т", name_en="Show", site="vk", save_path=str(old_dir), state="waiting",
                      url="u|q", source_type="vk_video", vk_search_mode="get_all")
        conn.commit()
    db = Database(str(db_path))
    bus = Bus()
    fake = FakeNeighbours(bus)
    catalog = CatalogModule(bus, db)
    library = LibraryModule(bus, db)
    probe = Probe(bus)
    runner = Runner(bus, [fake, catalog, library, probe])
    await runner.start()
    yield runner, fake, probe, str(db_path), str(old_dir), str(new_dir)
    await runner.stop()


@pytest.fixture
def system(started):
    runner, fake, probe, db_path, old_dir, new_dir = started
    return runner.bus, fake, probe, db_path, old_dir, new_dir


def _save_path(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT save_path FROM series WHERE id=2").fetchone()[0]


def _tasks(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT * FROM relocation_tasks")]


async def plan(probe, path):
    return await probe.request("library.relocation.plan", {"series_id": 2, "new_path": path}, timeout=10)


async def apply(probe):
    return await probe.request("library.relocation.apply", {"series_id": 2}, timeout=10)


@pytest.mark.asyncio
async def test_a_save_plans_the_move_and_the_sync_makes_it(started):
    runner, fake, probe, db_path, old_dir, new_dir = started
    bus = runner.bus
    fake.media_items = [{"unique_id": "u1", "final_filename": "Show s01e01.mp4"}]
    fake.sliced = [{"id": 1, "file_path": "Show s01e02.mp4"}]
    for f in ("Show s01e01.mp4", "Show s01e02.mp4"):
        open(os.path.join(old_dir, f), "wb").close()
    sub_planned = bus.subscribe("library.relocation.planned")
    sub_busy = bus.subscribe("series.busy.changed")

    assert await plan(probe, new_dir) == {"new_path": new_dir}
    assert (await asyncio.wait_for(sub_planned.queue.get(), 3)).payload == {"series_id": 2, "new_path": new_dir}
    await settle(bus, *runner.modules)
    # the save touched no file: the files and the path of the card wait for the sync
    assert sorted(os.listdir(old_dir)) == ["Show s01e01.mp4", "Show s01e02.mp4"] and os.listdir(new_dir) == []
    assert _save_path(db_path) == old_dir
    assert await probe.request("library.relocation.get", {"series_id": 2}, timeout=5) == {"new_path": new_dir}

    assert await apply(probe) == {"moved": True}
    for f in ("Show s01e01.mp4", "Show s01e02.mp4"):
        assert os.path.exists(os.path.join(new_dir, f)) and not os.path.exists(os.path.join(old_dir, f))
    assert _save_path(db_path) == new_dir and _tasks(db_path) == []
    assert fake.reprocess_calls == []  # the names are the sync's own step after the move
    assert await probe.request("library.relocation.get", {"series_id": 2}, timeout=5) == {"new_path": None}
    busy_seq = []

    def busy_ended():
        while not sub_busy.queue.empty():
            busy_seq.append(sub_busy.queue.get_nowait().payload["is_busy"])
        return busy_seq == [True, False]

    await wait_until(busy_ended, bus, *runner.modules)
    assert await apply(probe) == {"moved": False}  # nothing planned: the sync goes on in place


@pytest.mark.asyncio
async def test_a_later_save_replaces_the_plan_and_the_path_the_series_is_in_drops_it(system, tmp_path):
    bus, _, probe, db_path, old_dir, new_dir = system
    other = str(tmp_path / "other")
    await plan(probe, new_dir)
    await plan(probe, other)
    assert [(t["new_path"], t["status"]) for t in _tasks(db_path)] == [(other, "planned")]
    assert await plan(probe, old_dir) == {"new_path": None}
    assert _tasks(db_path) == []


@pytest.mark.asyncio
async def test_torrent_relocation_uses_set_location(system):
    bus, fake, probe, db_path, old_dir, new_dir = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE series_sources SET source_type='torrent' WHERE series_id=2")
        conn.commit()
    fake.active_torrents = [{"qb_hash": "h1"}, {"qb_hash": "h2"}]
    await plan(probe, new_dir)
    assert fake.set_location_calls == []
    assert await apply(probe) == {"moved": True}
    assert [c["hash"] for c in fake.set_location_calls] == ["h1", "h2"]
    assert all(c["location"] == new_dir for c in fake.set_location_calls)
    assert _save_path(db_path) == new_dir


@pytest.mark.asyncio
async def test_a_failed_move_fails_the_sync_keeps_its_plan_and_releases_busy(system):
    bus, fake, probe, db_path, old_dir, new_dir = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE series_sources SET source_type='torrent' WHERE series_id=2")
        conn.commit()
    fake.active_torrents = [{"qb_hash": "h1"}]
    fake.fail_set_location = True
    sub = bus.subscribe("library.relocation.finished")
    await plan(probe, new_dir)
    with pytest.raises(BusRequestError, match="не смог переместить"):
        await apply(probe)
    env = await asyncio.wait_for(sub.queue.get(), 3)
    assert env.payload["success"] is False
    tasks = _tasks(db_path)
    assert [(t["status"], t["new_path"]) for t in tasks] == [("planned", new_dir)]
    assert "не смог переместить" in tasks[0]["error_message"]
    # busy снят несмотря на ошибку (находка 36) — карточка доступна
    series = await probe.request("catalog.series.get", {"series_id": 2}, timeout=5)
    assert series["is_busy"] is False
    assert _save_path(db_path) == old_dir  # путь не переключён
    fake.fail_set_location = False  # the next sync makes the move
    assert await apply(probe) == {"moved": True} and _save_path(db_path) == new_dir


@pytest.mark.asyncio
async def test_a_plan_is_refused_while_a_move_is_made(system):
    _, _, probe, db_path, old_dir, new_dir = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO relocation_tasks (series_id, new_path, "
                     "status) VALUES (2, '/x', 'in_progress')")
        conn.commit()
    with pytest.raises(BusRequestError, match="уже выполняется"):
        await plan(probe, new_dir)


@pytest.mark.asyncio
async def test_relocate_sanitizes_target_path(system, tmp_path):
    """The directory library creates obeys the filesystem-name rule."""
    bus, _, probe, db_path, _, _ = system
    assert await plan(probe, str(tmp_path / "Show: Two?")) == {"new_path": str(tmp_path / "Show Two")}
    await apply(probe)
    assert _save_path(db_path) == str(tmp_path / "Show Two")


@pytest.mark.asyncio
async def test_a_relocation_a_restart_interrupted_is_taken_up_when_the_system_has_started_and_a_plan_is_not(started):
    runner, fake, probe, db_path, old_dir, new_dir = started
    bus = runner.bus
    fake.media_items = [{"unique_id": "u1", "final_filename": "Show s01e01.mp4"}]
    fake.sliced = []
    open(os.path.join(old_dir, "Show s01e01.mp4"), "wb").close()
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO relocation_tasks (series_id, new_path, status, created_at) "
                     "VALUES (2, ?, 'in_progress', '2026-11-02 10:00:00')", (new_dir,))
        conn.commit()
    sub_fin = bus.subscribe("library.relocation.finished")
    await settle(bus, *runner.modules)  # nothing in the module looks for such a task by itself
    assert os.path.exists(os.path.join(old_dir, "Show s01e01.mp4")) and sub_fin.queue.empty()
    bus.publish(Envelope(topic="system.started", kind="event"))
    env = await asyncio.wait_for(sub_fin.queue.get(), 5)
    assert env.payload["success"] is True and os.path.exists(os.path.join(new_dir, "Show s01e01.mp4"))
    assert _save_path(db_path) == new_dir and _tasks(db_path) == []
    # a plan is the sync's: a start does not make it
    await plan(probe, old_dir)
    bus.publish(Envelope(topic="system.started", kind="event"))
    await settle(bus, *runner.modules)
    assert os.path.exists(os.path.join(new_dir, "Show s01e01.mp4")) and _save_path(db_path) == new_dir
