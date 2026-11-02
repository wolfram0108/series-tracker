"""Тесты модуля renaming: соседи — фейки, ФС и renaming_tasks настоящие."""
import asyncio
import os
import sqlite3
import subprocess
import sys

import pytest

from core import BaseModule, Bus, BusRequestError, Runner
from core.db import Database
from core.envelope import Envelope
from modules.renaming import RenamingModule
from series_factory import insert_series
from settle import settle


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
        self.torrent_profiles: list = []
        self.format_series: list[dict] = []
        self.collide_name: str | None = None  # одно имя на все файлы
        self.files_block: "asyncio.Event | None" = None  # C9: блок files.get
        self.files_calls = 0
        self.files_entered = asyncio.Event()  # set when files.get is entered
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
        self.format_series.append(env.payload["series"])
        item = env.payload["media_item"]
        ep = env.payload.get("episode_override") or item["episode_start"]
        return {"filename": f"Show s01e{ep:02d}.mp4"}

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
            "parser_profile_id": profile_id, "vk_search_mode": "search", "vibix_season": None, "players_off": []}


def _torrent(series, profile_id=1):
    """The same series as a torrent one: its single source is a torrent with the profile."""
    return {**series, "kind": "torrent", "sources": [_source("torrent", profile_id)]}


@pytest.fixture
async def started(tmp_path):
    db_path = tmp_path / "test.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{db_path}",
                        "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    media = tmp_path / "media"
    media.mkdir()
    with sqlite3.connect(db_path) as conn:
        insert_series(conn, id=2, name="Show", name_en="Show", site="vk", save_path=str(media), state="waiting",
                      url="u|q/2", source_type="vk_video")
        conn.commit()
    series = {"id": 2, "name_en": "Show", "kind": "video", "save_path": str(media), "season": None,
              "sources": [_source("vk_video", profile_id=1)]}
    bus = Bus()
    fake = FakeNeighbours(bus, series)
    renaming = RenamingModule(bus, Database(str(db_path)))
    probe = Probe(bus)
    runner = Runner(bus, [fake, renaming, probe])
    await runner.start()
    yield bus, fake, renaming, probe, str(db_path), str(media)
    await runner.stop()


@pytest.fixture
def system(started):
    bus, fake, _, probe, db_path, media = started
    return bus, fake, probe, db_path, media


class WatchedLock(asyncio.Lock):
    """A lock that says when someone has to wait for it: a request that waits makes no step the bus could show."""

    def __init__(self) -> None:
        super().__init__()
        self.contended = asyncio.Event()

    async def acquire(self) -> bool:
        if self.locked():
            self.contended.set()
        return await super().acquire()


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
async def test_process_torrent_uses_original_path_from_db(system):
    _, fake, probe, _, _ = system
    fake.series = _torrent(fake.series)
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


@pytest.mark.asyncio
async def test_sim_C9_pipeline_rename_serializes_with_reprocess(started):
    """C9: переименование конвейера (process_torrent) и reprocess (от save)
    делят per-series лок — НЕ переименовывают одни файлы параллельно
    (иначе кривые имена, как у «Путешествия»)."""
    _, fake, renaming, probe, _, media = started
    lock = renaming._locks[2] = WatchedLock()  # the lock of the series, seen
    fake.series = _torrent(fake.series)
    fake.active_torrents = [{"qb_hash": "h", "torrent_id": "t"}]
    fake.qbt_files["h"] = [{"name": "a.mkv"}]
    fake.db_files["h"] = [{"original_path": "a.mkv", "renamed_path": None,
                           "status": "downloaded"}]
    fake.files_block = asyncio.Event()  # конвейерный rename виснет в files.get

    t1 = asyncio.create_task(probe.request(
        "renaming.process_torrent", {"series_id": 2, "qb_hash": "h"},
        timeout=10))
    await asyncio.wait_for(fake.files_entered.wait(), 5)  # it has entered and holds the lock
    t2 = asyncio.create_task(probe.request(
        "renaming.reprocess", {"series_id": 2}, timeout=10))
    await asyncio.wait_for(lock.contended.wait(), 5)  # reprocess is queued for the lock
    assert fake.files_calls == 1  # reprocess ЖДЁТ лок, в files.get не вошёл

    fake.files_block.set()  # отпускаем конвейерный rename
    await asyncio.gather(t1, t2)
    assert fake.files_calls >= 2  # reprocess отработал ПОСЛЕ, не параллельно


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


@pytest.mark.asyncio
async def test_process_torrent_flat_with_subfolders(system):
    """Плоская раздача (без корневой папки торрента) с подкаталогами озвучки."""
    _, fake, probe, _, _ = system
    fake.series = _torrent(fake.series)
    fake.qbt_files["h_flat"] = [
        {"name": "01.mkv", "index": 0},
        {"name": "rus/AniLiberty/01.mka", "index": 1},
        {"name": "Fonts/font.ttf", "index": 2},
    ]

    reply = await probe.request("renaming.process_torrent", {
        "series_id": 2, "qb_hash": "h_flat"}, timeout=10)

    # 01.mkv переименован в Season 01/Show 01.mkv
    # rus/AniLiberty/01.mka переименован в Season 01/Show 01.rus_AniLiberty.mka
    # Fonts/font.ttf уже лежит в Fonts/font.ttf, qBit rename не нужен
    assert reply == {"renamed": 2}
    renames = {r["old_path"]: r["new_path"] for r in fake.renames}
    assert renames["01.mkv"] == "Season 01/Show 01.mkv"
    assert renames["rus/AniLiberty/01.mka"] == "Season 01/Show 01.rus_AniLiberty.mka"

    saved = {f["original_path"]: f for f in fake.upserts[0]["files"]}
    assert saved["Fonts/font.ttf"]["renamed_path"] == "Fonts/font.ttf"
    assert saved["Fonts/font.ttf"]["extracted_metadata"]["is_extra"] is True


@pytest.mark.asyncio
async def test_the_profile_asked_for_is_the_one_of_the_torrent_source(system):
    _, fake, probe, _, _ = system
    web = _source("web_video", profile_id=7, source_id=3)
    fake.series = {**_torrent(fake.series, profile_id=5), "sources": [web, _source("torrent", 5, source_id=4)]}
    fake.qbt_files["hp"] = [{"name": "old/ep1.mkv"}]

    await probe.request("renaming.process_torrent", {"series_id": 2, "qb_hash": "hp"}, timeout=10)

    assert fake.torrent_profiles == [5]


@pytest.mark.asyncio
async def test_a_torrent_series_without_a_profile_is_skipped_by_the_reprocess(system):
    _, fake, probe, _, _ = system
    fake.series = _torrent(fake.series, profile_id=None)
    fake.active_torrents = [{"qb_hash": "h", "torrent_id": "t"}]
    fake.qbt_files["h"] = [{"name": "a.mkv"}]

    reply = await probe.request("renaming.reprocess", {"series_id": 2}, timeout=10)

    assert reply == {"renamed": 0, "skipped": True}
    assert fake.torrent_profiles == [] and fake.renames == []


@pytest.mark.asyncio
async def test_a_video_series_is_renamed_by_its_numbers_without_any_profile(system):
    """The rows carry the numbers of the constructor already; the profile of a source is not asked."""
    _, fake, probe, _, media = system
    fake.series = {**fake.series, "sources": [_source("vk_video", profile_id=None)]}
    fake.media_items = [_item("u1", 1, "старое имя 1.mp4")]
    open(os.path.join(media, "старое имя 1.mp4"), "wb").close()

    reply = await probe.request("renaming.reprocess", {"series_id": 2}, timeout=10)

    assert reply == {"renamed": 1}
    assert os.path.exists(os.path.join(media, "Show s01e01.mp4"))
    assert fake.format_series[0]["kind"] == "video"


@pytest.mark.asyncio
async def test_preview_names_the_rows_of_a_video_series(system):
    _, fake, probe, _, _ = system
    fake.media_items = [_item("u1", 1, "старое имя 1.mp4"), _item("u2", 2, "Show s01e02.mp4")]

    reply = await probe.request("renaming.preview", {"series_id": 2}, timeout=10)

    assert reply["needs_rename_count"] == 1
    assert [(row["unique_id"], row["new_filename_preview"]) for row in reply["preview"]] == [
        ("u1", "Show s01e01.mp4"), ("u2", "Show s01e02.mp4")]


@pytest.mark.asyncio
async def test_preview_gives_every_row_the_numbers_of_its_slot(system):
    _, fake, probe, _, _ = system
    fake.media_items = [{**_item("u1", 3, "a.mp4"), "season": 2, "part": 1}]

    reply = await probe.request("renaming.preview", {"series_id": 2}, timeout=10)

    assert [(row["season"], row["episode"], row["part"]) for row in reply["preview"]] == [(2, 3, 1)]


@pytest.mark.asyncio
async def test_a_reprocess_a_restart_interrupted_is_run_again_when_the_system_has_started_and_not_before(started):
    bus, fake, renaming, probe, db_path, media = started
    fake.media_items = [_item("u1", 1, "старое имя 1.mp4")]
    open(os.path.join(media, "старое имя 1.mp4"), "wb").close()
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO renaming_tasks (series_id, status, attempts, created_at, task_type, task_data) "
                     "VALUES (2, 'in_progress', 0, '2026-11-02 10:00:00', 'reprocess', '{}')")
        conn.commit()
    sub = bus.subscribe("renaming.finished")
    await settle(bus, renaming)  # nothing in the module looks for such a task by itself
    assert os.path.exists(os.path.join(media, "старое имя 1.mp4")) and sub.queue.empty()
    bus.publish(Envelope(topic="system.started", kind="event"))
    env = await asyncio.wait_for(sub.queue.get(), 5)
    assert env.payload == {"series_id": 2}
    assert os.path.exists(os.path.join(media, "Show s01e01.mp4")) and _renaming_tasks(db_path) == []
