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
from core.db import Database
from modules.catalog import CatalogModule
from modules.catalog.repository import CatalogRepository
from modules.scan import ScanModule


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
    return {"url": f"u|q/{name}", "name": name, "name_en": name,
            "site": "vk", "save_path": f"/media/{name}",
            "source_type": "vk_video", "parser_profile_id": 1}


@pytest.mark.asyncio
async def test_series_id_never_reused(db_path):
    """Номер удалённой серии не занимается новой."""
    repo = CatalogRepository(Database(db_path))
    first = await repo.create_series(_series_data("первая"))
    await repo.delete_series(first)
    second = await repo.create_series(_series_data("вторая"))
    assert second != first
    assert second > first


@pytest.mark.asyncio
async def test_parallel_create_returns_distinct_ids(db_path):
    """Параллельное создание серий: каждый вызов получает свой id."""
    db = Database(db_path)
    repo = CatalogRepository(db)
    ids = await asyncio.gather(
        *(repo.create_series(_series_data(f"с{i}")) for i in range(10)))
    assert len(set(ids)) == 10
    rows = await db.fetch_all("SELECT id FROM series")
    assert sorted(r["id"] for r in rows) == sorted(ids)


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
    ]
    for sql in inserts:
        with pytest.raises(sqlite3.IntegrityError):
            await db.execute(sql)


class SlowSource(BaseModule):
    """Источник видео, отвечающий не сразу: удаление серии застаёт скан
    на середине работы."""
    name = "slow_source"

    def __init__(self, bus):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.settings = {"debug_force_replace": "false"}
        self.videos = [{"url": "https://vkvideo.ru/video-1_2",
                        "title": "Т1 - 1 серия",
                        "publication_date": "2026-03-17T20:13:16Z"}]
        super().__init__(bus)

    def register(self):
        self.handle("sources.vk.scan", self.on_vk)
        self.handle("rules.apply", self.on_rules)
        self.handle("renaming.reprocess", self.on_reprocess)
        self.handle("downloads.fs.sync", self.on_fs_sync)
        self.handle("slicing.verify", self.on_verify)
        self.handle("settings.value.get", self.on_get)

    async def on_get(self, env):
        return {"key": env.payload["key"],
                "value": self.settings.get(env.payload["key"])}

    async def on_vk(self, env):
        self.entered.set()
        await self.release.wait()
        return {"videos": self.videos, "complete": True}

    async def on_rules(self, env):
        return {"results": [{"extracted": {"episode": 1}}], "invalid_rules": []}

    async def on_reprocess(self, env):
        return {"ok": True}

    async def on_fs_sync(self, env):
        return {"adopted": 0, "lost": 0}

    async def on_verify(self, env):
        return {"status": "completed"}


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
        assert await db.fetch_all("SELECT * FROM media_items WHERE series_id=?",
                                  (series_id,)) == []
        assert await db.fetch_all("SELECT * FROM scan_tasks WHERE series_id=?",
                                  (series_id,)) == []
    finally:
        await runner.stop()
