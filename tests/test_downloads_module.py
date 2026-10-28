"""Тесты модуля downloads: catalog настоящий, rules/settings — фейк,
загрузчик — инжектированная корутина (yt-dlp не нужен).
"""
import asyncio
import os
import sqlite3
import subprocess
import sys

from unittest.mock import AsyncMock, patch

import pytest

from core import BaseModule, Bus, BusRequestError, Runner
from core.db import Database
from modules.catalog import CatalogModule
from modules.downloads import DownloadsModule
from modules.downloads import ytdlp


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
        super().__init__(bus)

    def register(self):
        self.handle("rules.format_filename", self.on_format)
        self.handle("settings.value.get", self.on_get)
        self.handle("sources.video.stream", self.on_stream)

    async def on_stream(self, env):
        url = env.payload["url"]
        self.stream_requests.append(url)
        self.stream_payloads.append(dict(env.payload))
        if url in self.stream_raises:
            raise RuntimeError("sources unavailable")
        return self.stream_replies.get(url, {"handled": False})

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
        self.timeouts: dict[str, int] = {}   # url -> how many more attempts end in a network timeout
        self.started: list[str] = []
        self.gate: asyncio.Event | None = None
        self.concurrent = 0
        self.max_concurrent = 0

    async def __call__(self, url, path, on_progress, *, threads=6, **kw):
        self.threads = threads
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
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode, "
            "parser_profile_id) VALUES (2, 'https://vkvideo.ru/@c|т', 'Т2', "
            "'T2', 'vk', ?, 'waiting', 'vk_video', 0, 'get_all', 1)",
            (str(media_dir),))
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode, "
            "parser_profile_id) VALUES (3, 'https://1.ruchime.org/release/x', "
            "'Т3', 'T3', '1.ruchime.org', ?, 'waiting', 'web_video', 0, "
            "'get_all', 1)", (str(media_dir),))
        conn.commit()
    return str(db_path), str(media_dir)


def _add_item(db_path, uid, episode, *, plan="in_plan_single",
              status="pending", filename=None, ignored=0, download_url=None,
              series_id=2, source_url=None, superseded_by=None):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO media_items (series_id, unique_id, source_title, "
            "episode_start, plan_status, status, is_ignored_by_user, "
            "source_url, publication_date, slicing_status, is_available, "
            "final_filename, download_url, superseded_by) VALUES (?, ?, ?, ?, ?, "
            "?, ?, ?, '2026-01-01 00:00:00', 'none', 1, ?, ?, ?)",
            (series_id, uid, f"Серия {episode}", episode, plan, status, ignored,
             source_url or f"https://vk.com/video-1_{episode}", filename,
             download_url, superseded_by))
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
async def test_plan_updated_adopts_only_no_downloads(system):
    """Инвариант контракта: scan.plan.updated делает ТОЛЬКО усыновление.
    Если файла на диске нет — НЕ создаёт задач и НЕ запускает загрузку
    (загрузку стартует лишь явный скан через downloads.dispatch)."""
    _, _, dl, probe, db_path, _ = system
    _add_item(db_path, "uid1", 1)  # файла на диске НЕТ
    probe.publish_event("scan.plan.updated", {"series_id": 2})
    await asyncio.sleep(0.2)
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
    probe.publish_event("downloads.dispatch", {"series_id": 2})
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
    for _ in range(50):
        while not sub.queue.empty():
            flags = sub.queue.get_nowait().payload["flags"]
        if flags is not None:
            break
        await asyncio.sleep(0.02)
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
    # имитируем недокачанные артефакты yt-dlp на диске
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    open(save_path, "wb").close()
    open(save_path + ".part", "wb").close()

    reply = await probe.request("downloads.cancel",
                                {"task_id": task["id"]}, timeout=10)
    assert reply["cancelled"] is True

    # задача снята, элемент вернулся в pending (план/игнор не тронуты)
    assert _tasks(db_path) == []
    item = _items(db_path)["uid1"]
    assert item["status"] == "pending"
    assert item["plan_status"] == "in_plan_single"
    assert item["is_ignored_by_user"] == 0
    # файл и .part удалены
    assert not os.path.exists(save_path)
    assert not os.path.exists(save_path + ".part")


@pytest.mark.asyncio
async def test_cancel_unknown_task_errors(system):
    bus, _, dl, probe, db_path, media_dir = system
    with pytest.raises(BusRequestError, match="не найдена"):
        await probe.request("downloads.cancel", {"task_id": 999}, timeout=5)


def test_is_allvideo_url():
    assert ytdlp.is_allvideo_url("https://fsst.online/embed/992227/")
    assert ytdlp.is_allvideo_url("https://incvideo1.online/embed/123/")
    assert not ytdlp.is_allvideo_url("https://vk.com/video-170515668_456247993")
    assert not ytdlp.is_allvideo_url("https://vkvideo.ru/video_ext.php")


@pytest.mark.asyncio
async def test_download_allvideo_route(tmp_path):
    out_file = str(tmp_path / "test.mp4")
    progress = []

    async def on_progress(p):
        progress.append(p)

    with patch.object(ytdlp, "_resolve_allvideo_stream") as mock_resolve, \
         patch.object(ytdlp, "_download_direct_ffmpeg") as mock_ffmpeg:
        mock_resolve.return_value = ("https://cdn.example.com/test.mp4", "https://incvideo1.online/")
        mock_ffmpeg.return_value = (True, "")

        ok, err = await ytdlp.download("https://fsst.online/embed/992227/", out_file, on_progress)
        assert ok is True
        assert err == ""
        mock_resolve.assert_called_once_with("https://fsst.online/embed/992227/")
        mock_ffmpeg.assert_called_once_with("https://cdn.example.com/test.mp4", {"Referer": "https://incvideo1.online/"}, out_file, on_progress)


@pytest.mark.asyncio
async def test_download_direct_stream_with_headers(tmp_path):
    out_file = str(tmp_path / "direct.mp4")
    progress = []

    async def on_progress(p):
        progress.append(p)

    with patch.object(ytdlp, "_download_direct_ffmpeg") as mock_ffmpeg:
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
            on_progress
        )


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
         patch.object(ytdlp, "_phase_remux", AsyncMock(return_value=(True, ""))):
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
async def test_web_series_stream_request_failure_never_reaches_ytdlp(system, monkeypatch):
    from modules.downloads import module as downloads_module
    monkeypatch.setattr(downloads_module, "RETRY_DELAY_S", 0.01)
    _, fake, dl, probe, db_path, _ = system
    fake.stream_raises.add(PAGE_URL)
    _add_item(db_path, "web1", 1, series_id=3, source_url=PAGE_URL)

    probe.publish_event("downloads.dispatch", {"series_id": 3})

    assert await _wait(lambda: _items(db_path)["web1"]["status"] == "error")
    assert dl.started == []


@pytest.mark.asyncio
async def test_vk_series_keeps_ytdlp_fallback_when_stream_request_fails(system):
    _, fake, dl, probe, db_path, _ = system
    fake.stream_raises.add("https://vk.com/video-1_1")
    _add_item(db_path, "uid1", 1)

    probe.publish_event("downloads.dispatch", {"series_id": 2})

    assert await _wait(lambda: _items(db_path)["uid1"]["status"] == "completed")
    assert dl.started == ["https://vk.com/video-1_1"]


def _touch(media_dir, name):
    path = os.path.join(media_dir, name)
    with open(path, "wb") as f:
        f.write(b"video")
    return path


@pytest.mark.asyncio
async def test_superseded_file_is_removed_once_winner_is_on_disk(system):
    _, _, dl, probe, db_path, media_dir = system
    loser = _touch(media_dir, "e01 360p.mp4")
    winner = _touch(media_dir, "e01 1080p.mp4")
    _add_item(db_path, "win", 1, status="completed", filename="e01 1080p.mp4")
    _add_item(db_path, "lose", 1, plan="replaced", status="completed",
              filename="e01 360p.mp4", superseded_by="win")

    probe.publish_event("scan.plan.updated", {"series_id": 2})

    assert await _wait(lambda: not os.path.exists(loser))
    assert os.path.exists(winner)
    items = _items(db_path)
    assert items["lose"]["final_filename"] is None
    assert items["lose"]["status"] == "completed"   # remembers the fetched resolution
    assert items["win"]["final_filename"] == "e01 1080p.mp4"
    assert dl.started == []


@pytest.mark.asyncio
async def test_superseded_file_stays_while_winner_is_not_downloaded(system):
    _, _, _, probe, db_path, media_dir = system
    loser = _touch(media_dir, "e01 360p.mp4")
    _add_item(db_path, "win", 1)                               # still pending
    _add_item(db_path, "lose", 1, plan="replaced", status="completed",
              filename="e01 360p.mp4", superseded_by="win")

    probe.publish_event("scan.plan.updated", {"series_id": 2})
    await asyncio.sleep(0.2)

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
    await asyncio.sleep(0.2)

    assert os.path.exists(loser)


@pytest.mark.asyncio
async def test_winner_download_retires_superseded_file(system):
    """The lower file survives until the better one is actually downloaded."""
    _, _, dl, probe, db_path, media_dir = system
    loser = _touch(media_dir, "Серия 01 old.mp4")
    _add_item(db_path, "win", 1)
    _add_item(db_path, "lose", 1, plan="replaced", status="completed",
              filename="Серия 01 old.mp4", superseded_by="win")

    probe.publish_event("downloads.dispatch", {"series_id": 2})

    assert await _wait(lambda: not os.path.exists(loser))
    assert os.path.exists(os.path.join(media_dir, "Серия 01.mp4"))
    assert _items(db_path)["win"]["status"] == "completed"


@pytest.mark.asyncio
async def test_retirement_is_idempotent_and_survives_fs_sync(system):
    _, _, dl, probe, db_path, media_dir = system
    _touch(media_dir, "e01 360p.mp4")
    _touch(media_dir, "e01 1080p.mp4")
    _add_item(db_path, "win", 1, status="completed", filename="e01 1080p.mp4")
    _add_item(db_path, "lose", 1, plan="replaced", status="completed",
              filename="e01 360p.mp4", superseded_by="win")

    await probe.request("downloads.fs.sync", {"series_id": 2}, timeout=10)
    await probe.request("downloads.fs.sync", {"series_id": 2}, timeout=10)
    probe.publish_event("scan.plan.updated", {"series_id": 2})
    await asyncio.sleep(0.2)

    items = _items(db_path)
    assert items["lose"]["status"] == "completed"       # not sent back to pending
    assert items["lose"]["final_filename"] is None
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
    await asyncio.sleep(0.2)

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
    await asyncio.sleep(0.2)

    assert outside.exists()


@pytest.mark.asyncio
async def test_retired_file_leaves_downloaded_counter(system):
    bus, _, _, probe, db_path, media_dir = system
    _touch(media_dir, "e01 360p.mp4")
    _touch(media_dir, "e01 1080p.mp4")
    _add_item(db_path, "win", 1, status="completed", filename="e01 1080p.mp4")
    _add_item(db_path, "lose", 1, plan="replaced", status="completed",
              filename="e01 360p.mp4", superseded_by="win")
    sub = bus.subscribe("series.downloaded.changed")

    probe.publish_event("scan.plan.updated", {"series_id": 2})

    count = None
    for _ in range(100):
        while not sub.queue.empty():
            count = sub.queue.get_nowait().payload["count"]
        if count == 1:
            break
        await asyncio.sleep(0.02)
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

    probe.publish_event("scan.plan.updated", {"series_id": 2})

    assert await _wait(lambda: _tasks(db_path) == [])


# --- общая загрузка «поток → файл» (downloads.fetch) ----------------------------------------


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
    await asyncio.sleep(0.1)
    assert dl.max_concurrent == 2 and dl.concurrent == 2
    dl.gate.set()
    got = [(await asyncio.wait_for(sub.queue.get(), 5)).payload["key"] for _ in range(4)]
    assert sorted(got) == [f"dub:{i}" for i in range(4)]
    await asyncio.sleep(0.1)
    assert sub.queue.empty() and len(dl.started) == 4


@pytest.mark.asyncio
async def test_fetch_cancel_drops_the_queue_and_active_jobs_of_a_series(system, tmp_path):
    bus, fake, dl, probe, _, _ = system
    dl.gate = asyncio.Event()
    for i in range(4):
        probe.send_command("downloads.fetch", {
            "key": f"dub:{i}", "url": f"https://site/#cvh={i}",
            "path": str(tmp_path / f"{i}.mp4"), "series_id": 3 if i < 3 else 4})
    assert await _wait(lambda: dl.started == ["https://site/#cvh=0", "https://site/#cvh=1"])
    probe.send_command("downloads.fetch.cancel", {"series_id": 3})
    # The slots of the cancelled jobs go to the next series' job; the queued job of series 3 never runs.
    assert await _wait(lambda: "https://site/#cvh=3" in dl.started)
    dl.gate.set()
    assert await _wait(lambda: os.path.isfile(tmp_path / "3.mp4"))
    assert "https://site/#cvh=2" not in dl.started
    assert not any(os.path.exists(tmp_path / f"{i}.mp4") for i in (0, 1, 2))


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
    await asyncio.sleep(0.1)
    assert sub.queue.empty()
