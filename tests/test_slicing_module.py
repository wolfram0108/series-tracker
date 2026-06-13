"""Тесты модуля slicing: фильтр глав (чистые функции) + сквозная
нарезка с фейковым ffmpeg и фейковыми соседями.
"""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from core import BaseModule, Bus, BusRequestError, Runner
from core.db import Database
from modules.slicing import SlicingModule
from modules.slicing import chapters as ch


# --- фильтр глав (порт эвристик 1:1) -------------------------------------------------

def test_filter_marks_openings_endings_and_short():
    chapters = [
        {"time": "00:00:00", "title": "Опенинг"},
        {"time": "00:01:30", "title": "Серия 1"},
        {"time": "00:24:00", "title": "Серия 2"},
        {"time": "00:47:00", "title": "Ending"},
    ]
    filtered = ch.filter_chapters(chapters)
    assert [c["title"] for c in filtered] == ["Серия 1", "Серия 2"]
    garbage = ch.garbage_chapters(chapters)
    assert {g["title"] for g in garbage} == {"Опенинг", "Ending"}


def test_mark_manually():
    chapters = [{"time": "00:00:00", "title": "A"},
                {"time": "00:10:00", "title": "B"}]
    marked = ch.mark_manually(chapters, [1])
    assert marked[0]["is_garbage"] is False
    assert marked[1]["is_garbage"] is True
    assert marked[1]["garbage_reason"] == "Отмечено вручную"


def test_time_helpers():
    assert ch.format_seconds(3725) == "01:02:05"
    assert ch.time_to_seconds("01:02:05") == 3725
    assert ch.time_to_seconds("xx") is None


# --- сквозные сценарии ----------------------------------------------------------------

class FakeNeighbours(BaseModule):
    name = "fake_neighbours"

    def __init__(self, bus, series):
        self.series = series
        self.media_list: list[dict] = []
        self.ignored_calls: list[dict] = []
        self.status_calls: list[dict] = []
        self.settings = {}
        super().__init__(bus)

    def register(self):
        self.handle("catalog.series.get", self.on_series)
        self.handle("rules.format_filename", self.on_format)
        self.handle("scan.media.list", self.on_media)
        self.handle("scan.item.set_ignored", self.on_ignored)
        self.handle("downloads.item.set_filename", self.on_set_filename)
        self.handle("downloads.item.set_status", self.on_set_status)
        self.handle("settings.value.get", self.on_setting)

    async def on_series(self, env):
        return self.series

    async def on_format(self, env):
        ep = env.payload["episode_override"]
        return {"filename": f"Show s01e{ep:02d}.mp4"}

    async def on_media(self, env):
        return self.media_list

    async def on_ignored(self, env):
        self.ignored_calls.append(env.payload)

    async def on_set_filename(self, env):
        self.status_calls.append(("filename", env.payload))

    async def on_set_status(self, env):
        self.status_calls.append(("status", env.payload))

    async def on_setting(self, env):
        return {"key": env.payload["key"],
                "value": self.settings.get(env.payload["key"])}


class FakeFfmpeg:
    def __init__(self):
        self.calls: list[tuple] = []
        self.fail_episodes: set[str] = set()

    async def __call__(self, source, start, duration, output):
        self.calls.append((source, start, duration, output))
        if any(e in output for e in self.fail_episodes):
            return False, "битый поток (имитация)"
        os.makedirs(os.path.dirname(output), exist_ok=True)
        with open(output, "wb") as f:
            f.write(b"cut")
        return True, ""


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
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO parser_profiles (id, name) VALUES (1, 'p')")
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode, "
            "parser_profile_id) VALUES (2, 'u|q', 'Т', 'Show', 'vk', ?, "
            "'waiting', 'vk_video', 0, 'get_all', 1)", (str(media),))
        conn.commit()
    series = {"id": 2, "name_en": "Show", "source_type": "vk_video",
              "save_path": str(media), "parser_profile_id": 1,
              "season": None}
    bus = Bus()
    fake = FakeNeighbours(bus, series)
    ffmpeg = FakeFfmpeg()

    async def fake_fetch(url):
        raise AssertionError("yt-dlp не должен вызываться в этом тесте")

    slicing = SlicingModule(bus, Database(str(db_path)), ffmpeg=ffmpeg,
                            fetch_chapters=fake_fetch)
    probe = Probe(bus)
    runner = Runner(bus, [fake, slicing, probe])
    await runner.start()
    yield bus, fake, ffmpeg, probe, str(db_path), str(media)
    await runner.stop()


# Первая глава всегда мусор у автофильтра (эвристика оригинала
# «длительность по времени начала»: 00:00:00 < 30 c) — здесь это
# опенинг, что соответствует реальным данным.
CHAPTERS = [{"time": "00:00:00", "title": "Опенинг"},
            {"time": "00:01:30", "title": "Серия 11"},
            {"time": "00:24:00", "title": "Серия 12"},
            {"time": "00:46:00", "title": "Серия 13"}]


def _add_compilation(db_path, uid="comp1", chapters=CHAPTERS,
                     slicing_status="none", filename="компиляция.mp4"):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO media_items (series_id, unique_id, source_title, "
            "season, episode_start, episode_end, plan_status, status, "
            "is_ignored_by_user, source_url, publication_date, chapters, "
            "slicing_status, is_available, final_filename) VALUES "
            "(2, ?, 'Серии 11-13', 1, 11, 13, 'in_plan_compilation', "
            "'completed', 0, 'https://vk.com/video-1_9', "
            "'2026-01-01 00:00:00', ?, ?, 1, ?)",
            (uid, json.dumps(chapters), slicing_status, filename))
        conn.commit()


def _item(db_path, uid):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute(
            "SELECT * FROM media_items WHERE unique_id=?", (uid,)).fetchone())


def _sliced(db_path, uid):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT * FROM sliced_files WHERE source_media_item_unique_id=? "
            "ORDER BY episode_number", (uid,))]


async def _wait(predicate, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


@pytest.mark.asyncio
async def test_full_slice_flow(system):
    _, fake, ffmpeg, probe, db_path, media = system
    _add_compilation(db_path)
    open(os.path.join(media, "компиляция.mp4"), "wb").close()

    reply = await probe.request("slicing.task.create",
                                {"unique_id": "comp1"}, timeout=10)
    assert reply == {"created": True, "chapters": 3}
    assert await _wait(
        lambda: _item(db_path, "comp1")["slicing_status"] == "completed")

    files = _sliced(db_path, "comp1")
    assert [f["episode_number"] for f in files] == [11, 12, 13]
    for ep in (11, 12, 13):
        assert os.path.exists(os.path.join(media, f"Show s01e{ep:02d}.mp4"))
    # длительности: разница глав; последняя — до конца (None)
    durations = [c[2] for c in ffmpeg.calls]
    assert durations == ["0:22:30", "0:22:00", None]
    # компиляция ушла из планов (семантика оригинала); команда
    # асинхронная — дожидаемся
    assert await _wait(lambda: fake.ignored_calls == [
        {"unique_id": "comp1", "is_ignored": True}])
    # задача удалена
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM slicing_tasks").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_resume_skips_completed_episodes(system):
    _, _, ffmpeg, probe, db_path, media = system
    _add_compilation(db_path)
    open(os.path.join(media, "компиляция.mp4"), "wb").close()
    # эпизод 11 уже нарезан ранее (усыновление при первом запуске)
    open(os.path.join(media, "Show s01e11.mp4"), "wb").close()

    await probe.request("slicing.task.create", {"unique_id": "comp1"},
                        timeout=10)
    assert await _wait(
        lambda: _item(db_path, "comp1")["slicing_status"] == "completed")
    # ffmpeg звали только для 12 и 13
    outputs = [os.path.basename(c[3]) for c in ffmpeg.calls]
    assert outputs == ["Show s01e12.mp4", "Show s01e13.mp4"]
    assert len(_sliced(db_path, "comp1")) == 3


@pytest.mark.asyncio
async def test_error_is_carrier_and_retryable(system):
    bus, _, ffmpeg, probe, db_path, media = system
    _add_compilation(db_path)
    open(os.path.join(media, "компиляция.mp4"), "wb").close()
    ffmpeg.fail_episodes.add("e12")
    sub = bus.subscribe("series.status.contribution")

    await probe.request("slicing.task.create", {"unique_id": "comp1"},
                        timeout=10)
    assert await _wait(
        lambda: _item(db_path, "comp1")["slicing_status"] == "error")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT status, error_message, progress_chapters "
                           "FROM slicing_tasks").fetchone()
    assert row[0] == "error" and "битый поток" in row[1]
    assert json.loads(row[2])["11"] == "completed"  # прогресс сохранён

    # ретрай из error разрешён (контракт роутов) и доделывает остаток
    ffmpeg.fail_episodes.clear()
    await probe.request("slicing.task.create", {"unique_id": "comp1"},
                        timeout=10)
    assert await _wait(
        lambda: _item(db_path, "comp1")["slicing_status"] == "completed")
    # свёртка по дороге побывала в slicing и error
    seen = [e.payload["flags"] for e in iter(
        lambda: sub.queue.get_nowait() if not sub.queue.empty() else None,
        None)]
    assert any(f["error"] for f in seen)
    assert any(f["slicing"] for f in seen)


@pytest.mark.asyncio
async def test_task_create_validates_chapter_count(system):
    _, _, _, probe, db_path, _ = system
    _add_compilation(db_path, chapters=CHAPTERS[:2])  # 2 главы на 3 эпизода
    with pytest.raises(BusRequestError, match="не совпадает"):
        await probe.request("slicing.task.create", {"unique_id": "comp1"},
                            timeout=10)


@pytest.mark.asyncio
async def test_verify_marks_missing(system):
    _, _, _, probe, db_path, media = system
    _add_compilation(db_path, slicing_status="completed")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO sliced_files (series_id, "
                     "source_media_item_unique_id, episode_number, "
                     "file_path, status) VALUES (2, 'comp1', 11, "
                     "'Show s01e11.mp4', 'completed'), (2, 'comp1', 12, "
                     "'Show s01e12.mp4', 'completed')")
        conn.commit()
    open(os.path.join(media, "Show s01e11.mp4"), "wb").close()  # 12 пропал

    reply = await probe.request("slicing.verify", {"unique_id": "comp1"},
                                timeout=10)
    assert reply == {"status": "completed_with_errors",
                     "has_missing_files": True}
    files = _sliced(db_path, "comp1")
    assert [f["status"] for f in files] == ["completed", "missing"]


@pytest.mark.asyncio
async def test_files_list_and_set_path_for_renaming(system):
    _, _, _, probe, db_path, _ = system
    _add_compilation(db_path, slicing_status="completed")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO sliced_files (series_id, "
                     "source_media_item_unique_id, episode_number, "
                     "file_path, status) VALUES (2, 'comp1', 11, "
                     "'старое.mp4', 'completed')")
        conn.commit()

    files = await probe.request("slicing.files.list", {"series_id": 2},
                                timeout=5)
    assert len(files) == 1
    probe.send_command("slicing.file.set_path",
                       {"id": files[0]["id"], "path": "новое.mp4"})
    assert await _wait(
        lambda: _sliced(db_path, "comp1")[0]["file_path"] == "новое.mp4")


@pytest.mark.asyncio
async def test_chapters_check_does_not_block_slicing(tmp_path):
    """Находка 53: проверка глав НЕ ставит slicing_status='pending',
    поэтому нарезка после проверки оглавления запускается; повторный
    запуск при уже созданной задаче — отвергается."""
    db_path = tmp_path / "test.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": "sqlite:///%s" % db_path,
                        "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    media = tmp_path / "media"; media.mkdir()
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO parser_profiles (id, name) VALUES (1, 'p')")
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode, "
            "parser_profile_id) VALUES (2, 'u|q', 'Т', 'Show', 'vk', ?, "
            "'waiting', 'vk_video', 0, 'get_all', 1)", (str(media),))
        conn.commit()
    _add_compilation(db_path, slicing_status="none")
    open(os.path.join(media, "компиляция.mp4"), "wb").close()

    series = {"id": 2, "name_en": "Show", "source_type": "vk_video",
              "save_path": str(media), "parser_profile_id": 1, "season": None}
    bus = Bus()
    fake = FakeNeighbours(bus, series)

    async def fetch(url):
        return CHAPTERS[1:]  # 3 главы = диапазон 11-13 (совпадает)

    slicing = SlicingModule(bus, Database(str(db_path)), ffmpeg=FakeFfmpeg(),
                            fetch_chapters=fetch)
    probe = Probe(bus)
    runner = Runner(bus, [fake, slicing, probe])
    await runner.start()
    try:
        await probe.request("slicing.chapters.get", {"unique_id": "comp1"},
                            timeout=10)
        # проверка глав не перевела в pending — нарезка не заблокирована
        assert _item(db_path, "comp1")["slicing_status"] == "none"

        reply = await probe.request("slicing.task.create",
                                    {"unique_id": "comp1"}, timeout=10)
        assert reply["created"] is True

        # активная задача → повторный запуск отвергается
        with pytest.raises(BusRequestError, match="уже"):
            await probe.request("slicing.task.create",
                                {"unique_id": "comp1"}, timeout=10)
    finally:
        await runner.stop()
