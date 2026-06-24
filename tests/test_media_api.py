"""Тесты блока «media-items и операции серии» этапа 5 (Р-21):
главы/нарезка через готовые контракты slicing, ignore → пересборка
плана, композиция по владельцам, rename_preview, переобработки."""
import asyncio
import os
import sqlite3
import subprocess
import sys

import httpx
import pytest

from core import BaseModule, Bus, Runner
from core.db import Database
from modules.catalog import CatalogModule
from modules.gateway import GatewayModule
from modules.renaming import RenamingModule
from modules.scan import ScanModule
from modules.slicing import SlicingModule

CHAPTERS = [
    {"time": "00:01:00", "title": "Серия 1"},
    {"time": "00:21:00", "title": "Серия 2"},
    {"time": "00:41:00", "title": "Серия 3"},
]


async def fake_fetch(url):
    return [dict(c) for c in CHAPTERS]


async def fake_ffmpeg(source, start, duration, output):
    return True, ""


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{path}",
                        "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    # save_path VK-серии — реальный каталог в tmp: нарезка проверяет
    # наличие исходника на диске (исходник кладёт тест перед slice).
    media_s = tmp_path / "media_s"
    media_s.mkdir()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode, "
            "parser_profile_id) VALUES (1, 'http://x', 'Т', 'T', 'kinozal', "
            "'/media/t', 'waiting', 'torrent', 0, 'search', 1)")
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode, "
            "parser_profile_id) VALUES (2, 'u|q', 'ВК', 'Show', 'vk', ?, "
            "'waiting', 'vk_video', 0, 'get_all', 1)", (str(media_s),))
        # одиночный скачанный эпизод
        conn.execute(
            "INSERT INTO media_items (series_id, unique_id, episode_start, "
            "plan_status, status, is_ignored_by_user, source_url, "
            "publication_date, final_filename, slicing_status, "
            "is_available, source_title) VALUES (2, 'uid1', 1, "
            "'in_plan_single', 'completed', 0, 'http://v/1', "
            "'2026-01-01 00:00:00', 'Show s01e01.mp4', 'none', 1, "
            "'Серия 1')")
        # компиляция для глав/нарезки
        conn.execute(
            "INSERT INTO media_items (series_id, unique_id, episode_start, "
            "episode_end, plan_status, status, is_ignored_by_user, "
            "source_url, publication_date, slicing_status, is_available, "
            "source_title) VALUES (2, 'uid2', 1, 3, 'in_plan_compilation', "
            "'pending', 0, 'http://v/2', '2026-01-02 00:00:00', 'none', 1, "
            "'Серии 1-3')")
        conn.commit()
    return str(path)


class Neighbours(BaseModule):
    name = "neighbours"

    def __init__(self, bus):
        self.calls: list[tuple[str, dict]] = []
        self.active_renaming: list[dict] = []
        super().__init__(bus)

    def register(self):
        async def fs_sync(env):
            self.calls.append(("fs.sync", env.payload))
            return {"adopted": 0, "lost": 0}

        async def rules_apply(env):
            return {"results": [
                {"title": t, "extracted": {"episode": i + 1},
                 "events": [], "excluded": False, "errors": []}
                for i, t in enumerate(env.payload["titles"])],
                "invalid_rules": []}

        async def format_filename(env):
            ep = env.payload.get("episode_override") \
                or env.payload["media_item"].get("episode_start")
            return {"filename": f"Show s01e{ep:02d}.mp4"}

        async def files_for_series(env):
            return [{"id": 1, "original_path": "Dir/T.S01E01.mkv",
                     "renamed_path": None, "status": "renamed",
                     "qb_hash": "h1", "extracted_metadata": None}]

        async def reprocess(env):
            self.calls.append(("reprocess", env.payload))
            return {"renamed": 0}

        async def tasks_active(env):
            return self.active_renaming

        async def torrents_composition(env):
            self.calls.append(("torrents.composition", env.payload))
            return [{"id": 1, "original_path": "Dir/T.S01E01.mkv"}]

        self.handle("downloads.fs.sync", fs_sync)
        self.handle("rules.apply", rules_apply)
        self.handle("rules.format_filename", format_filename)
        self.handle("torrents.db.files.for_series", files_for_series)
        self.handle("renaming.reprocess", reprocess)
        self.handle("renaming.tasks.active", tasks_active)
        self.handle("torrents.composition", torrents_composition)


@pytest.fixture
async def system(db_path, tmp_path):
    bus = Bus()
    db = Database(db_path)
    gateway = GatewayModule(bus, static_dir=str(tmp_path), auth_required=False,
                            templates_dir=str(tmp_path))
    neighbours = Neighbours(bus)
    scan = ScanModule(bus, db, scheduler_tick=None)
    slicing = SlicingModule(bus, db, ffmpeg=fake_ffmpeg,
                            fetch_chapters=fake_fetch)
    runner = Runner(bus, [gateway, CatalogModule(bus, db), scan, slicing,
                          neighbours])
    await runner.start()
    transport = httpx.ASGITransport(app=gateway.app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        yield bus, db, client, neighbours
    await runner.stop()


@pytest.mark.asyncio
async def test_media_items_iso_dates(system):
    _, _, client, _ = system
    resp = await client.get("/api/series/2/media-items")
    items = {i["unique_id"]: i for i in resp.json()}
    assert items["uid1"]["publication_date"] == "2026-01-01T00:00:00"


@pytest.mark.asyncio
async def test_ignore_triggers_plan_update(system):
    bus, db, client, _ = system
    sub = bus.subscribe("scan.plan.updated")
    resp = await client.put("/api/media-items/uid1/ignore",
                            json={"is_ignored": True})
    assert resp.json() == {"success": True}
    env = await asyncio.wait_for(sub.queue.get(), 3)
    assert env.payload == {"series_id": 2}
    row = await db.fetch_one(
        "SELECT is_ignored_by_user FROM media_items WHERE unique_id='uid1'")
    assert row["is_ignored_by_user"] == 1

    resp = await client.put("/api/media-items/uid1/ignore", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_chapters_and_slice_flow(system):
    _, db, client, _ = system
    resp = await client.post("/api/media-items/uid2/chapters")
    assert resp.status_code == 200
    assert [c["title"] for c in resp.json()] == ["Серия 1", "Серия 2",
                                                 "Серия 3"]
    # находка 53: проверка глав НЕ переводит в pending — статус остаётся
    row = await db.fetch_one(
        "SELECT slicing_status FROM media_items WHERE unique_id='uid2'")
    assert row["slicing_status"] == "none"

    # исходник компиляции кладём на диск (нарезка проверяет наличие)
    srow = await db.fetch_one("SELECT save_path FROM series WHERE id=2")
    open(os.path.join(srow["save_path"], "comp.mp4"), "wb").close()
    await db.execute("UPDATE media_items SET final_filename='comp.mp4' "
                     "WHERE unique_id='uid2'")

    # нарезка запускается после проверки глав (не заблокирована)
    resp = await client.post("/api/media-items/uid2/slice")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # теперь задача запущена (pending) — повторный запуск отвергается
    resp = await client.post("/api/media-items/uid2/slice")
    assert resp.status_code == 409

    resp = await client.post("/api/media-items/uid404/chapters")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chapters_filtered_status_message(system):
    _, _, client, _ = system
    resp = await client.post("/api/media-items/uid2/chapters/filtered")
    body = resp.json()
    assert body["expected_count"] == 3
    assert len(body["filtered_chapters"]) == 3
    assert "совпало" in body["status_message"]


@pytest.mark.asyncio
async def test_composition_routes_by_source_type(system):
    _, _, client, neighbours = system
    # торрент-серия → torrents.composition (фейк)
    resp = await client.get("/api/series/1/composition")
    assert resp.json()[0]["original_path"] == "Dir/T.S01E01.mkv"
    assert ("torrents.composition",
            {"series_id": 1, "refresh": False}) in neighbours.calls

    # VK-серия → scan.composition (реальный scan: план + реконструкция)
    resp = await client.get("/api/series/2/composition")
    assert resp.status_code == 200
    plan = {i["unique_id"]: i for i in resp.json()}
    assert plan["uid1"]["result"]["extracted"] == {"episode": 1}
    assert plan["uid1"]["source_data"]["publication_date"] == \
        "2026-01-01T00:00:00"
    assert plan["uid2"]["plan_status"]  # план пересобран
    assert ("fs.sync", {"series_id": 2}) in neighbours.calls


@pytest.mark.asyncio
async def test_rename_preview_shape(db_path, tmp_path):
    bus = Bus()
    db = Database(db_path)
    gateway = GatewayModule(bus, static_dir=str(tmp_path), auth_required=False,
                            templates_dir=str(tmp_path))
    neighbours = Neighbours(bus)
    runner = Runner(bus, [gateway, CatalogModule(bus, db),
                          ScanModule(bus, db, scheduler_tick=None),
                          SlicingModule(bus, db, ffmpeg=fake_ffmpeg,
                                        fetch_chapters=fake_fetch),
                          RenamingModule(bus, db), neighbours])
    await runner.start()
    try:
        transport = httpx.ASGITransport(app=gateway.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            resp = await client.get("/api/series/2/rename_preview")
            body = resp.json()
            by_uid = {p["unique_id"]: p for p in body["preview"]}
            # uid1 уже скачан с верным именем — переименование не нужно
            assert by_uid["uid1"]["current_filename"] == "Show s01e01.mp4"
            assert by_uid["uid1"]["new_filename_preview"] == \
                "Show s01e01.mp4"
            assert body["needs_rename_count"] == 0
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_reprocess_409_and_command(system):
    _, _, client, neighbours = system
    resp = await client.post("/api/series/2/reprocess_vk_files")
    assert resp.status_code == 200
    for _ in range(100):
        if ("reprocess", {"series_id": 2}) in neighbours.calls:
            break
        await asyncio.sleep(0.02)
    assert ("reprocess", {"series_id": 2}) in neighbours.calls

    neighbours.active_renaming = [{"id": 9, "status": "in_progress"}]
    resp = await client.post("/api/series/2/reprocess")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_source_filenames_both_types(system):
    _, _, client, _ = system
    resp = await client.get("/api/series/1/source-filenames")
    assert resp.json() == ["T.S01E01.mkv"]
    resp = await client.get("/api/series/2/source-filenames")
    assert resp.json() == ["Show s01e01.mp4"]


@pytest.mark.asyncio
async def test_removed_endpoints_gone(system):
    """Р-21: мёртвые точки удалены."""
    _, _, client, _ = system
    resp = await client.post("/api/series/1/reset_torrents")
    assert resp.status_code in (404, 405)
    resp = await client.post("/api/series/1/relocate",
                             json={"new_path": "/x"})
    assert resp.status_code in (404, 405)
