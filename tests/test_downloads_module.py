"""Тесты модуля downloads: catalog настоящий, rules/settings — фейк,
загрузчик — инжектированная корутина (yt-dlp не нужен).
"""
import asyncio
import logging
import os
import sqlite3
import subprocess
import sys

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from packing_support import FakePacker
from core import BaseModule, Bus, BusRequestError, Envelope, Runner
from core.db import Database
from modules.catalog import CatalogModule
from modules.downloads import DownloadsModule
from modules.downloads import ytdlp
from modules.downloads.module import source_of_candidate
from series_factory import insert_series, source
from settle import settle, wait_until


# --- чистые функции разбора yt-dlp ----------------------------------------------

def test_parse_progress_line():
    line = "[download]  42.5% of ~1.20GiB at 5.00MiB/s ETA 01:33"
    data = ytdlp.parse_progress_line(line)
    assert data == {"phase": "download", "progress": 42,
                    "total_size_mb": 1228.8,
                    "dlspeed": 5 * 1024 * 1024, "eta": 93}
    assert ytdlp.parse_progress_line("[merge] something") is None
    # формат hlsnative+-N: пробелы после '~', ETA Unknown, хвост (frag)
    hls = ytdlp.parse_progress_line(
        "[download]   3.0% of ~  24.82GiB at    5.00MiB/s ETA Unknown "
        "(frag 138/4602)")
    assert hls["progress"] == 3
    assert hls["dlspeed"] == 5 * 1024 * 1024
    assert hls["eta"] == 0          # Unknown -> 0
    # и обычная hls-строка с временем ETA
    hls2 = ytdlp.parse_progress_line(
        "[download]  55.0% of ~  24.82GiB at  60.00MiB/s ETA 03:30 "
        "(frag 2500/4602)")
    assert hls2["progress"] == 55 and hls2["eta"] == 210


def test_ffmpeg_progress_percent_and_eta():
    # remux обработал 30с из 120с со скоростью 60x: 25%, ETA = 90/60 = 1с
    out = ytdlp.ffmpeg_progress(
        {"out_time": "00:00:30.000000", "speed": "60x",
         "progress": "continue"}, duration=120.0)
    assert out == {"phase": "remux", "progress": 25, "eta": 1, "speed": 60.0}
    assert ytdlp.ffmpeg_progress(
        {"out_time": "00:00:30.000000", "speed": "60x"},
        duration=120.0, phase="download")["phase"] == "download"
    # нулевая длительность не делит на ноль
    assert ytdlp.ffmpeg_progress({"out_time": "00:00:10", "speed": "1x"},
                                 0.0)["progress"] == 0


def test_artifacts_handle_glob_metachars_in_path(tmp_path):
    """Каталоги медиатеки именуются с [tmdbid-NNNN] — это спецсимволы glob.
    Поиск скачанного файла и подбор артефактов обязаны работать строками, а
    не glob: glob трактует [tmdbid-…] как класс символов и не находит реально
    существующий файл (ложное «yt-dlp не оставил файл» + неубранные сироты)."""
    series_dir = tmp_path / "Tales of Herding Gods (2024) [tmdbid-236534]"
    series_dir.mkdir()
    full = str(series_dir / "Tales of Herding Gods s01e87 2160p.mp4")
    stem = os.path.splitext(full)[0]

    # фаза download оставила готовый промежуточный файл — должен находиться
    open(full + ".download.mp4", "wb").close()
    assert ytdlp._download_artifact(full) == full + ".download.mp4"

    # полный набор артефактов для чистки (раньше glob возвращал [] из-за скобок)
    open(full + ".remux.mp4", "wb").close()
    open(full + ".part", "wb").close()
    open(stem + ".f140.mp4", "wb").close()
    open(stem + ".ytdl", "wb").close()
    arts = set(ytdlp.download_artifacts(full))
    assert arts == {full + ".download.mp4", full + ".remux.mp4",
                    full + ".part", stem + ".f140.mp4", stem + ".ytdl"}

    # сам финальный файл артефактом НЕ считается (его не сносим как мусор)
    open(full, "wb").close()
    assert full not in ytdlp.download_artifacts(full)


# --- сквозные сценарии ---------------------------------------------------------------

class FakeNeighbours(BaseModule):
    """rules + settings одним фейком."""
    name = "fake_neighbours"

    def __init__(self, bus):
        self.settings = {"max_parallel_downloads": "2"}
        self.stream_replies: dict[str, dict] = {}
        self.stream_raises: set[str] = set()
        self.stream_requests: list[str] = []
        self.stream_payloads: list[dict] = []
        # What packing asks (stage 11): the reference of each episode and the order of the voice-overs of each season.
        self.references: list[dict] = []
        self.orders: list[dict] = []
        super().__init__(bus)

    def register(self):
        self.handle("rules.format_filename", self.on_format)
        self.handle("settings.value.get", self.on_get)
        self.handle("sources.video.stream", self.on_stream)
        self.handle("scan.output.get", self.on_references)
        self.handle("composition.get", self.on_layout)

    async def on_references(self, env):
        return self.references

    async def on_layout(self, env):
        return {"voice_orders": self.orders}

    async def on_stream(self, env):
        url = env.payload["url"]
        self.stream_requests.append(url)
        self.stream_payloads.append(dict(env.payload))
        if url in self.stream_raises:
            raise RuntimeError("sources unavailable")
        return self.stream_replies.get(url, {"handled": False})

    async def on_format(self, env):
        item = env.payload["media_item"]
        packed = (env.payload.get("original_filename") or "").endswith(".mkv")
        return {"filename": f"Серия {item['episode_start']:02d}.{'mkv' if packed else 'mp4'}"}

    async def on_get(self, env):
        return {"key": env.payload["key"],
                "value": self.settings.get(env.payload["key"])}


class FakeDownloader:
    """Управляемый «yt-dlp»: пишет файл или возвращает ошибку."""

    # When set, entering the downloader is a step of the bus: a general fetch reports no progress, so without it nothing
    # tells a waiter that the downloader was called.
    bus: Bus | None = None

    def __init__(self):
        self.fail_urls: set[str] = set()
        self.timeouts: dict[str, int] = {}   # url -> how many more attempts end in a network timeout
        self.started: list[str] = []
        self.kwargs: dict[str, dict] = {}
        self.gate: asyncio.Event | None = None
        self.concurrent = 0
        self.max_concurrent = 0

    async def __call__(self, url, path, on_progress, *, threads=6, **kw):
        self.threads = threads
        self.started.append(url)
        self.kwargs[url] = kw
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        if self.bus is not None:
            self.bus.publish(Envelope(topic="test.downloader.entered", kind="event"))
        try:
            await on_progress({"progress": 50, "dlspeed": 1024, "eta": 5,
                               "total_size_mb": 10.0})
            if self.gate:
                await self.gate.wait()
            if url in self.fail_urls:
                return False, "видео недоступно или приватно"
            if self.timeouts.get(url, 0):
                self.timeouts[url] -= 1
                return False, "ffmpeg direct download: Connection timed out"
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
        # 2: a VK series; 3: a web one; 4: one series with both kinds of source (position 0 is VK, 1 is web).
        insert_series(conn, id=2, name="Т2", name_en="T2", site="vk", save_path=str(media_dir), state="waiting",
                      url="https://vkvideo.ru/@c|т", source_type="vk_video", vk_search_mode="get_all",
                      parser_profile_id=1)
        insert_series(conn, id=3, name="Т3", name_en="T3", site="1.ruchime.org", save_path=str(media_dir),
                      state="waiting", url="https://1.ruchime.org/release/x", source_type="web_video",
                      vk_search_mode="get_all", parser_profile_id=1)
        insert_series(conn, id=4, name="Т4", name_en="T4", site="mixed", save_path=str(media_dir), state="waiting",
                      sources=[source(url="https://vkvideo.ru/@c|т4", source_type="vk_video", parser_profile_id=1),
                               source(url="https://1.ruchime.org/release/y", source_type="web_video",
                                      parser_profile_id=1)])
        # 5: a torrent series on a path that is not there (a downed NAS), whose files are not this module's
        insert_series(conn, id=5, name="Т5", name_en="T5", site="kinozal", save_path="/nonexistent/nas",
                      url="http://kinozal.tv/details.php?id=5", source_type="torrent")
        conn.commit()
    return str(db_path), str(media_dir)


def _add_item(db_path, uid, episode, *, plan="in_plan_single",
              status="pending", filename=None, ignored=0, download_url=None,
              series_id=2, source_url=None, superseded_by=None, source_id=None):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO media_items (series_id, unique_id, source_title, "
            "episode_start, plan_status, status, is_ignored_by_user, "
            "source_url, publication_date, slicing_status, is_available, "
            "final_filename, download_url, superseded_by, source_id) VALUES (?, ?, ?, ?, ?, "
            "?, ?, ?, '2026-01-01 00:00:00', 'none', 1, ?, ?, ?, ?)",
            (series_id, uid, f"Серия {episode}", episode, plan, status, ignored,
             source_url or f"https://vk.com/video-1_{episode}", filename,
             download_url, superseded_by, source_id))
        conn.commit()


def _source_id(db_path, series_id, position=0):
    """The id of the source of a series at a position, as the factory wrote it."""
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT id FROM series_sources WHERE series_id=? AND position=?",
                            (series_id, position)).fetchone()[0]


@pytest.fixture
async def system(env_paths):
    db_path, media_dir = env_paths
    bus = Bus()
    db = Database(db_path)
    fake = FakeNeighbours(bus)
    dl = FakeDownloader()
    dl.bus = bus
    catalog = CatalogModule(bus, db)
    downloads = DownloadsModule(bus, db, downloader=dl, packer=FakePacker())
    probe = Probe(bus)
    runner = Runner(bus, [fake, catalog, downloads, probe])
    await runner.start()
    _watch(runner, downloads)
    yield bus, fake, dl, probe, db_path, media_dir
    await runner.stop()
    _watch(None, None)


# The system a test started, for the waiters below: the fixtures (and tests) that start one say which it is.
_running = SimpleNamespace(bus=None, modules=(), downloads=None)


def _watch(runner, downloads):
    _running.bus, _running.modules, _running.downloads = (
        (runner.bus, tuple(runner.modules), downloads) if runner else (None, (), None))


async def _wait(predicate, timeout=3.0):
    """True when the condition holds; it is looked at again after each step of the bus and each end of a task of the modules.

    `timeout` only ends a test that is stuck."""
    return await wait_until(predicate, _running.bus, *_running.modules, seconds=timeout)


async def _quiet():
    """Returns when the bus took apart every envelope and the modules run nothing: what did not happen by now never will."""
    await settle(_running.bus, *_running.modules)


@pytest.mark.asyncio
async def test_sim_C5_delete_keeps_completed_removes_partial(system):
    """C5/Д2: удаление серии сносит НЕДОДЕЛАННЫЙ полуфайл (final нет), но
    НЕ трогает законченный (final на месте — файлы остаются). Задачи
    вычищаются."""
    bus, _, dl, probe, db_path, media_dir = system
    done = os.path.join(media_dir, "done.mp4")   # законченный: final есть
    open(done, "w").close()
    wip = os.path.join(media_dir, "wip.mp4")     # недоделанный: final НЕТ
    # VF6: реальные артефакты ЭТОГО кода (шаблоны ytdlp.py), а не только
    # generic .part — обрыв в фазе download оставлял именно их.
    artifacts = [wip + ".part", wip + ".download.mp4", wip + ".remux.mp4",
                 wip + ".download.f140.mp4"]
    for a in artifacts:
        open(a, "w").close()
    # соседний эпизод: его артефакты трогать нельзя
    keep = os.path.join(media_dir, "other.mp4.download.mp4")
    open(keep, "w").close()
    with sqlite3.connect(db_path) as conn:
        for uid, sp in (("u_done", done), ("u_wip", wip)):
            conn.execute(
                "INSERT INTO download_tasks (task_key, series_id, video_url, "
                "save_path, task_type, status, attempts, created_at, "
                "updated_at) VALUES (?,2,'url',?, 'vk_video','pending',0,"
                "'t','t')", (uid, sp))
        conn.commit()

    probe.publish_event("series.deleted",
                        {"series_id": 2, "delete_from_qb": False})

    # все полуфайлы wip снесены (включая .download.*/.remux.mp4 — VF6)
    for a in artifacts:
        assert await _wait(lambda a=a: not os.path.exists(a)), a
    assert os.path.exists(done)                  # законченный файл не тронут
    assert os.path.exists(keep)                  # чужой артефакт не тронут
    assert await _wait(lambda: not _tasks(db_path))  # задачи вычищены


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
async def test_dispatch_prefers_download_url(system):
    """VK: канонический source_url остаётся идентичностью, а качается
    embed-ссылка с hash (каноническая страница отдаёт Access restricted)."""
    bus, _, dl, probe, db_path, media_dir = system
    raw = "https://vkvideo.ru/video_ext.php?oid=-1&id=1&hash=abc"
    _add_item(db_path, "uid1", 1, download_url=raw)

    probe.publish_event("downloads.dispatch", {"series_id": 2})

    assert await _wait(lambda: bool(dl.started))
    assert dl.started[0] == raw
    assert await _wait(lambda: _items(db_path)["uid1"]["status"] == "completed")


@pytest.mark.asyncio
async def test_dispatch_creates_downloads_and_completes(system):
    bus, _, dl, probe, db_path, media_dir = system
    _add_item(db_path, "uid1", 1)
    _add_item(db_path, "uid2", 2, plan="redundant")  # вне плана — без задачи
    sub = bus.subscribe("series.status.contribution")

    probe.publish_event("downloads.dispatch", {"series_id": 2})
    assert await _wait(lambda: _items(db_path)["uid1"]["status"] == "completed")

    items = _items(db_path)
    assert items["uid1"]["final_filename"] == "Серия 01.mp4"
    assert os.path.exists(os.path.join(media_dir, "Серия 01.mp4"))
    assert items["uid2"]["status"] == "pending"  # не трогали
    assert await _wait(lambda: _tasks(db_path) == [])  # the task goes one write after the status
    # свёртка дошла до ready (финальная публикуется после завершения
    # воркера — дожидаемся именно её)
    expected = {"downloading": False, "error": False,
                "ready": True, "waiting": False}
    flags = None

    def reached_ready():
        nonlocal flags
        while not sub.queue.empty():
            flags = sub.queue.get_nowait().payload["flags"]
        return flags == expected

    await _wait(reached_ready)
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
async def test_plan_updated_adopts_only_no_downloads(system):
    """Инвариант контракта: scan.plan.updated делает ТОЛЬКО усыновление.
    Если файла на диске нет — НЕ создаёт задач и НЕ запускает загрузку
    (загрузку стартует лишь явный скан через downloads.dispatch)."""
    _, _, dl, probe, db_path, _ = system
    _add_item(db_path, "uid1", 1)  # файла на диске НЕТ
    probe.publish_event("scan.plan.updated", {"series_id": 2})
    await _quiet()
    assert dl.started == []                                  # не качали
    assert _tasks(db_path) == []                             # задач не создано
    assert _items(db_path)["uid1"]["status"] == "pending"    # остался pending


@pytest.mark.asyncio
async def test_error_carrier_and_retry_by_next_scan(system):
    _, _, dl, probe, db_path, _ = system
    _add_item(db_path, "uid1", 1)
    dl.fail_urls.add("https://vk.com/video-1_1")

    probe.publish_event("downloads.dispatch", {"series_id": 2})
    assert await _wait(lambda: _items(db_path)["uid1"]["status"] == "error")
    tasks = _tasks(db_path)
    assert len(tasks) == 1 and tasks[0]["status"] == "error"
    assert "недоступно" in tasks[0]["error_message"]

    # следующий скан (dispatch): error-задача заменяется новой, без дублей
    # (completed пишется на шаг раньше удаления задачи — ждём оба факта)
    dl.fail_urls.clear()
    probe.publish_event("downloads.dispatch", {"series_id": 2})
    assert await _wait(lambda: _items(db_path)["uid1"]["status"] == "completed")
    assert await _wait(lambda: _tasks(db_path) == [])


@pytest.mark.asyncio
async def test_pending_outside_plan_dropped(system):
    _, _, dl, probe, db_path, _ = system
    dl.gate = asyncio.Event()  # держим загрузки, чтобы успеть проверить
    _add_item(db_path, "uid1", 1)
    probe.publish_event("downloads.dispatch", {"series_id": 2})
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

    await probe.request("downloads.settle", {"series_id": 2}, timeout=10)  # a sync brings the disk to the plan (I-59)
    assert await _wait(lambda: not any(
        t["task_key"] == "uid2" for t in _tasks(db_path)))
    dl.gate.set()


@pytest.mark.asyncio
async def test_parallel_limit_respected(system):
    _, fake, dl, probe, db_path, _ = system
    fake.settings["max_parallel_downloads"] = "1"
    probe.publish_event("settings.changed",
                        {"key": "max_parallel_downloads", "value": "1"})
    await _quiet()  # the module took the new limit

    dl.gate = asyncio.Event()
    for ep in (1, 2, 3):
        _add_item(db_path, f"uid{ep}", ep)
    probe.publish_event("downloads.dispatch", {"series_id": 2})
    assert await _wait(lambda: len(dl.started) >= 1)
    # A held worker never ends, so quiet is the bus alone: nothing is left in it that could start a second worker.
    await _running.bus.settled()
    assert dl.max_concurrent == 1 and len(_running.downloads.background()) == 1
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
async def test_fs_sync_of_a_torrent_series_answers_at_once_and_says_nothing(system, caplog):
    """The files of a torrent series are the torrents module's: no contribution, no warning about its path."""
    bus, _, _, probe, _, _ = system
    contributions = bus.subscribe("series.status.contribution")
    with caplog.at_level(logging.WARNING):
        reply = await probe.request("downloads.fs.sync", {"series_id": 5}, timeout=10)
    assert reply == {"adopted": 0, "lost": 0}
    assert contributions.queue.empty()
    assert [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING] == []


@pytest.mark.asyncio
async def test_fs_sync_always_contributes(system):
    """Обход диска публикует готовность даже без изменений (после
    рестарта вклад иначе не переотправится до первого изменения)."""
    bus, _, _, probe, db_path, media_dir = system
    _add_item(db_path, "uid1", 1, status="completed",
              filename="Серия 01.mp4")
    with open(os.path.join(media_dir, "Серия 01.mp4"), "wb") as f:
        f.write(b"x")

    sub = bus.subscribe("series.status.contribution")
    reply = await probe.request("downloads.fs.sync", {"series_id": 2},
                                timeout=10)
    assert reply == {"adopted": 0, "lost": 0}  # ничего не изменилось
    flags = None

    def contributed():
        nonlocal flags
        while not sub.queue.empty():
            flags = sub.queue.get_nowait().payload["flags"]
        return flags is not None

    await _wait(contributed)
    assert flags is not None and flags["ready"] is True


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
    dl.bus = bus
    dl.gate = asyncio.Event()
    downloads = DownloadsModule(bus, db, downloader=dl)
    runner = Runner(bus, [FakeNeighbours(bus), CatalogModule(bus, db), downloads, Probe(bus)])
    await runner.start()
    _watch(runner, downloads)
    try:
        # оборванная — снова запущена; error-задача не тронута (Р-13)
        assert await _wait(lambda: "https://vk.com/video-1_1" in dl.started)
        statuses = {t["task_key"]: t["status"] for t in _tasks(db_path)}
        assert statuses["uid2"] == "error"
        dl.gate.set()
    finally:
        await runner.stop()
        _watch(None, None)


@pytest.mark.asyncio
async def test_cancel_active_download(system):
    """Находка 45: точечная отмена — убить активную загрузку, удалить
    файл, снять задачу; план/игноры не трогаются."""
    bus, _, dl, probe, db_path, media_dir = system
    dl.gate = asyncio.Event()  # держим загрузку активной на 50%
    _add_item(db_path, "uid1", 1)
    probe.publish_event("downloads.dispatch", {"series_id": 2})

    # дождаться, пока задача реально пошла в загрузку
    assert await _wait(lambda: _items(db_path)["uid1"]["status"]
                       == "downloading")
    task = _tasks(db_path)[0]
    save_path = task["save_path"]
    # half-written files of the download on disk: a task writes under a name of its own beside the name of the video
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    work = task["work_path"]
    open(work, "wb").close()
    open(work + ".part", "wb").close()
    open(save_path + ".part", "wb").close()  # a piece an earlier version left beside the name of the video

    reply = await probe.request("downloads.cancel",
                                {"task_id": task["id"]}, timeout=10)
    assert reply["cancelled"] is True

    # задача снята, элемент вернулся в pending (план/игнор не тронуты)
    assert _tasks(db_path) == []
    item = _items(db_path)["uid1"]
    assert item["status"] == "pending"
    assert item["plan_status"] == "in_plan_single"
    assert item["is_ignored_by_user"] == 0
    # the file of the task and its pieces are gone
    assert not os.path.exists(work)
    assert not os.path.exists(work + ".part")
    assert not os.path.exists(save_path + ".part")


@pytest.mark.asyncio
async def test_a_cancel_leaves_the_file_under_the_name_of_the_video(system):
    """The name of the video holds a whole file (of the video this one displaces, or of a row): a cancel never touches it."""
    bus, _, dl, probe, db_path, media_dir = system
    dl.gate = asyncio.Event()
    _add_item(db_path, "uid1", 1)
    probe.publish_event("downloads.dispatch", {"series_id": 2})
    assert await _wait(lambda: _items(db_path)["uid1"]["status"] == "downloading")
    task = _tasks(db_path)[0]
    os.makedirs(os.path.dirname(task["save_path"]), exist_ok=True)
    open(task["save_path"], "wb").close()

    await probe.request("downloads.cancel", {"task_id": task["id"]}, timeout=10)

    assert os.path.exists(task["save_path"])


@pytest.mark.asyncio
async def test_cancel_unknown_task_errors(system):
    bus, _, dl, probe, db_path, media_dir = system
    with pytest.raises(BusRequestError, match="не найдена"):
        await probe.request("downloads.cancel", {"task_id": 999}, timeout=5)


def made_as_announced(seconds: float = 60.0):
    """The source announces a length and the file the faked way of reading made measures it: the routes are tested, not
    the length (tests/test_download_integrity.py)."""
    return patch.multiple(ytdlp, _announced_length=AsyncMock(return_value=seconds),
                          _local_duration=AsyncMock(return_value=seconds))


@pytest.mark.asyncio
async def test_download_direct_stream_with_headers(tmp_path):
    out_file = str(tmp_path / "direct.mp4")
    progress = []

    async def on_progress(p):
        progress.append(p)

    with patch.object(ytdlp, "_download_direct_ffmpeg") as mock_ffmpeg, made_as_announced():
        mock_ffmpeg.return_value = (True, "")
        ok, err = await ytdlp.download(
            "https://cdn.example.com/video_2160p.mp4",
            out_file,
            on_progress,
            headers={"Referer": "https://fsst.online/"}
        )
        assert ok is True
        assert err == ""
        mock_ffmpeg.assert_called_once_with(
            "https://cdn.example.com/video_2160p.mp4",
            {"Referer": "https://fsst.online/"},
            out_file,
            on_progress,
            60.0
        )


@pytest.mark.asyncio
async def test_a_bare_mp4_link_is_read_without_headers_of_any_site(tmp_path):
    """Headers belong to the player that resolved the stream; the downloader adds none of its own."""
    out_file = str(tmp_path / "bare.mp4")

    async def on_progress(_data):
        pass

    with patch.object(ytdlp, "_download_direct_ffmpeg") as mock_ffmpeg, made_as_announced():
        mock_ffmpeg.return_value = (True, "")
        ok, _ = await ytdlp.download("https://cdn.example.com/video_1080p.mp4", out_file, on_progress)

    assert ok is True
    mock_ffmpeg.assert_called_once_with("https://cdn.example.com/video_1080p.mp4", {}, out_file, on_progress, 60.0)


@pytest.mark.asyncio
async def test_download_rejects_relative_path():
    async def on_progress(_data):
        pass

    ok, err = await ytdlp.download(
        "https://example.test/video.mp4", "Z/Donghua/video.mp4",
        on_progress)

    assert ok is False
    assert "абсолютн" in err


def test_transient_download_errors():
    from modules.downloads.module import _transient_download_error

    assert _transient_download_error("ffmpeg: Connection timed out")
    assert _transient_download_error("Server returned 503 Service Unavailable")
    assert _transient_download_error("Server returned 429 Too Many Requests")
    assert _transient_download_error("ERROR: HTTP Error 403: Forbidden")
    assert _transient_download_error("HTTP Error 503: Service Unavailable")
    assert not _transient_download_error("путь сохранения должен быть абсолютным")
    assert not _transient_download_error("Option not found")


def test_file_manifest_has_no_http_headers():
    referer = {"Referer": "https://r/"}
    assert ytdlp._headers_args("file:///tmp/x.m3u8", referer) == []
    assert ytdlp._headers_args("https://cdn.test/x.mp4", referer) == [
        "-headers", "Referer: https://r/\r\n"]


def test_stream_headers_reach_ffmpeg_in_full():
    args = ytdlp._headers_args("https://cdn.test/x.mp4", {
        "Origin": "https://o/", "User-Agent": "Agent/1", "Referer": "https://r/"})
    assert args == ["-user_agent", "Agent/1",
                    "-headers", "Origin: https://o/\r\nReferer: https://r/\r\n"]
    assert ytdlp._headers_args("https://cdn.test/x.mp4", {}) == []


def test_ytdlp_error_prefers_error_line():
    tail = (
        "WARNING: Your yt-dlp version is older than 90 days!\n"
        "         It is strongly recommended to update.\n"
        "         Run \"yt-dlp --update\"\n"
        "To suppress this warning, add --no-update to your command/config.\n"
        "ERROR: unable to download video data: HTTP Error 403: Forbidden\n"
    )
    assert "HTTP Error 403" in ytdlp._ytdlp_error(tail, "fallback")


def test_ytdlp_error_ignores_warning_only_tail():
    tail = (
        "WARNING: Your yt-dlp version is older than 90 days!\n"
        "To suppress this warning, add --no-update to your command/config.\n"
    )
    assert ytdlp._ytdlp_error(tail, "fallback") == "fallback"


@pytest.mark.asyncio
async def test_download_falls_back_to_dash_on_hls_error(tmp_path):
    out = str(tmp_path / "x.mp4")

    async def on_progress(_data):
        pass

    with patch.object(ytdlp, "_probe_duration", AsyncMock(return_value=1.0)), \
         patch.object(ytdlp, "_phase_download", side_effect=[
             (False, "unable to download video data: HTTP Error 403"),
             "/tmp/raw"]) as phase, \
         patch.object(ytdlp, "_phase_remux", AsyncMock(return_value=(True, ""))), \
         patch.object(ytdlp, "_local_duration", AsyncMock(return_value=1.0)):
        ok, err = await ytdlp.download("https://vk.com/video-1_2", out, on_progress)

    assert ok is True
    assert err == ""
    assert phase.call_count == 2
    assert phase.call_args_list[1].args[4] == ytdlp.DASH_FALLBACK_FORMAT


@pytest.mark.asyncio
async def test_download_does_not_fall_back_for_private_video(tmp_path):
    out = str(tmp_path / "x.mp4")

    async def on_progress(_data):
        pass

    with patch.object(ytdlp, "_probe_duration", AsyncMock(return_value=1.0)), \
         patch.object(ytdlp, "_phase_download",
                      return_value=(False, "видео недоступно или приватно")) as phase:
        ok, err = await ytdlp.download("https://vk.com/video-1_2", out, on_progress)

    assert ok is False
    assert "приватно" in err
    assert phase.call_count == 1


PAGE_URL = "https://1.ruchime.org/release/x/episode-1"


@pytest.mark.asyncio
async def test_provider_stream_error_fails_task_without_ytdlp(system):
    """A web episode page that has no stream must not be handed to the downloader."""
    _, fake, dl, probe, db_path, _ = system
    fake.stream_replies[PAGE_URL] = {
        "handled": True, "error": "Vibix 480p: манифест ответил 400",
        "transient": False}
    _add_item(db_path, "web1", 1, series_id=3, source_url=PAGE_URL)

    probe.publish_event("downloads.dispatch", {"series_id": 3})

    assert await _wait(lambda: _items(db_path)["web1"]["status"] == "error")
    assert dl.started == []
    assert "400" in _tasks(db_path)[0]["error_message"]


@pytest.mark.asyncio
async def test_a_progressive_file_is_handed_to_the_downloader_as_such(system):
    _, fake, dl, probe, db_path, _ = system
    fake.stream_replies[PAGE_URL] = {"handled": True, "stream_url": "https://cdn.test/f?sig=1", "headers": {}, "progressive": True}
    _add_item(db_path, "web1", 1, series_id=3, source_url=PAGE_URL)

    probe.publish_event("downloads.dispatch", {"series_id": 3})

    assert await _wait(lambda: _items(db_path)["web1"]["status"] == "completed")
    assert dl.started == ["https://cdn.test/f?sig=1"]
    assert dl.kwargs["https://cdn.test/f?sig=1"] == {"progressive": True, "whole_video": True}


@pytest.mark.asyncio
async def test_a_stream_that_is_not_marked_progressive_is_handed_over_without_the_mark(system):
    _, fake, dl, probe, db_path, _ = system
    fake.stream_replies[PAGE_URL] = {"handled": True, "stream_url": "https://cdn.test/g.mp4", "headers": {"Referer": "https://r/"}}
    _add_item(db_path, "web1", 1, series_id=3, source_url=PAGE_URL)

    probe.publish_event("downloads.dispatch", {"series_id": 3})

    assert await _wait(lambda: _items(db_path)["web1"]["status"] == "completed")
    assert dl.kwargs["https://cdn.test/g.mp4"] == {"headers": {"Referer": "https://r/"}, "whole_video": True}


@pytest.mark.asyncio
async def test_transient_stream_error_is_retried(system, monkeypatch):
    from modules.downloads import module as downloads_module
    monkeypatch.setattr(downloads_module, "RETRY_DELAY_S", 0.01)
    _, fake, dl, probe, db_path, _ = system
    fake.stream_replies[PAGE_URL] = {
        "handled": True, "error": "Vibix: API не ответил", "transient": True}
    _add_item(db_path, "web1", 1, series_id=3, source_url=PAGE_URL)

    probe.publish_event("downloads.dispatch", {"series_id": 3})

    assert await _wait(lambda: _items(db_path)["web1"]["status"] == "error")
    assert fake.stream_requests.count(PAGE_URL) == downloads_module.MAX_DOWNLOAD_ATTEMPTS
    assert dl.started == []


@pytest.mark.asyncio
async def test_candidate_of_a_web_source_never_reaches_ytdlp_when_the_stream_request_fails(system, monkeypatch):
    from modules.downloads import module as downloads_module
    monkeypatch.setattr(downloads_module, "RETRY_DELAY_S", 0.01)
    _, fake, dl, probe, db_path, _ = system
    fake.stream_raises.add(PAGE_URL)
    _add_item(db_path, "web1", 1, series_id=3, source_url=PAGE_URL, source_id=_source_id(db_path, 3))

    probe.publish_event("downloads.dispatch", {"series_id": 3})

    assert await _wait(lambda: _items(db_path)["web1"]["status"] == "error")
    assert dl.started == []


@pytest.mark.asyncio
async def test_candidate_of_a_vk_source_keeps_the_ytdlp_fallback_when_the_stream_request_fails(system):
    _, fake, dl, probe, db_path, _ = system
    fake.stream_raises.add("https://vk.com/video-1_1")
    _add_item(db_path, "uid1", 1, source_id=_source_id(db_path, 2))

    probe.publish_event("downloads.dispatch", {"series_id": 2})

    assert await _wait(lambda: _items(db_path)["uid1"]["status"] == "completed")
    assert dl.started == ["https://vk.com/video-1_1"]


@pytest.mark.asyncio
async def test_in_one_series_the_fallback_follows_the_source_of_each_candidate(system, monkeypatch):
    """The kind of the series is mixed (a VK source and a web one): the decision is made per candidate."""
    from modules.downloads import module as downloads_module
    monkeypatch.setattr(downloads_module, "RETRY_DELAY_S", 0.01)
    _, fake, dl, probe, db_path, _ = system
    vk_link, web_link = "https://vk.com/video-1_1", PAGE_URL
    fake.stream_raises.update({vk_link, web_link})
    _add_item(db_path, "vk", 1, series_id=4, source_url=vk_link, source_id=_source_id(db_path, 4, 0))
    _add_item(db_path, "web", 2, series_id=4, source_url=web_link, source_id=_source_id(db_path, 4, 1))

    probe.publish_event("downloads.dispatch", {"series_id": 4})

    assert await _wait(lambda: _items(db_path)["vk"]["status"] == "completed"
                       and _items(db_path)["web"]["status"] == "error")
    assert dl.started == [vk_link]


@pytest.mark.asyncio
async def test_a_row_without_a_source_takes_the_type_of_the_only_source_of_its_series(system, monkeypatch):
    from modules.downloads import module as downloads_module
    monkeypatch.setattr(downloads_module, "RETRY_DELAY_S", 0.01)
    _, fake, dl, probe, db_path, _ = system
    fake.stream_raises.update({"https://vk.com/video-1_1", PAGE_URL})
    _add_item(db_path, "vk", 1, series_id=2)
    _add_item(db_path, "web", 2, series_id=3, source_url=PAGE_URL)

    probe.publish_event("downloads.dispatch", {"series_id": 2})
    probe.publish_event("downloads.dispatch", {"series_id": 3})

    assert await _wait(lambda: _items(db_path)["vk"]["status"] == "completed"
                       and _items(db_path)["web"]["status"] == "error")
    assert dl.started == ["https://vk.com/video-1_1"]


@pytest.mark.asyncio
async def test_a_row_without_a_source_in_a_series_of_several_sources_is_a_web_one(system, monkeypatch):
    from modules.downloads import module as downloads_module
    monkeypatch.setattr(downloads_module, "RETRY_DELAY_S", 0.01)
    _, fake, dl, probe, db_path, _ = system
    fake.stream_raises.add("https://vk.com/video-1_1")
    _add_item(db_path, "old", 1, series_id=4)

    probe.publish_event("downloads.dispatch", {"series_id": 4})

    assert await _wait(lambda: _items(db_path)["old"]["status"] == "error")
    assert dl.started == []


@pytest.mark.asyncio
async def test_a_removed_source_is_not_guessed_when_the_stream_request_fails(system, monkeypatch):
    from modules.downloads import module as downloads_module
    monkeypatch.setattr(downloads_module, "RETRY_DELAY_S", 0.01)
    _, fake, dl, probe, db_path, _ = system
    fake.stream_raises.add("https://vk.com/video-1_1")
    _add_item(db_path, "gone", 1, series_id=2, source_id=9999)

    probe.publish_event("downloads.dispatch", {"series_id": 2})

    assert await _wait(lambda: _items(db_path)["gone"]["status"] == "error")
    assert dl.started == []


def test_the_source_of_a_candidate():
    vk, web = {"id": 5, "source_type": "vk_video"}, {"id": 6, "source_type": "web_video"}
    assert source_of_candidate([vk, web], 6) is web
    assert source_of_candidate([vk], None) is vk
    assert source_of_candidate([vk, web], None) is None
    assert source_of_candidate([vk], 7) is None
    assert source_of_candidate([], None) is None


def _touch(media_dir, name):
    path = os.path.join(media_dir, name)
    with open(path, "wb") as f:
        f.write(b"video")
    return path


def _slot(db_path, fake, winner, *losers, voice="Ruchime"):
    """The rows of one slot as scan leaves them: season 1, the rows of source 7, the reference of the slot the winner's."""
    with sqlite3.connect(db_path) as conn:
        for uid in (winner, *losers):
            conn.execute("UPDATE media_items SET season=1, source_id=7, row_key=? WHERE unique_id=?", (f"vk:{uid}", uid))
        conn.commit()
    episode = _items(db_path)[winner]["episode_start"]
    fake.references = [{"season": 1, "episode": episode, "part": None, "mark": "by_level", "source_id": 7,
                        "row_key": f"vk:{winner}", "player": "vk", "voice_group": voice, "height": 1080, "reason": None}]


def _fake_of(system):
    return system[1]


async def _pack(probe):
    """The last step of a run of a sync: the containers of the series (ЭТАП_11)."""
    return await probe.request("downloads.pack", {"series_id": 2}, timeout=10)


@pytest.mark.asyncio
async def test_a_displaced_file_goes_when_the_container_of_its_winner_is_made(system):
    bus, fake, dl, probe, db_path, media_dir = system
    loser = _touch(media_dir, "e01 360p.mp4")
    winner = _touch(media_dir, "e01 1080p.mp4")
    _add_item(db_path, "win", 1, status="completed", filename="e01 1080p.mp4")
    _add_item(db_path, "lose", 1, plan="replaced", status="completed",
              filename="e01 360p.mp4", superseded_by="win")
    _slot(db_path, fake, "win", "lose")

    await probe.request("downloads.settle", {"series_id": 2}, timeout=10)
    assert os.path.exists(loser)  # the displaced file lives until the container of the winner is whole (stage 11, 3)
    assert await _pack(probe) == {"packed": 1, "failed": 0, "unchanged": 0, "waiting": 0}

    assert not os.path.exists(loser) and not os.path.exists(winner)
    assert os.path.isfile(os.path.join(media_dir, "e01 1080p.mkv"))
    items = _items(db_path)
    assert items["lose"]["final_filename"] is None
    assert items["lose"]["status"] == "pending"     # holds no file, so it is not ready; the plan does not want it, so it is not fetched
    assert items["win"]["final_filename"] == "e01 1080p.mkv" and items["win"]["status"] == "completed"
    assert dl.started == []
    assert await _pack(probe) == {"packed": 0, "failed": 0, "unchanged": 1, "waiting": 0}  # the container is what the plan asks

@pytest.mark.asyncio
async def test_superseded_file_stays_while_winner_is_not_downloaded(system):
    _, _, _, probe, db_path, media_dir = system
    loser = _touch(media_dir, "e01 360p.mp4")
    _add_item(db_path, "win", 1)                               # still pending
    _add_item(db_path, "lose", 1, plan="replaced", status="completed",
              filename="e01 360p.mp4", superseded_by="win")

    probe.publish_event("scan.plan.updated", {"series_id": 2})
    await _quiet()

    assert os.path.exists(loser)
    assert _items(db_path)["lose"]["final_filename"] == "e01 360p.mp4"


@pytest.mark.asyncio
async def test_superseded_file_stays_when_winner_file_is_missing(system):
    _, _, _, probe, db_path, media_dir = system
    loser = _touch(media_dir, "e01 360p.mp4")
    _add_item(db_path, "win", 1, status="completed", filename="e01 1080p.mp4")
    _add_item(db_path, "lose", 1, plan="replaced", status="completed",
              filename="e01 360p.mp4", superseded_by="win")

    probe.publish_event("scan.plan.updated", {"series_id": 2})
    await _quiet()

    assert os.path.exists(loser)


@pytest.mark.asyncio
async def test_the_file_a_better_download_displaces_lives_until_the_container_of_the_winner_is_made(system):
    """The lower file survives the download of the better one, and goes when its container is made."""
    _, fake, dl, probe, db_path, media_dir = system
    loser = _touch(media_dir, "Серия 01 old.mp4")
    _add_item(db_path, "win", 1)
    _add_item(db_path, "lose", 1, plan="replaced", status="completed",
              filename="Серия 01 old.mp4", superseded_by="win")
    _slot(db_path, fake, "win", "lose")

    probe.publish_event("downloads.dispatch", {"series_id": 2})

    assert await _wait(lambda: _items(db_path)["win"]["status"] == "completed")
    assert os.path.exists(loser) and os.path.exists(os.path.join(media_dir, "Серия 01.mp4"))
    await _pack(probe)
    assert not os.path.exists(loser) and os.path.exists(os.path.join(media_dir, "Серия 01.mkv"))
    assert _items(db_path)["win"]["final_filename"] == "Серия 01.mkv"

@pytest.mark.asyncio
async def test_packing_is_idempotent_and_survives_fs_sync(system):
    _, fake, dl, probe, db_path, media_dir = system
    _touch(media_dir, "e01 360p.mp4")
    _touch(media_dir, "e01 1080p.mp4")
    _add_item(db_path, "win", 1, status="completed", filename="e01 1080p.mp4")
    _add_item(db_path, "lose", 1, plan="replaced", status="completed",
              filename="e01 360p.mp4", superseded_by="win")
    _slot(db_path, fake, "win", "lose")

    await probe.request("downloads.fs.sync", {"series_id": 2}, timeout=10)
    await _pack(probe)
    await probe.request("downloads.fs.sync", {"series_id": 2}, timeout=10)
    await _pack(probe)
    await _quiet()

    items = _items(db_path)
    assert items["lose"]["status"] == "pending"         # holds no file; not in the plan, so no task is made for it
    assert items["lose"]["final_filename"] is None
    assert items["win"]["final_filename"] == "e01 1080p.mkv" and items["win"]["status"] == "completed"
    assert sorted(os.listdir(media_dir)) == ["e01 1080p.mkv"]
    assert dl.started == []
    assert _tasks(db_path) == []

@pytest.mark.asyncio
async def test_superseded_row_sharing_the_winner_file_is_not_deleted(system):
    _, _, _, probe, db_path, media_dir = system
    shared = _touch(media_dir, "e01.mp4")
    _add_item(db_path, "win", 1, status="completed", filename="e01.mp4")
    _add_item(db_path, "lose", 1, plan="replaced", status="completed",
              filename="e01.mp4", superseded_by="win")

    probe.publish_event("scan.plan.updated", {"series_id": 2})
    await _quiet()

    assert os.path.exists(shared)
    assert _items(db_path)["lose"]["final_filename"] == "e01.mp4"


@pytest.mark.asyncio
async def test_superseded_path_outside_series_dir_is_never_touched(system, tmp_path):
    _, _, _, probe, db_path, media_dir = system
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"keep")
    _touch(media_dir, "e01 1080p.mp4")
    _add_item(db_path, "win", 1, status="completed", filename="e01 1080p.mp4")
    _add_item(db_path, "lose", 1, plan="replaced", status="completed",
              filename="../outside.mp4", superseded_by="win")

    probe.publish_event("scan.plan.updated", {"series_id": 2})
    await _quiet()

    assert outside.exists()


@pytest.mark.asyncio
async def test_retired_file_leaves_downloaded_counter(system):
    bus, _, _, probe, db_path, media_dir = system
    _touch(media_dir, "e01 360p.mp4")
    _touch(media_dir, "e01 1080p.mp4")
    _add_item(db_path, "win", 1, status="completed", filename="e01 1080p.mp4")
    _add_item(db_path, "lose", 1, plan="replaced", status="completed",
              filename="e01 360p.mp4", superseded_by="win")
    _slot(db_path, _fake_of(system), "win", "lose")
    sub = bus.subscribe("series.downloaded.changed")

    await _pack(probe)  # the container of the winner is made: the displaced row holds nothing

    count = None

    def counted():
        nonlocal count
        while not sub.queue.empty():
            count = sub.queue.get_nowait().payload["count"]
        return count == 1

    await _wait(counted)
    assert count == 1


@pytest.mark.asyncio
async def test_error_task_outside_plan_is_dropped(system):
    _, _, _, probe, db_path, _ = system
    _add_item(db_path, "gone", 1, plan="candidate", status="error")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO download_tasks (task_key, series_id, video_url, "
                     "save_path, status, task_type) VALUES "
                     "('gone', 2, 'u', '/tmp/x', 'error', 'vk_video')")
        conn.commit()

    await probe.request("downloads.settle", {"series_id": 2}, timeout=10)  # a sync brings the disk to the plan (I-59)

    assert await _wait(lambda: _tasks(db_path) == [])


def _add_task(db_path, uid, status, save_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO download_tasks (task_key, series_id, video_url, save_path, status, task_type) "
                     "VALUES (?, 2, 'u', ?, ?, 'vk_video')", (uid, save_path, status))
        conn.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending", "error"])
async def test_a_waiting_or_failed_task_without_a_row_goes_with_what_it_left(system, state):
    _, _, _, probe, db_path, media_dir = system
    save_path = os.path.join(media_dir, "ghost.mp4")
    leftover = save_path + ".part"
    open(leftover, "wb").close()
    _add_task(db_path, "ghost", state, save_path)

    await probe.request("downloads.settle", {"series_id": 2}, timeout=10)  # a sync brings the disk to the plan (I-59)

    assert await _wait(lambda: _tasks(db_path) == [] and not os.path.exists(leftover))


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["downloading", "processing"])
async def test_an_active_task_without_a_row_is_cancelled_and_leaves_nothing_behind(system, state):
    """Scan deleted the row of a slot that lost: the download would end with a file nobody registers."""
    _, _, dl, probe, db_path, _ = system
    dl.gate = asyncio.Event()  # the download is held in the middle
    _add_item(db_path, "uid1", 1)
    probe.publish_event("downloads.dispatch", {"series_id": 2})
    assert await _wait(lambda: dl.concurrent == 1)
    task = _tasks(db_path)[0]
    partial = task["save_path"] + ".download.mp4"
    open(partial, "wb").close()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE download_tasks SET status=? WHERE id=?", (state, task["id"]))
        conn.execute("DELETE FROM media_items WHERE unique_id='uid1'")
        conn.commit()

    await probe.request("downloads.settle", {"series_id": 2}, timeout=10)  # a sync brings the disk to the plan (I-59)

    assert await _wait(lambda: _tasks(db_path) == [] and dl.concurrent == 0)
    assert not os.path.exists(partial)
    assert _items(db_path) == {}


@pytest.mark.asyncio
async def test_a_finished_file_of_a_task_without_a_row_stays(system):
    _, _, _, probe, db_path, media_dir = system
    finished = os.path.join(media_dir, "ghost.mp4")
    open(finished, "wb").close()
    _add_task(db_path, "ghost", "error", finished)

    await probe.request("downloads.settle", {"series_id": 2}, timeout=10)  # a sync brings the disk to the plan (I-59)

    assert await _wait(lambda: _tasks(db_path) == [])
    assert os.path.exists(finished)


@pytest.mark.asyncio
async def test_an_active_task_whose_row_left_the_plan_is_stopped_with_what_it_wrote_and_its_row_is_not_fetched(system):
    """The plan does not want the video any more (the episode is switched off): the download stops by itself, the pieces it
    wrote go, and the row goes back to not fetched; the task of a video the plan still wants goes on."""
    _, _, dl, probe, db_path, _ = system
    dl.gate = asyncio.Event()
    _add_item(db_path, "uid1", 1)
    _add_item(db_path, "uid2", 2)
    probe.publish_event("downloads.dispatch", {"series_id": 2})
    assert await _wait(lambda: dl.concurrent >= 1)
    task = next(each for each in _tasks(db_path) if each["task_key"] == "uid1" and each["status"] == "downloading")
    work = os.path.join(os.path.dirname(task["save_path"]), f".uid1.{os.path.basename(task['save_path'])}")
    os.makedirs(os.path.dirname(work), exist_ok=True)
    open(work, "wb").close()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE media_items SET plan_status='redundant' WHERE unique_id='uid1'")
        conn.commit()

    await probe.request("downloads.settle", {"series_id": 2}, timeout=10)  # a sync brings the disk to the plan (I-59)

    assert await _wait(lambda: "uid1" not in [t["task_key"] for t in _tasks(db_path)])
    assert await _wait(lambda: not os.path.exists(work))
    assert _items(db_path)["uid1"]["status"] == "pending" and not _items(db_path)["uid1"]["final_filename"]
    dl.gate.set()
    assert await _wait(lambda: _items(db_path)["uid2"]["status"] == "completed")
    assert _items(db_path)["uid1"]["status"] == "pending"


@pytest.mark.asyncio
async def test_a_file_that_is_whole_stays_when_its_row_leaves_the_plan(system):
    """Switching an episode off stops the work, not the library: a downloaded file is left where it lies."""
    _, _, dl, probe, db_path, media_dir = system
    done = os.path.join(media_dir, "done.mp4")
    open(done, "wb").close()
    _add_item(db_path, "uid1", 1, status="completed", plan="redundant", filename="done.mp4")
    probe.publish_event("scan.plan.updated", {"series_id": 2})
    await _quiet()
    assert os.path.exists(done) and _items(db_path)["uid1"]["status"] == "completed"




async def _fetch(probe, bus, expect, **job):
    """Sends downloads.fetch jobs and returns the `finished` events of `expect` of them."""
    sub = bus.subscribe("downloads.fetch.finished")
    for item in job["jobs"]:
        probe.send_command("downloads.fetch", item)
    return [(await asyncio.wait_for(sub.queue.get(), 5)).payload for _ in range(expect)]


@pytest.mark.asyncio
async def test_fetch_downloads_a_stream_without_touching_the_media_tables(system, tmp_path):
    bus, fake, dl, probe, db_path, _ = system
    fake.stream_replies["https://site/#cvh=1"] = {
        "handled": True, "stream_url": "https://cdn/1.mp4", "headers": {"User-Agent": "A/1"}}
    path = str(tmp_path / "temp" / "s01e01" / "voice.mp4")
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:1", "url": "https://site/#cvh=1", "path": path, "max_height": 720, "series_id": 3}])
    assert done["ok"] is True and done["key"] == "dub:1" and done["path"] == path
    assert os.path.isfile(path) and dl.started == ["https://cdn/1.mp4"]
    assert fake.stream_payloads == [{"url": "https://site/#cvh=1", "max_height": 720}]
    assert _tasks(db_path) == []


@pytest.mark.asyncio
async def test_fetch_failure_is_reported_and_leaves_no_partial_file(system, tmp_path):
    bus, fake, dl, probe, _, _ = system
    path = str(tmp_path / "t" / "x.mp4")
    dl.fail_urls.add("https://site/#kodik=1")
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:9", "url": "https://site/#kodik=1", "path": path, "series_id": 3}])
    assert done["ok"] is False and "приватно" in done["error"] and done["transient"] is False
    assert not os.path.exists(path)


@pytest.mark.asyncio
async def test_fetch_stream_error_of_the_provider_is_the_error_of_the_job(system, tmp_path):
    bus, fake, _, probe, _, _ = system
    fake.stream_replies["https://site/#cvh=2"] = {"handled": True, "error": "VK: видео недоступно",
                                                  "transient": False}
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:2", "url": "https://site/#cvh=2", "path": str(tmp_path / "a.mp4"), "series_id": 3}])
    assert (done["ok"], done["error"], done["transient"]) == (False, "VK: видео недоступно", False)


@pytest.mark.asyncio
async def test_fetch_unreachable_sources_is_an_error_not_a_blind_download(system, tmp_path, monkeypatch):
    monkeypatch.setattr("modules.downloads.module.RETRY_DELAY_S", 0.0)
    bus, fake, dl, probe, _, _ = system
    fake.stream_raises.add("https://site/#cvh=3")
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:3", "url": "https://site/#cvh=3", "path": str(tmp_path / "b.mp4"), "series_id": 3}])
    assert done["ok"] is False and "поток не получен" in done["error"] and dl.started == []


@pytest.mark.asyncio
async def test_fetch_retries_transient_errors_then_succeeds(system, tmp_path, monkeypatch):
    monkeypatch.setattr("modules.downloads.module.RETRY_DELAY_S", 0.0)
    bus, _, dl, probe, _, _ = system
    dl.timeouts["https://site/#cvh=4"] = 2
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:4", "url": "https://site/#cvh=4", "path": str(tmp_path / "c.mp4"), "series_id": 3}])
    assert done["ok"] is True and dl.started.count("https://site/#cvh=4") == 3


@pytest.mark.asyncio
async def test_fetch_gives_up_after_the_attempt_limit(system, tmp_path, monkeypatch):
    monkeypatch.setattr("modules.downloads.module.RETRY_DELAY_S", 0.0)
    bus, _, dl, probe, _, _ = system
    dl.timeouts["https://site/#cvh=5"] = 99
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:5", "url": "https://site/#cvh=5", "path": str(tmp_path / "d.mp4"), "series_id": 3}])
    assert done["ok"] is False and done["transient"] is True
    assert dl.started.count("https://site/#cvh=5") == 3 and not os.path.exists(tmp_path / "d.mp4")


@pytest.mark.asyncio
async def test_fetch_has_its_own_limit_and_ignores_repeated_keys(system, tmp_path):
    bus, fake, dl, probe, _, _ = system
    dl.gate = asyncio.Event()
    jobs = [{"key": f"dub:{i}", "url": f"https://site/#cvh={i}", "path": str(tmp_path / f"{i}.mp4"),
             "series_id": 3} for i in range(4)]
    jobs.append(dict(jobs[0]))
    sub = bus.subscribe("downloads.fetch.finished")
    for job in jobs:
        probe.send_command("downloads.fetch", job)
    assert await _wait(lambda: dl.concurrent == 2)
    await _running.bus.settled()  # every job is taken apart: two run, the rest wait (held workers never end)
    assert dl.max_concurrent == 2 and dl.concurrent == 2 and len(dl.started) == 2
    dl.gate.set()
    got = [(await asyncio.wait_for(sub.queue.get(), 5)).payload["key"] for _ in range(4)]
    assert sorted(got) == [f"dub:{i}" for i in range(4)]
    await _quiet()
    assert sub.queue.empty() and len(dl.started) == 4


@pytest.mark.asyncio
async def test_the_fetches_of_voice_overs_are_in_the_queue_of_the_window_while_they_wait_and_run(system, tmp_path):
    bus, fake, dl, probe, _, _ = system
    dl.gate = asyncio.Event()
    changed = bus.subscribe("downloads.queue.changed")
    for i in range(3):
        probe.send_command("downloads.fetch", {
            "key": f"dub:{i}", "url": f"https://site/#cvh={i}", "path": str(tmp_path / f"{i}.mp4"), "series_id": 3})
    assert await _wait(lambda: dl.concurrent == 2)
    await _running.bus.settled()
    queue = await probe.request("downloads.queue.get", {}, timeout=5)
    assert queue["count"] == 3
    assert sorted((each["task_key"], each["status"], each["task_type"]) for each in queue["tasks"]) == [
        ("dub:0", "downloading", "fetch"), ("dub:1", "downloading", "fetch"), ("dub:2", "pending", "fetch")]
    assert {each["save_path"] for each in queue["tasks"]} == {str(tmp_path / f"{i}.mp4") for i in range(3)}
    assert not changed.queue.empty()  # the window is told, not asked
    dl.gate.set()
    assert await _wait(lambda: os.path.isfile(tmp_path / "2.mp4"))
    await _quiet()
    assert (await probe.request("downloads.queue.get", {}, timeout=5))["count"] == 0


@pytest.mark.asyncio
async def test_fetch_cancel_drops_the_queue_and_active_jobs_of_a_series(system, tmp_path):
    bus, fake, dl, probe, _, _ = system
    dl.gate = asyncio.Event()
    for i in range(4):
        probe.send_command("downloads.fetch", {
            "key": f"dub:{i}", "url": f"https://site/#cvh={i}",
            "path": str(tmp_path / f"{i}.mp4"), "series_id": 3 if i < 3 else 4})
    assert await _wait(lambda: dl.started == ["https://site/#cvh=0", "https://site/#cvh=1"])
    probe.publish_event("work.withdrawn", {"series_id": 3})
    # The slots of the cancelled jobs go to the next series' job; the queued job of series 3 never runs.
    assert await _wait(lambda: "https://site/#cvh=3" in dl.started)
    dl.gate.set()
    assert await _wait(lambda: os.path.isfile(tmp_path / "3.mp4"))
    assert "https://site/#cvh=2" not in dl.started
    assert not any(os.path.exists(tmp_path / f"{i}.mp4") for i in (0, 1, 2))


@pytest.mark.asyncio
async def test_fetch_cancel_by_key_drops_only_that_job(system, tmp_path):
    bus, fake, dl, probe, _, _ = system
    dl.gate = asyncio.Event()
    for i in range(3):
        probe.send_command("downloads.fetch", {
            "key": f"file:{i}", "url": f"https://site/#cvh={i}",
            "path": str(tmp_path / f"{i}.mp4"), "series_id": 3})
    assert await _wait(lambda: dl.started == ["https://site/#cvh=0", "https://site/#cvh=1"])
    probe.publish_event("work.withdrawn", {"series_id": 3, "keys": ["file:0"]})
    # The slot of the cancelled job goes to the queued one; the other running job is untouched.
    assert await _wait(lambda: "https://site/#cvh=2" in dl.started)
    dl.gate.set()
    assert await _wait(lambda: os.path.isfile(tmp_path / "1.mp4") and os.path.isfile(tmp_path / "2.mp4"))
    assert not os.path.exists(tmp_path / "0.mp4")


@pytest.mark.asyncio
async def test_fetch_rejects_a_relative_path(system):
    bus, _, dl, probe, _, _ = system
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:7", "url": "https://site/#cvh=7", "path": "rel/x.mp4", "series_id": 3}])
    assert done["ok"] is False and "абсолютным" in done["error"] and dl.started == []


@pytest.mark.asyncio
async def test_a_new_file_name_is_announced_to_the_modules_that_keep_files_next_to_the_video(system):
    bus, fake, dl, probe, db_path, _ = system
    _add_item(db_path, "uid-r", 1, status="completed", filename="old.mp4", series_id=2)
    sub = bus.subscribe("downloads.item.renamed")
    probe.send_command("downloads.item.set_filename", {"unique_id": "uid-r", "filename": "new.mp4"})
    event = (await asyncio.wait_for(sub.queue.get(), 3)).payload
    assert event == {"series_id": 2, "unique_id": "uid-r", "filename": "new.mp4"}
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT final_filename FROM media_items WHERE unique_id='uid-r'").fetchone() == ("new.mp4",)
    probe.send_command("downloads.item.set_filename", {"unique_id": "nobody", "filename": "x.mp4"})
    await _quiet()
    assert sub.queue.empty()


@pytest.mark.asyncio
async def test_a_fetch_is_announced_when_it_leaves_the_queue_and_starts(system, tmp_path, monkeypatch):
    bus, fake, dl, probe, db_path, _ = system
    fake.stream_replies["https://site/#cvh=1"] = {"handled": True, "stream_url": "https://cdn/1.mp4", "headers": {}}
    fake.stream_replies["https://site/#cvh=2"] = {"handled": True, "stream_url": "https://cdn/2.mp4", "headers": {}}
    fake.settings["max_parallel_dub_downloads"] = "1"
    started = bus.subscribe("downloads.fetch.started")
    finished = bus.subscribe("downloads.fetch.finished")
    probe.send_command("downloads.fetch", {"key": "dub:1", "url": "https://site/#cvh=1",
                                           "path": str(tmp_path / "t" / "1.mp4"), "series_id": 3})
    probe.send_command("downloads.fetch", {"key": "dub:2", "url": "https://site/#cvh=2",
                                           "path": str(tmp_path / "t" / "2.mp4"), "series_id": 3})
    first = (await asyncio.wait_for(started.queue.get(), 3)).payload
    assert first == {"key": "dub:1", "series_id": 3, "path": str(tmp_path / "t" / "1.mp4")}
    await asyncio.wait_for(finished.queue.get(), 3)
    second = (await asyncio.wait_for(started.queue.get(), 3)).payload
    assert second["key"] == "dub:2"


@pytest.mark.asyncio
@pytest.mark.parametrize("how", [{"ignored": 1}, {"plan": "removed"}])
async def test_the_file_of_a_row_the_configuration_took_out_of_the_plan_goes_and_the_row_is_not_fetched(system, how):
    _, _, dl, probe, db_path, media_dir = system
    path = os.path.join(media_dir, "e1.mp4")
    open(path, "wb").close()
    _add_item(db_path, "uid1", 1, status="completed", filename="e1.mp4", **({"plan": "candidate"} | how))
    await probe.request("downloads.settle", {"series_id": 2}, timeout=10)  # a sync brings the disk to the plan (I-59)
    assert await _wait(lambda: not os.path.exists(path))
    assert _items(db_path)["uid1"]["status"] == "pending" and not _items(db_path)["uid1"]["final_filename"]


@pytest.mark.asyncio
async def test_a_file_of_a_compilation_a_sliced_file_and_a_file_the_plan_holds_under_the_same_name_are_not_unwanted(system):
    _, _, dl, probe, db_path, media_dir = system
    for name in ("compilation.mp4", "sliced.mp4", "shared.mp4"):
        open(os.path.join(media_dir, name), "wb").close()
    _add_item(db_path, "c", 1, status="completed", filename="compilation.mp4", plan="candidate", ignored=1)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE media_items SET episode_end=3 WHERE unique_id='c'")
        conn.commit()
    _add_item(db_path, "s", 4, status="completed", filename="sliced.mp4", plan="candidate", ignored=1)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE media_items SET slicing_status='completed' WHERE unique_id='s'")
        conn.commit()
    _add_item(db_path, "off", 5, status="completed", filename="shared.mp4", plan="candidate", ignored=1)
    _add_item(db_path, "on", 6, status="completed", filename="shared.mp4")
    probe.publish_event("scan.plan.updated", {"series_id": 2})
    await _quiet()
    assert all(os.path.exists(os.path.join(media_dir, name)) for name in ("compilation.mp4", "sliced.mp4", "shared.mp4"))


@pytest.mark.asyncio
@pytest.mark.parametrize("state, allowed", [("pending", True), ("error", False)])
async def test_a_download_is_cancelled_only_in_the_states_the_table_allows(system, state, allowed):
    """A task that waits or runs is cancelled; a task that failed has no process to cancel, and is refused with the code."""
    _, _, dl, probe, db_path, media_dir = system
    _add_item(db_path, "uid1", 1)
    _add_task(db_path, "uid1", state, os.path.join(media_dir, "e1.mp4"))
    task = _tasks(db_path)[0]
    if allowed:
        assert (await probe.request("downloads.cancel", {"task_id": task["id"]}, timeout=10))["cancelled"] is True
        assert _tasks(db_path) == []
    else:
        with pytest.raises(BusRequestError, match="not_allowed"):
            await probe.request("downloads.cancel", {"task_id": task["id"]}, timeout=10)
        assert len(_tasks(db_path)) == 1


@pytest.mark.asyncio
async def test_the_download_of_a_reference_asks_for_a_whole_video_and_the_fetch_of_a_voice_over_does_not(system, tmp_path):
    _, fake, dl, probe, db_path, _ = system
    fake.stream_replies[PAGE_URL] = {"handled": True, "stream_url": "https://cdn.test/ref.m3u8", "headers": {}}
    fake.stream_replies["https://site/#cvh=9"] = {"handled": True, "stream_url": "https://cdn.test/dub.m3u8", "headers": {}}
    _add_item(db_path, "web1", 1, series_id=3, source_url=PAGE_URL)
    probe.publish_event("downloads.dispatch", {"series_id": 3})
    assert await _wait(lambda: _items(db_path)["web1"]["status"] == "completed")
    probe.send_command("downloads.fetch", {"key": "dub:1", "url": "https://site/#cvh=9",
                                           "path": str(tmp_path / "d.mp4"), "series_id": 3})
    assert await _wait(lambda: "https://cdn.test/dub.m3u8" in dl.kwargs)
    assert dl.kwargs["https://cdn.test/ref.m3u8"].get("whole_video") is True
    assert "whole_video" not in dl.kwargs["https://cdn.test/dub.m3u8"]


# --- I-59: the disk changes in a sync alone ------------------------------------------------------------------------------

SITUATIONS = ("displaced", "taken_out", "switched_off", "task_outside_plan")


def _situation(db_path, media_dir, name) -> list[str]:
    """A state the plan alone decides about; returns the files that a sync removes."""
    if name == "displaced":
        _touch(media_dir, "e01 1080p.mp4")
        _add_item(db_path, "win", 1, status="completed", filename="e01 1080p.mp4")
        _add_item(db_path, "lose", 1, plan="replaced", status="completed", filename="e01 360p.mp4", superseded_by="win")
        return [_touch(media_dir, "e01 360p.mp4")]
    if name in ("taken_out", "switched_off"):
        how = {"plan": "removed"} if name == "taken_out" else {"plan": "candidate", "ignored": 1}
        _add_item(db_path, "out", 1, status="completed", filename="e1.mp4", **how)
        return [_touch(media_dir, "e1.mp4")]
    _add_item(db_path, "gone", 2, plan="redundant")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO download_tasks (task_key, series_id, video_url, save_path, status, task_type) "
                     "VALUES ('gone', 2, 'u', '/tmp/x', 'pending', 'vk_video')")
        conn.commit()
    return []


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["plan", "disk_check", "settle", "dispatch"])
@pytest.mark.parametrize("name", SITUATIONS)
async def test_what_the_plan_alone_decides_reaches_the_disk_in_a_sync_and_never_on_a_recount_or_a_disk_check(
        system, name, trigger):
    _, _, dl, probe, db_path, media_dir = system
    dl.gate = asyncio.Event()  # nothing is fetched while it is looked at
    files = _situation(db_path, media_dir, name)
    tasks_before = len(_tasks(db_path))
    if trigger == "plan":
        probe.publish_event("scan.plan.updated", {"series_id": 2})
        await probe.request("downloads.queue.get", {}, timeout=10)  # the plan was heard: its handler took its turn
    elif trigger == "disk_check":
        await probe.request("downloads.fs.sync", {"series_id": 2}, timeout=10)
    else:
        await probe.request(f"downloads.{trigger}", {"series_id": 2}, timeout=10)
    # A displaced file goes when the container of its winner is made (packing, stage 11), not at settle or dispatch.
    synced = trigger in ("settle", "dispatch") and name != "displaced"
    if synced:
        assert await _wait(lambda: not any(os.path.exists(path) for path in files))
    assert all(os.path.exists(path) for path in files) is (not synced or not files)
    if name == "task_outside_plan":
        assert len(_tasks(db_path)) == (0 if synced else tasks_before)


@pytest.mark.asyncio
async def test_the_run_of_a_series_hears_whether_downloads_has_work_of_it(system):
    """A task that waits or runs is work of the run of the series (ЭТАП_11); when the last ends, the run is told."""
    _, _, dl, probe, db_path, _ = system
    states = probe.bus.subscribe("work.series.state")
    assert (await probe.request("downloads.work.get", {"series_id": 2}, timeout=10))["active"] is False
    dl.gate = asyncio.Event()
    _add_item(db_path, "uid1", 1)
    await probe.request("downloads.dispatch", {"series_id": 2}, timeout=10)
    assert await _wait(lambda: len(_tasks(db_path)) == 1)
    busy = await probe.request("downloads.work.get", {"series_id": 2}, timeout=10)
    assert {key: busy[key] for key in ("owner", "series_id", "active")} == {
        "owner": "downloads", "series_id": 2, "active": True}
    dl.gate.set()
    assert await _wait(lambda: _items(db_path)["uid1"]["status"] == "completed")
    assert await _wait(lambda: not any(task["status"] in ("pending", "downloading", "processing", "publishing")
                                       for task in _tasks(db_path)))
    assert (await probe.request("downloads.work.get", {"series_id": 2}, timeout=10))["active"] is False
    # Every change of the state is told once, numbered in the order it was counted (I-60): the work, then its end.
    told = [(await asyncio.wait_for(states.queue.get(), 5)).payload]
    while not (told[-1]["active"] is False and any(each["active"] for each in told)):
        told.append((await asyncio.wait_for(states.queue.get(), 5)).payload)
    assert [each["active"] for each in told][-2:] == [True, False]
    assert [each["seq"] for each in told] == sorted({each["seq"] for each in told})


@pytest.mark.asyncio
async def test_an_interface_that_connects_gets_the_queues_as_they_are(system):
    bus, _, dl, probe, db_path, media_dir = system
    downloads = bus.subscribe("downloads.queue.changed")
    packing = bus.subscribe("downloads.pack.queue.changed")

    probe.publish_event("gateway.sse.clients", {"count": 1})

    got = await asyncio.wait_for(downloads.queue.get(), 5)
    assert "tasks" in got.payload and "count" in got.payload
    assert (await asyncio.wait_for(packing.queue.get(), 5)).payload == {"tasks": [], "count": 0}
