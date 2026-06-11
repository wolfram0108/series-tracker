"""Тесты модуля renaming: соседи — фейки, ФС и renaming_tasks настоящие."""
import asyncio
import os
import sqlite3
import subprocess
import sys

import pytest

from core import BaseModule, Bus, BusRequestError, Runner
from core.db import Database
from modules.renaming import RenamingModule


class FakeNeighbours(BaseModule):
    """catalog + rules + scan + downloads + slicing + torrents одним фейком."""
    name = "fake_neighbours"

    def __init__(self, bus, series):
        self.series = series
        self.media_items: list[dict] = []
        self.sliced: list[dict] = []
        self.set_filename_calls: list[dict] = []
        self.set_path_calls: list[dict] = []
        self.qbt_files: dict[str, list] = {}
        self.db_files: dict[str, list] = {}
        self.renames: list[dict] = []
        self.upserts: list[dict] = []
        self.active_torrents: list[dict] = []
        self.fail_format = False
        super().__init__(bus)

    def register(self):
        h = self.handle
        h("catalog.series.get", self.on_series)
        h("rules.format_filename", self.on_format)
        h("rules.format_torrent_file", self.on_format_torrent)
        h("scan.media.list", self.on_media)
        h("downloads.item.set_filename", self.on_set_filename)
        h("slicing.files.list", self.on_sliced)
        h("slicing.file.set_path", self.on_set_path)
        h("torrents.db.active", self.on_active)
        h("torrents.files.get", self.on_qbt_files)
        h("torrents.db.files.list", self.on_db_files)
        h("torrents.rename_file", self.on_rename_file)
        h("torrents.db.files.upsert", self.on_upsert)

    async def on_series(self, env):
        return self.series

    async def on_format(self, env):
        if self.fail_format:
            raise RuntimeError("профиль сломан (имитация)")
        item = env.payload["media_item"]
        ep = env.payload.get("episode_override") or item["episode_start"]
        return {"filename": f"Show s01e{ep:02d}.mp4"}

    async def on_format_torrent(self, env):
        base = env.payload["file_basename"]
        if "skip" in base:
            return {"filename": None, "extracted": {}}
        return {"filename": f"Season 01/Show {base}",
                "extracted": {"episode": 1}}

    async def on_media(self, env):
        return self.media_items

    async def on_set_filename(self, env):
        self.set_filename_calls.append(env.payload)

    async def on_sliced(self, env):
        return self.sliced

    async def on_set_path(self, env):
        self.set_path_calls.append(env.payload)

    async def on_active(self, env):
        return self.active_torrents

    async def on_qbt_files(self, env):
        return self.qbt_files.get(env.payload["hash"], [])

    async def on_db_files(self, env):
        return self.db_files.get(env.payload["qb_hash"], [])

    async def on_rename_file(self, env):
        self.renames.append(env.payload)

    async def on_upsert(self, env):
        self.upserts.append(env.payload)
        return {"count": len(env.payload["files"])}


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
async def system(tmp_path):
    db_path = tmp_path / "test.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{db_path}",
                        "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    media = tmp_path / "media"
    media.mkdir()
    series = {"id": 2, "name_en": "Show", "source_type": "vk_video",
              "save_path": str(media), "parser_profile_id": 1,
              "season": None}
    bus = Bus()
    fake = FakeNeighbours(bus, series)
    renaming = RenamingModule(bus, Database(str(db_path)))
    probe = Probe(bus)
    runner = Runner(bus, [fake, renaming, probe])
    await runner.start()
    yield bus, fake, probe, str(db_path), str(media)
    await runner.stop()


def _item(uid, ep, filename):
    return {"unique_id": uid, "episode_start": ep, "status": "completed",
            "final_filename": filename, "source_title": f"Серия {ep}"}


def _renaming_tasks(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM renaming_tasks")]


@pytest.mark.asyncio
async def test_vk_reprocess_renames_on_disk(system):
    bus, fake, probe, db_path, media = system
    fake.media_items = [_item("u1", 1, "старое имя 1.mp4"),
                        _item("u2", 2, "Show s01e02.mp4")]  # уже правильное
    for f in ("старое имя 1.mp4", "Show s01e02.mp4"):
        open(os.path.join(media, f), "wb").close()
    sub = bus.subscribe("renaming.finished")

    reply = await probe.request("renaming.reprocess", {"series_id": 2},
                                timeout=10)
    assert reply == {"renamed": 1}
    assert os.path.exists(os.path.join(media, "Show s01e01.mp4"))
    assert not os.path.exists(os.path.join(media, "старое имя 1.mp4"))
    # имя в БД обновляет владелец колонки (downloads)
    assert fake.set_filename_calls == [
        {"unique_id": "u1", "filename": "Show s01e01.mp4"}]
    env = await asyncio.wait_for(sub.queue.get(), 2)
    assert env.payload == {"series_id": 2}
    assert _renaming_tasks(db_path) == []  # запись подчищена


@pytest.mark.asyncio
async def test_sliced_children_renamed_with_episode_override(system):
    _, fake, probe, _, media = system
    fake.media_items = [_item("parent", 1, "Show s01e01.mp4")]
    fake.media_items[0]["episode_end"] = 3
    fake.sliced = [{"id": 5, "source_media_item_unique_id": "parent",
                    "episode_number": 2, "file_path": "кусок 2.mp4"}]
    open(os.path.join(media, "кусок 2.mp4"), "wb").close()

    reply = await probe.request("renaming.reprocess", {"series_id": 2},
                                timeout=10)
    assert reply == {"renamed": 1}
    assert os.path.exists(os.path.join(media, "Show s01e02.mp4"))
    assert fake.set_path_calls == [{"id": 5, "path": "Show s01e02.mp4"}]


@pytest.mark.asyncio
async def test_error_carrier_and_reset(system):
    _, fake, probe, db_path, media = system
    fake.media_items = [_item("u1", 1, "x.mp4")]
    open(os.path.join(media, "x.mp4"), "wb").close()
    fake.fail_format = True

    with pytest.raises(BusRequestError, match="профиль сломан"):
        await probe.request("renaming.reprocess", {"series_id": 2},
                            timeout=10)
    tasks = _renaming_tasks(db_path)
    assert tasks[0]["status"] == "error"
    assert "профиль сломан" in tasks[0]["error_message"]

    fake.fail_format = False
    await probe.request("renaming.reprocess", {"series_id": 2}, timeout=10)
    assert _renaming_tasks(db_path) == []


@pytest.mark.asyncio
async def test_process_torrent_renames_in_qbit(system):
    _, fake, probe, _, _ = system
    fake.series = {**fake.series, "source_type": "torrent"}
    fake.qbt_files["h1"] = [{"name": "old/ep1.mkv"},
                            {"name": "readme.txt"},      # не видео
                            {"name": "skip это.mkv"}]    # сезон не определён
    reply = await probe.request("renaming.process_torrent", {
        "series_id": 2, "qb_hash": "h1"}, timeout=10)
    # readme — не видео, skip — без сезона: переименован только ep1
    assert reply == {"renamed": 1}
    assert [r["new_path"] for r in fake.renames] == ["Season 01/Show ep1.mkv"]
    saved = fake.upserts[0]["files"]
    assert [f["original_path"] for f in saved] == ["old/ep1.mkv"]


@pytest.mark.asyncio
async def test_process_torrent_uses_original_path_from_db(system):
    _, fake, probe, _, _ = system
    fake.series = {**fake.series, "source_type": "torrent"}
    # файл уже переименован ранее: текущий путь != original
    fake.qbt_files["h2"] = [{"name": "Season 01/Show ep1.mkv"}]
    fake.db_files["h2"] = [{"original_path": "old/ep1.mkv",
                            "renamed_path": "Season 01/Show ep1.mkv",
                            "status": "renamed"}]
    reply = await probe.request("renaming.process_torrent", {
        "series_id": 2, "qb_hash": "h2"}, timeout=10)
    # имя уже правильное (формат от original basename) — rename не нужен
    assert reply == {"renamed": 0}
    assert fake.renames == []
    assert fake.upserts[0]["files"][0]["original_path"] == "old/ep1.mkv"
