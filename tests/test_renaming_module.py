"""The module renaming: the neighbours are fakes, the file system is real."""
import asyncio
import sqlite3

import pytest

from core.bus import Bus
from core.module import BaseModule
from core.runner import Runner
from modules.renaming import RenamingModule
from series_factory import insert_series
from schema_db import migrated


class FakeNeighbours(BaseModule):
    """catalog + rules + torrents одним фейком."""
    name = "fake_neighbours"

    def __init__(self, bus, series):
        self.series = series
        self.rename_calls: list[dict] = []
        self.qbt_files: dict[str, list] = {}
        self.db_files: dict[str, list] = {}
        self.renames: list[dict] = []
        self.upserts: list[dict] = []
        self.active_torrents: list[dict] = []
        self.torrent_profiles: list = []
        self.collide_name: str | None = None  # одно имя на все файлы
        self.files_block: "asyncio.Event | None" = None  # C9: блок files.get
        self.files_calls = 0
        self.files_entered = asyncio.Event()  # set when files.get is entered
        super().__init__(bus)

    def register(self):
        h = self.handle
        h("catalog.series.get", self.on_series)
        h("rules.format_torrent_file", self.on_format_torrent)
        h("torrents.db.active", self.on_active)
        h("torrents.files.get", self.on_qbt_files)
        h("torrents.db.files.list", self.on_db_files)
        h("torrents.rename_file", self.on_rename_file)
        h("torrents.db.files.upsert", self.on_upsert)

    async def on_series(self, env):
        return self.series

    async def on_format_torrent(self, env):
        self.torrent_profiles.append(env.payload["profile_id"])
        base = env.payload["file_basename"]
        if "skip" in base:
            return {"filename": None, "extracted": {}}
        if self.collide_name:
            return {"filename": self.collide_name,
                    "extracted": {"episode": 1}}
        return {"filename": f"Season 01/Show {base}",
                "extracted": {"episode": 1}}

    async def on_active(self, env):
        return self.active_torrents

    async def on_qbt_files(self, env):
        self.files_calls += 1
        self.files_entered.set()
        if self.files_block is not None and self.files_calls == 1:
            await self.files_block.wait()  # C9: держим конвейерный rename
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


def _source(source_type, profile_id=None, source_id=1):
    """A source as catalog.series.get answers it."""
    return {"id": source_id, "series_id": 2, "position": 0, "url": "u|q/2", "source_type": source_type,
            "parser_profile_id": profile_id, "vibix_season": None, "players_off": []}


def _torrent(series, profile_id=1):
    """The same series as a torrent one: its single source is a torrent with the profile."""
    return {**series, "sources": [_source("torrent", profile_id)]}


@pytest.fixture
async def started(tmp_path):
    db_path = tmp_path / "test.db"
    migrated(db_path)
    media = tmp_path / "media"
    media.mkdir()
    with sqlite3.connect(db_path) as conn:
        insert_series(conn, id=2, name="Show", name_en="Show", site="vk", save_path=str(media), state="waiting",
                      url="u|q/2", source_type="vk_video")
        conn.commit()
    series = {"id": 2, "name_en": "Show", "save_path": str(media), "season": None,
              "sources": [_source("vk_video", profile_id=1)]}
    bus = Bus()
    fake = FakeNeighbours(bus, series)
    renaming = RenamingModule(bus)
    probe = Probe(bus)
    runner = Runner(bus, [fake, renaming, probe])
    await runner.start()
    yield bus, fake, renaming, probe, str(db_path), str(media)
    await runner.stop()


@pytest.fixture
def system(started):
    bus, fake, _, probe, db_path, media = started
    return bus, fake, probe, db_path, media


@pytest.mark.asyncio
async def test_process_torrent_renames_in_qbit(system):
    _, fake, probe, _, _ = system
    fake.series = _torrent(fake.series)
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
    assert fake.torrent_profiles == [1, 1]  # the profile of the torrent source goes with every file


@pytest.mark.asyncio
async def test_dry_run_writes_composition_without_touching_qbit(system):
    """Состав раздачи нужен до закачки: правила применяются, qBit — нет."""
    _, fake, probe, _, _ = system
    fake.series = _torrent(fake.series)
    fake.qbt_files["h3"] = [{"name": "old/ep1.mkv", "index": 4},
                            {"name": "cover.jpg", "index": 5}]

    reply = await probe.request("renaming.process_torrent", {
        "series_id": 2, "qb_hash": "h3", "dry_run": True}, timeout=10)

    assert reply == {"renamed": 0}
    assert fake.renames == [], "в режиме предпросмотра qBit не трогаем"
    saved = fake.upserts[0]["files"]
    assert saved[0]["status"] == "pending_rename"
    assert saved[0]["renamed_path"] is None
    assert saved[0]["qb_file_index"] == 4


@pytest.mark.asyncio
async def test_process_torrent_collision_not_merged(system):
    """Находка 63 (консистентность БД↔qBit): если правила дают двум
    файлам ОДНО имя — это коллизия. Второй файл помечается rename_error
    и НЕ отправляется в qBit (qBit мог бы принять переименование в
    занятый путь и слить файлы, а БД соврала бы «renamed»)."""
    _, fake, probe, _, _ = system
    fake.series = _torrent(fake.series)
    fake.collide_name = "Season 01/Show one.mkv"
    fake.qbt_files["hc"] = [{"name": "old/a.mkv"}, {"name": "old/b.mkv"}]
    reply = await probe.request("renaming.process_torrent", {
        "series_id": 2, "qb_hash": "hc"}, timeout=10)
    # переименован только первый; второй — коллизия, в qBit не ушёл
    assert reply == {"renamed": 1}
    assert [r["new_path"] for r in fake.renames] == ["Season 01/Show one.mkv"]
    saved = {f["original_path"]: f["status"] for f in fake.upserts[0]["files"]}
    assert saved["old/a.mkv"] == "renamed"
    assert saved["old/b.mkv"] == "rename_error"


@pytest.mark.asyncio
async def test_process_torrent_audio_subs_and_fonts(system):
    """Раздача со сложной структурой (дорожки озвучки, субтитры, шрифты)."""
    _, fake, probe, _, _ = system
    fake.series = _torrent(fake.series)
    root = "Katainaka no Ossan [WEB-DL 1080p]"
    fake.qbt_files["h_complex"] = [
        {"name": f"{root}/[VARYG] Katainaka - 01 [1080p].mkv", "index": 0},
        {"name": f"{root}/RUS Sound/AniLiberty/[VARYG] Katainaka - 01 [1080p].AniLiberty.mka", "index": 1},
        {"name": f"{root}/RUS Subs/Alvakarp/[VARYG] Katainaka - 01 [1080p].Alvakarp.rus.ass", "index": 2},
        {"name": f"{root}/RUS Subs/Fonts/Gabriela-Regular.ttf", "index": 3},
        {"name": f"{root}/readme.txt", "index": 4},
    ]

    reply = await probe.request("renaming.process_torrent", {
        "series_id": 2, "qb_hash": "h_complex"}, timeout=10)

    # Переименованы 4 файла: видео, аудио, субтитры и шрифт (отсечена папка root).
    # readme.txt в корне пропущен.
    assert reply == {"renamed": 4}

    renames = {r["old_path"]: r["new_path"] for r in fake.renames}
    assert renames[f"{root}/[VARYG] Katainaka - 01 [1080p].mkv"] == (
        "Season 01/Show [VARYG] Katainaka - 01 [1080p].mkv"
    )
    assert renames[f"{root}/RUS Sound/AniLiberty/[VARYG] Katainaka - 01 [1080p].AniLiberty.mka"] == (
        "Season 01/Show [VARYG] Katainaka - 01 [1080p].RUS_Sound_AniLiberty.mka"
    )
    assert renames[f"{root}/RUS Subs/Alvakarp/[VARYG] Katainaka - 01 [1080p].Alvakarp.rus.ass"] == (
        "Season 01/Show [VARYG] Katainaka - 01 [1080p].RUS_Subs_Alvakarp.ass"
    )
    # Шрифты переносятся в исходной структуре без корневой папки торрента
    assert renames[f"{root}/RUS Subs/Fonts/Gabriela-Regular.ttf"] == (
        "RUS Subs/Fonts/Gabriela-Regular.ttf"
    )
    assert f"{root}/readme.txt" not in renames

    saved = {f["original_path"]: f for f in fake.upserts[0]["files"]}
    # Проверяем метаданные
    audio_meta = saved[f"{root}/RUS Sound/AniLiberty/[VARYG] Katainaka - 01 [1080p].AniLiberty.mka"]["extracted_metadata"]
    assert audio_meta["media_type"] == "audio"
    assert audio_meta["episode"] == 1

    subs_meta = saved[f"{root}/RUS Subs/Alvakarp/[VARYG] Katainaka - 01 [1080p].Alvakarp.rus.ass"]["extracted_metadata"]
    assert subs_meta["media_type"] == "subtitles"
    assert subs_meta["episode"] == 1

    font_meta = saved[f"{root}/RUS Subs/Fonts/Gabriela-Regular.ttf"]["extracted_metadata"]
    assert font_meta["is_extra"] is True
    assert font_meta["media_type"] == "extra"
