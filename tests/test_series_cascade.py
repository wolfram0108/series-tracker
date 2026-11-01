"""Инварианты каскада удаления серии (Р-19 и его починка).

Свойства, которые не видны в обычном сценарии: номера серий не
переиспользуются, серийные таблицы не принимают запись несуществующей
серии, удаление под идущим сканом не оставляет строк.
"""
import asyncio
import sqlite3
import subprocess
import sys

import pytest

from core import BaseModule, Bus, Runner
from core.contracts import ReleaseTable, TableGetReply, TableState
from core.db import Database
from modules.catalog import CatalogModule
from modules.catalog.repository import CatalogRepository
from modules.scan import ScanModule
from series_factory import create_series, source


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO parser_profiles (id, name) "
                     "VALUES (1, 'профиль')")
        conn.commit()
    return str(path)


def _series_data(name: str) -> dict:
    return {"name": name, "name_en": name, "site": "vk",
            "save_path": f"/media/{name}",
            "sources": [source(url=f"u|q/{name}", source_type="vk_video",
                               parser_profile_id=1)]}


@pytest.mark.asyncio
async def test_series_id_never_reused(db_path):
    """Номер удалённой серии не занимается новой."""
    db = Database(db_path)
    first = (await create_series(db, **_series_data("первая"))).series_id
    await CatalogRepository(db).delete_series(first)
    second = (await create_series(db, **_series_data("вторая"))).series_id
    assert second != first
    assert second > first


@pytest.mark.asyncio
async def test_source_id_never_reused(db_path):
    """The number of a deleted source is not given to a new one: settings refer to it."""
    db = Database(db_path)
    first = await create_series(db, **_series_data("первая"))
    await CatalogRepository(db).delete_series(first.series_id)
    second = await create_series(db, **_series_data("вторая"))
    assert second.source_id > first.source_id


@pytest.mark.asyncio
async def test_parallel_create_returns_distinct_ids(db_path):
    """Параллельное создание серий: каждый вызов получает свой id."""
    db = Database(db_path)
    created = await asyncio.gather(
        *(create_series(db, **_series_data(f"с{i}")) for i in range(10)))
    ids = [c.series_id for c in created]
    assert len(set(ids)) == 10
    rows = await db.fetch_all("SELECT id FROM series")
    assert sorted(r["id"] for r in rows) == sorted(ids)
    # every series has its own single source
    sources = await db.fetch_all("SELECT id, series_id FROM series_sources")
    assert sorted(s["series_id"] for s in sources) == sorted(ids)
    assert sorted(s["id"] for s in sources) == sorted(c.source_id for c in created)


@pytest.mark.asyncio
async def test_child_tables_reject_unknown_series(db_path):
    """Серийная таблица не принимает запись несуществующей серии."""
    db = Database(db_path)
    inserts = [
        "INSERT INTO scan_tasks (series_id, created_at, status, task_data, "
        "results_data) VALUES (9999, '2026-01-01', 'processing', '[]', '{}')",
        "INSERT INTO download_tasks (task_key, series_id, status, task_type) "
        "VALUES ('k', 9999, 'pending', 'vk_video')",
        "INSERT INTO slicing_tasks (media_item_unique_id, series_id, status) "
        "VALUES ('u', 9999, 'pending')",
        "INSERT INTO renaming_tasks (series_id, status, task_type) "
        "VALUES (9999, 'error', 'mass_vk_reprocess')",
        "INSERT INTO relocation_tasks (series_id, new_path, status) "
        "VALUES (9999, '/media/new', 'processing')",
        "INSERT INTO series_sources (series_id, url, source_type) "
        "VALUES (9999, 'u|q', 'vk_video')",
    ]
    for sql in inserts:
        with pytest.raises(sqlite3.IntegrityError):
            await db.execute(sql)


class SlowSource(BaseModule):
    """The table of a page that is read slowly: the deletion of a series finds the scan in the middle of its work."""
    name = "slow_source"

    def __init__(self, bus):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.asked: list[str] = []
        super().__init__(bus)

    def register(self):
        self.handle("sources.table.get", self.on_table, concurrent=True)
        self.handle("renaming.reprocess", self.on_reprocess)
        self.handle("downloads.fs.sync", self.on_fs_sync)
        self.handle("settings.value.get", self.on_get)

    async def on_get(self, env):
        return {"key": env.payload["key"], "value": None}

    async def on_table(self, env):
        self.asked.append(env.topic)
        self.entered.set()
        await self.release.wait()
        url = env.payload["url"]
        table = ReleaseTable(url=url, site="vk_channel", complete=True)
        return TableGetReply(url=url, state=TableState.READY, table=table).model_dump(mode="json")

    async def on_reprocess(self, env):
        return {"ok": True}

    async def on_fs_sync(self, env):
        return {"adopted": 0, "lost": 0}


@pytest.mark.asyncio
async def test_delete_during_scan_leaves_no_rows(db_path):
    """Удаление серии под идущим сканом: строк не остаётся, скан
    заканчивается без ошибки."""
    bus = Bus()
    db = Database(db_path)
    catalog = CatalogModule(bus, db)
    source = SlowSource(bus)
    scan = ScanModule(bus, db, scheduler_tick=None)
    runner = Runner(bus, [catalog, source, scan])
    await runner.start()
    try:
        created = await catalog.request("catalog.series.create",
                                        {"data": _series_data("Т1")})
        series_id = created["id"]
        task = asyncio.create_task(catalog.request(
            "scan.series.run", {"series_id": series_id}))
        await asyncio.wait_for(source.entered.wait(), 10)
        await catalog.request("catalog.series.delete",
                              {"series_id": series_id})
        for _ in range(100):  # ждём, пока владелец scan увидит удаление
            if series_id in scan._aborted:
                break
            await asyncio.sleep(0.02)
        source.release.set()
        result = await asyncio.wait_for(task, 15)
        assert result.get("aborted") is True
        assert source.asked == ["sources.table.get"]  # the scan went no further than the step it was in
        assert await db.fetch_all("SELECT * FROM media_items WHERE series_id=?",
                                  (series_id,)) == []
        assert await db.fetch_all("SELECT * FROM scan_tasks WHERE series_id=?",
                                  (series_id,)) == []
    finally:
        await runner.stop()
