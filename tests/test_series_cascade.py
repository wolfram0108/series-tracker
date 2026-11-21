"""Инварианты каскада удаления серии (Р-19 и его починка).

Свойства, которые не видны в обычном сценарии: номера серий не
переиспользуются, серийные таблицы не принимают запись несуществующей
серии, удаление под идущим сканом не оставляет строк.
"""
import asyncio
import sqlite3

import pytest

from core.bus import Bus
from core.module import BaseModule
from core.runner import Runner
from core.contracts import ReleaseTable, TableGetReply, TableState
from core.db import Database
from modules.catalog import CatalogModule
from modules.scan import ScanModule
from series_factory import source
from settle import wait_until
from schema_db import migrated


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    migrated(path)
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


class SlowSource(BaseModule):
    """The table of a page that is read slowly: the deletion of a series finds the scan in the middle of its work."""
    name = "slow_source"

    def __init__(self, bus):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.asked: list[str] = []
        super().__init__(bus)

    def register(self):
        self.handle("sources.table.refresh", self.on_table, concurrent=True)
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


@pytest.mark.asyncio
async def test_delete_during_scan_leaves_no_rows(db_path):
    """Удаление серии под идущим сканом: строк не остаётся, скан
    заканчивается без ошибки."""
    bus = Bus()
    db = Database(db_path)
    catalog = CatalogModule(bus, db)
    source = SlowSource(bus)
    scan = ScanModule(bus, db, scheduler=False)
    runner = Runner(bus, [catalog, source, scan])
    await runner.start()
    try:
        created = await catalog.request("catalog.series.create",
                                        {"data": _series_data("Т1")})
        series_id = created.id
        task = asyncio.create_task(catalog.request(
            "scan.series.run", {"series_id": series_id}))
        await asyncio.wait_for(source.entered.wait(), 10)
        await catalog.request("catalog.series.delete",
                              {"series_id": series_id})
        # The source holds an envelope until released, so the bus never goes quiet: the state of scan is the signal.
        assert await wait_until(lambda: series_id in scan._aborted, bus, scan)
        source.release.set()
        result = await asyncio.wait_for(task, 15)
        assert result.aborted is True
        assert source.asked == ["sources.table.refresh"]  # the scan went no further than the step it was in
        assert await db.fetch_all("SELECT * FROM scan_tasks WHERE series_id=?",
                                  (series_id,)) == []
    finally:
        await runner.stop()
