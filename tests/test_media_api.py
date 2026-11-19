"""The points of the operations of a series (Р-21): the composition by its owner, the reprocessing of names, the names of
the source files; the points of the old path of the cutting of compilations are gone with it."""
import sqlite3

import httpx
import pytest

from core import BaseModule, Bus, Runner
from core.contracts.availability import Action, NotAllowed, refusal
from core.db import Database
from modules.catalog import CatalogModule
from modules.gateway import GatewayModule
from modules.scan import ScanModule
from series_factory import insert_series
from settle import settle
from schema_db import migrated

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    migrated(path)
    media_s = tmp_path / "media_s"
    media_s.mkdir()
    with sqlite3.connect(path) as conn:
        insert_series(conn, id=1, url="http://x", source_type="torrent")
        insert_series(conn, id=2, name="ВК", name_en="Show", site="vk", save_path=str(media_s),
                      url="u|q", source_type="vk_video")
        conn.commit()
    return str(path)


class Neighbours(BaseModule):
    name = "neighbours"

    def __init__(self, bus):
        self.calls: list[tuple[str, dict]] = []
        self.sync_running = False
        super().__init__(bus)

    def register(self):
        async def files_for_series(env):
            if env.payload["series_id"] != 1:  # the owner has no torrent files of a video series
                return []
            return [{"id": 1, "original_path": "Dir/T.S01E01.mkv",
                     "renamed_path": None, "status": "renamed",
                     "qb_hash": "h1", "extracted_metadata": None}]

        async def composition_get(env):
            self.calls.append(("composition.get", env.payload))
            return {"series_id": env.payload["series_id"], "rev": 2, "rows": [], "sources": [], "levels": [],
                    "settings": []}

        async def series_run(env):
            self.calls.append(("scan.series.run", env.payload))
            if self.sync_running:
                raise NotAllowed(refusal(Action.SYNC, "scanning"))
            return {"changed": False}

        self.handle("composition.get", composition_get)
        self.handle("torrents.db.files.for_series", files_for_series)
        self.handle("scan.series.run", series_run)


@pytest.fixture
async def system(db_path, tmp_path):
    bus = Bus()
    db = Database(db_path)
    gateway = GatewayModule(bus, static_dir=str(tmp_path), auth_required=False,
                            templates_dir=str(tmp_path))
    neighbours = Neighbours(bus)
    scan = ScanModule(bus, db, scheduler=False)
    runner = Runner(bus, [gateway, CatalogModule(bus, db), scan, neighbours])
    await runner.start()
    transport = httpx.ASGITransport(app=gateway.app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        yield bus, db, client, neighbours
    await runner.stop()


@pytest.mark.asyncio
async def test_the_composition_of_a_series_is_the_layout_of_composition_whatever_its_sources(system):
    _, _, client, neighbours = system
    for series_id in (1, 2):  # a series of a torrent source and one of video sources
        resp = await client.get(f"/api/series/{series_id}/composition")
        assert resp.status_code == 200
        assert ("composition.get",
                {"series_id": series_id, "include_excluded": False, "sources_only": False, "part": "whole",
                 "episodes": []}) in neighbours.calls


@pytest.mark.asyncio
async def test_reprocess_is_a_sync_without_download_and_is_refused_while_one_runs(system):
    """The files take the names of the rules in a sync alone (И-59): the route starts one that fetches nothing and never
    renames by itself; while a sync of the series runs it is refused with the reason of the table of what is allowed."""
    bus, _, client, neighbours = system
    resp = await client.post("/api/series/2/reprocess")
    assert resp.status_code == 200
    assert ("scan.series.run", {"series_id": 2, "download": False}) in neighbours.calls
    neighbours.sync_running = True
    resp = await client.post("/api/series/2/reprocess")
    assert resp.status_code == 409 and "not_allowed" in resp.json()["error"]
    await settle(bus, neighbours)
    assert not any(call[0] == "reprocess" for call in neighbours.calls)


@pytest.mark.asyncio
async def test_source_filenames_are_the_torrent_files_and_a_video_series_has_none(system):
    _, _, client, neighbours = system
    resp = await client.get("/api/series/1/source-filenames")
    assert resp.json() == ["T.S01E01.mkv"]
    # the lines for the rules test of a video series come from composition.preview, not from this list
    resp = await client.get("/api/series/2/source-filenames")
    assert resp.json() == []


@pytest.mark.asyncio
async def test_removed_endpoints_gone(system):
    """Р-21: мёртвые точки удалены."""
    _, _, client, _ = system
    resp = await client.post("/api/series/1/reset_torrents")
    assert resp.status_code in (404, 405)
    resp = await client.post("/api/series/1/relocate",
                             json={"new_path": "/x"})
    assert resp.status_code in (404, 405)
    # stage 10: the merge of slicing and scan answers and the duplicate of reprocess are gone
    assert (await client.get("/api/series/2/sliced-files")).status_code in (404, 405)
    assert (await client.post("/api/series/2/reprocess_vk_files")).status_code in (404, 405)
    # 2026-11-17: the old path of the cutting of compilations is gone with its points
    for method, path in (("get", "/api/series/2/media-items"), ("post", "/api/media-items/u/chapters"),
                         ("post", "/api/media-items/u/slice"), ("post", "/api/series/2/deep-adoption"),
                         ("get", "/api/series/2/rename_preview"), ("get", "/api/slicing/queue"),
                         ("get", "/api/settings/slicing_delete_source")):
        assert (await getattr(client, method)(path)).status_code in (404, 405), path
