"""Тесты модуля downloads: catalog настоящий, rules/settings — фейк,
загрузчик — инжектированная корутина (yt-dlp не нужен).
"""
import asyncio
import os
import sqlite3
import subprocess
import sys

import pytest

from core import BaseModule, Bus, Runner
from core.db import Database
from modules.catalog import CatalogModule
from modules.downloads import DownloadsModule
from modules.downloads import ytdlp


# --- чистые функции разбора yt-dlp ----------------------------------------------

def test_parse_progress_line():
    line = "[download]  42.5% of ~1.20GiB at 5.00MiB/s ETA 01:33"
    data = ytdlp.parse_progress_line(line)
    assert data == {"progress": 42, "total_size_mb": 1228.8,
                    "dlspeed": 5 * 1024 * 1024, "eta": 93}
    assert ytdlp.parse_progress_line("[merge] something") is None


# --- сквозные сценарии ---------------------------------------------------------------

class FakeNeighbours(BaseModule):
    """rules + settings одним фейком."""
    name = "fake_neighbours"

    def __init__(self, bus):
        self.settings = {"max_parallel_downloads": "2"}
        super().__init__(bus)

    def register(self):
        self.handle("rules.format_filename", self.on_format)
        self.handle("settings.value.get", self.on_get)

    async def on_format(self, env):
        item = env.payload["media_item"]
        return {"filename": f"Серия {item['episode_start']:02d}.mp4"}

    async def on_get(self, env):
        return {"key": env.payload["key"],
                "value": self.settings.get(env.payload["key"])}


class FakeDownloader:
    """Управляемый «yt-dlp»: пишет файл или возвращает ошибку."""

    def __init__(self):
        self.fail_urls: set[str] = set()
        self.started: list[str] = []
        self.gate: asyncio.Event | None = None
        self.concurrent = 0
        self.max_concurrent = 0

    async def __call__(self, url, path, on_progress):
        self.started.append(url)
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            await on_progress({"progress": 50, "dlspeed": 1024, "eta": 5,
                               "total_size_mb": 10.0})
            if self.gate:
                await self.gate.wait()
            if url in self.fail_urls:
                return False, "видео недоступно или приватно"
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"video")
            await on_progress({"progress": 100, "dlspeed": 0, "eta": 0})
            return True, ""
        finally:
            self.concurrent -= 1


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
def env_paths(tmp_path):
    db_path = tmp_path / "test.db"
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{db_path}",
                        "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO parser_profiles (id, name) "
                     "VALUES (1, 'профиль')")
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode, "
            "parser_profile_id) VALUES (2, 'https://vkvideo.ru/@c|т', 'Т2', "
            "'T2', 'vk', ?, 'waiting', 'vk_video', 0, 'get_all', 1)",
            (str(media_dir),))
        conn.commit()
    return str(db_path), str(media_dir)


def _add_item(db_path, uid, episode, *, plan="in_plan_single",
              status="pending", filename=None, ignored=0):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO media_items (series_id, unique_id, source_title, "
            "episode_start, plan_status, status, is_ignored_by_user, "
            "source_url, publication_date, slicing_status, is_available, "
            "final_filename) VALUES (2, ?, ?, ?, ?, ?, ?, ?, "
            "'2026-01-01 00:00:00', 'none', 1, ?)",
            (uid, f"Серия {episode}", episode, plan, status, ignored,
             f"https://vk.com/video-1_{episode}", filename))
        conn.commit()


@pytest.fixture
async def system(env_paths):
    db_path, media_dir = env_paths
    bus = Bus()
    db = Database(db_path)
    fake = FakeNeighbours(bus)
    dl = FakeDownloader()
    catalog = CatalogModule(bus, db)
    downloads = DownloadsModule(bus, db, downloader=dl)
    probe = Probe(bus)
    runner = Runner(bus, [fake, catalog, downloads, probe])
    await runner.start()
    yield bus, fake, dl, probe, db_path, media_dir
    await runner.stop()


async def _wait(predicate, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


def _items(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return {r["unique_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM media_items")}


def _tasks(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM download_tasks")]


@pytest.mark.asyncio
async def test_plan_updated_creates_downloads_and_completes(system):
    bus, _, dl, probe, db_path, media_dir = system
    _add_item(db_path, "uid1", 1)
    _add_item(db_path, "uid2", 2, plan="redundant")  # вне плана — без задачи
    sub = bus.subscribe("series.status.contribution")

    probe.publish_event("scan.plan.updated", {"series_id": 2})
    assert await _wait(lambda: _items(db_path)["uid1"]["status"] == "completed")

    items = _items(db_path)
    assert items["uid1"]["final_filename"] == "Серия 01.mp4"
    assert os.path.exists(os.path.join(media_dir, "Серия 01.mp4"))
    assert items["uid2"]["status"] == "pending"  # не трогали
    assert _tasks(db_path) == []  # задача удалена после успеха
    # свёртка дошла до ready (финальная публикуется после завершения
    # воркера — дожидаемся именно её)
    expected = {"downloading": False, "error": False,
                "ready": True, "waiting": False}
    flags = None
    for _ in range(100):
        while not sub.queue.empty():
            flags = sub.queue.get_nowait().payload["flags"]
        if flags == expected:
            break
        await asyncio.sleep(0.02)
    assert flags == expected


@pytest.mark.asyncio
async def test_adoption_skips_download(system):
    _, _, dl, probe, db_path, media_dir = system
    _add_item(db_path, "uid1", 1)
    with open(os.path.join(media_dir, "Серия 01.mp4"), "wb") as f:
        f.write("уже скачан".encode())

    probe.publish_event("scan.plan.updated", {"series_id": 2})
    assert await _wait(lambda: _items(db_path)["uid1"]["status"] == "completed")
    assert dl.started == []  # загрузчик не запускался
    assert _tasks(db_path) == []


@pytest.mark.asyncio
async def test_error_carrier_and_retry_by_next_scan(system):
    _, _, dl, probe, db_path, _ = system
    _add_item(db_path, "uid1", 1)
    dl.fail_urls.add("https://vk.com/video-1_1")

    probe.publish_event("scan.plan.updated", {"series_id": 2})
    assert await _wait(lambda: _items(db_path)["uid1"]["status"] == "error")
    tasks = _tasks(db_path)
    assert len(tasks) == 1 and tasks[0]["status"] == "error"
    assert "недоступно" in tasks[0]["error_message"]

    # следующий скан: error-задача заменяется новой, без дублей
    # (completed пишется на шаг раньше удаления задачи — ждём оба факта)
    dl.fail_urls.clear()
    probe.publish_event("scan.plan.updated", {"series_id": 2})
    assert await _wait(lambda: _items(db_path)["uid1"]["status"] == "completed")
    assert await _wait(lambda: _tasks(db_path) == [])


@pytest.mark.asyncio
async def test_pending_outside_plan_dropped(system):
    _, _, dl, probe, db_path, _ = system
    dl.gate = asyncio.Event()  # держим загрузки, чтобы успеть проверить
    _add_item(db_path, "uid1", 1)
    probe.publish_event("scan.plan.updated", {"series_id": 2})
    assert await _wait(lambda: len(_tasks(db_path)) == 1)

    # план изменился: элемент выпал, задача ещё pending? — нет, уже
    # downloading; смоделируем второй элемент, выпавший до старта
    _add_item(db_path, "uid2", 2)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO download_tasks (task_key, series_id, "
                     "video_url, save_path, status, task_type) VALUES "
                     "('uid2', 2, 'u', '/tmp/x', 'pending', 'vk_video')")
        conn.execute("UPDATE media_items SET plan_status='redundant' "
                     "WHERE unique_id='uid2'")
        conn.commit()

    probe.publish_event("scan.plan.updated", {"series_id": 2})
    assert await _wait(lambda: not any(
        t["task_key"] == "uid2" for t in _tasks(db_path)))
    dl.gate.set()


@pytest.mark.asyncio
async def test_parallel_limit_respected(system):
    _, fake, dl, probe, db_path, _ = system
    fake.settings["max_parallel_downloads"] = "1"
    probe.publish_event("settings.changed",
                        {"key": "max_parallel_downloads", "value": "1"})
    await asyncio.sleep(0.05)

    dl.gate = asyncio.Event()
    for ep in (1, 2, 3):
        _add_item(db_path, f"uid{ep}", ep)
    probe.publish_event("scan.plan.updated", {"series_id": 2})
    assert await _wait(lambda: len(dl.started) >= 1)
    await asyncio.sleep(0.1)
    assert dl.max_concurrent == 1
    dl.gate.set()
    assert await _wait(lambda: all(
        i["status"] == "completed" for i in _items(db_path).values()))
    assert dl.max_concurrent == 1


@pytest.mark.asyncio
async def test_fs_sync_detects_lost_and_adopted(system):
    _, _, dl, probe, db_path, media_dir = system
    # completed, но файла нет — пропажа
    _add_item(db_path, "uid1", 1, status="completed",
              filename="Серия 01.mp4")
    # pending, а файл уже лежит — усыновление
    _add_item(db_path, "uid2", 2)
    with open(os.path.join(media_dir, "Серия 02.mp4"), "wb") as f:
        f.write(b"x")

    dl.gate = asyncio.Event()  # перекачку пропажи держим за воротами
    reply = await probe.request("downloads.fs.sync", {"series_id": 2},
                                timeout=10)
    assert reply == {"adopted": 1, "lost": 1}
    items = _items(db_path)
    assert items["uid2"]["status"] == "completed"
    assert items["uid1"]["status"] in ("pending", "downloading")
    dl.gate.set()


@pytest.mark.asyncio
async def test_interrupted_requeued_on_start_but_error_kept(env_paths):
    db_path, _ = env_paths
    _add_item(db_path, "uid1", 1, status="downloading")
    _add_item(db_path, "uid2", 2, status="error")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO download_tasks (task_key, series_id, "
                     "video_url, save_path, status, task_type) VALUES "
                     "('uid1', 2, 'https://vk.com/video-1_1', '/tmp/a.mp4', "
                     "'downloading', 'vk_video'), "
                     "('uid2', 2, 'u2', '/tmp/b.mp4', 'error', 'vk_video')")
        conn.commit()

    bus = Bus()
    db = Database(db_path)
    dl = FakeDownloader()
    dl.gate = asyncio.Event()
    runner = Runner(bus, [FakeNeighbours(bus), CatalogModule(bus, db),
                          DownloadsModule(bus, db, downloader=dl),
                          Probe(bus)])
    await runner.start()
    try:
        # оборванная — снова запущена; error-задача не тронута (Р-13)
        assert await _wait(lambda: "https://vk.com/video-1_1" in dl.started)
        statuses = {t["task_key"]: t["status"] for t in _tasks(db_path)}
        assert statuses["uid2"] == "error"
        dl.gate.set()
    finally:
        await runner.stop()
