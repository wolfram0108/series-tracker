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
from modules.slicing.module import run_ffmpeg


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


def test_chapter_duration_by_interval_not_start_time():
    """Находка 54: длительность главы — интервал до следующей, а не её
    время начала. Глава в 00:00:00 с длинным интервалом не «короткая»."""
    chapters = [
        {"time": "00:00:00", "title": "1 Серия"},   # → 15:00 интервал
        {"time": "00:15:00", "title": "2 Серия"},   # → 15:10 интервал
        {"time": "00:30:10", "title": "Врезка"},     # → 10 сек, короткая
        {"time": "00:30:20", "title": "3 Серия"},    # последняя
    ]
    filtered = [c["title"] for c in ch.filter_chapters(chapters)]
    assert "1 Серия" in filtered           # 00:00:00 больше не бракуется
    assert filtered == ["1 Серия", "2 Серия", "3 Серия"]
    garbage = {g["title"]: g["garbage_reason"]
               for g in ch.garbage_chapters(chapters)}
    assert "Врезка" in garbage and "сек" in garbage["Врезка"]


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


# --- атомарность run_ffmpeg (VP7/VL2, находка 50) ------------------------------------

def _stub_ffmpeg(bin_dir: str, *, exit_code: int) -> None:
    """Поддельный ffmpeg: пишет «партиал» в свой последний аргумент
    (output) и выходит с заданным кодом. Имитирует частичную запись."""
    stub = os.path.join(bin_dir, "ffmpeg")
    with open(stub, "w") as f:
        f.write("#!/usr/bin/env python3\n"
                "import sys\n"
                "open(sys.argv[-1], 'wb').write(b'partial')\n"
                f"sys.exit({exit_code})\n")
    os.chmod(stub, 0o755)


@pytest.mark.asyncio
async def test_run_ffmpeg_no_final_on_failure(tmp_path, monkeypatch):
    """VP7/VL2: ffmpeg упал, записав частичный файл, — финального имени НЕ
    должно появиться (tmp→os.replace только при rc==0), иначе при повторе
    _init_progress усыновил бы битый партиал как готовый эпизод."""
    binp = tmp_path / "bin"
    binp.mkdir()
    _stub_ffmpeg(str(binp), exit_code=1)
    monkeypatch.setenv("PATH", f"{binp}:/usr/bin:/bin")
    out = str(tmp_path / "Season 01" / "Show s01e03.mp4")
    os.makedirs(os.path.dirname(out))

    ok, err = await run_ffmpeg("src.mp4", "00:00:00", "10", out)
    assert ok is False
    assert not os.path.exists(out)                    # финал не опубликован
    assert not os.path.exists(out + ".slicing.mp4")   # tmp снесён


@pytest.mark.asyncio
async def test_run_ffmpeg_publishes_final_on_success(tmp_path, monkeypatch):
    """Успех: tmp атомарно публикуется в финал, временный файл исчезает."""
    binp = tmp_path / "bin"
    binp.mkdir()
    _stub_ffmpeg(str(binp), exit_code=0)
    monkeypatch.setenv("PATH", f"{binp}:/usr/bin:/bin")
    out = str(tmp_path / "Show s01e03.mp4")

    ok, err = await run_ffmpeg("src.mp4", "00:00:00", None, out)
    assert ok is True and err == ""
    assert os.path.exists(out)                        # атомарно опубликован
    assert not os.path.exists(out + ".slicing.mp4")   # tmp ушёл в replace


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
        self.block: "asyncio.Event | None" = None  # задан → виснем после записи
        self.started = asyncio.Event()

    async def __call__(self, source, start, duration, output):
        self.calls.append((source, start, duration, output))
        if any(e in output for e in self.fail_episodes):
            return False, "битый поток (имитация)"
        os.makedirs(os.path.dirname(output), exist_ok=True)
        with open(output, "wb") as f:
            f.write(b"cut")
        if self.block is not None:  # имитация долгого ffmpeg до отмены (Д2)
            self.started.set()
            await self.block.wait()
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
async def test_sim_C5_slicing_aborts_removes_partial(system):
    """C5/Д2: удаление серии во время нарезки прерывает ffmpeg и сносит
    недоделанный выходной файл."""
    _, _, ffmpeg, probe, db_path, media = system
    _add_compilation(db_path)
    open(os.path.join(media, "компиляция.mp4"), "wb").close()
    ffmpeg.block = asyncio.Event()  # ffmpeg зависнет после записи частичного

    await probe.request("slicing.task.create", {"unique_id": "comp1"},
                        timeout=10)
    assert await _wait(lambda: ffmpeg.started.is_set())  # режет первую главу
    partial = os.path.join(media, "Show s01e11.mp4")
    assert os.path.exists(partial)  # частичный выход на диске

    probe.publish_event("series.deleted",
                        {"series_id": 2, "delete_from_qb": False})

    assert await _wait(lambda: not os.path.exists(partial))  # снесён
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM slicing_tasks").fetchone()[0] == 0


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
    # длительности: разница глав в секундах; последняя — до конца (None)
    durations = [c[2] for c in ffmpeg.calls]
    assert durations == ["1350", "1320", None]
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
    # 12 — потерянный файл, 13 — вообще не нарезан (нет записи)
    assert reply == {"status": "completed_with_errors",
                     "has_missing_files": True,
                     "missing_episodes": [12, 13]}
    files = _sliced(db_path, "comp1")
    assert [f["status"] for f in files] == ["completed", "missing"]


@pytest.mark.asyncio
async def test_verify_skips_when_path_unavailable(system):
    """F7/VF7: путь/NAS недоступен → сверка нарезки пропускается; дети НЕ
    помечаются missing, исходник НЕ возвращается в дозагрузку (иначе скан
    при отвале NAS массово портит статусы)."""
    _, fake, _, probe, db_path, media = system
    _add_compilation(db_path, slicing_status="completed")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO sliced_files (series_id, "
                     "source_media_item_unique_id, episode_number, "
                     "file_path, status) VALUES (2, 'comp1', 11, "
                     "'Show s01e11.mp4', 'completed')")
        conn.commit()
    fake.series = {**fake.series, "save_path": "/nonexistent/nas"}

    reply = await probe.request("slicing.verify", {"unique_id": "comp1"},
                                timeout=10)
    assert reply == {"status": "completed", "skipped": True}
    assert [f["status"] for f in _sliced(db_path, "comp1")] == ["completed"]
    assert ("status", {"unique_id": "comp1", "status": "pending"}) \
        not in fake.status_calls


@pytest.mark.asyncio
async def test_verify_incomplete_range_not_completed(system):
    """Находка 56: посерийная сверка. Все имеющиеся куски на диске, но
    нарезаны не все серии из диапазона (нарезка оборвалась) — статус
    completed_with_errors, а не completed; кнопка разблокируется."""
    _, _, _, probe, db_path, media = system
    _add_compilation(db_path, slicing_status="completed")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO sliced_files (series_id, "
                     "source_media_item_unique_id, episode_number, "
                     "file_path, status) VALUES (2, 'comp1', 11, "
                     "'Show s01e11.mp4', 'completed'), (2, 'comp1', 12, "
                     "'Show s01e12.mp4', 'completed')")
        conn.commit()
    open(os.path.join(media, "Show s01e11.mp4"), "wb").close()
    open(os.path.join(media, "Show s01e12.mp4"), "wb").close()  # 13 не нарезан

    reply = await probe.request("slicing.verify", {"unique_id": "comp1"},
                                timeout=10)
    assert reply == {"status": "completed_with_errors",
                     "has_missing_files": False,
                     "missing_episodes": [13]}


@pytest.mark.asyncio
async def test_verify_full_range_completed(system):
    """Полный набор серий на диске → completed (кнопка блокируется)."""
    _, _, _, probe, db_path, media = system
    _add_compilation(db_path, slicing_status="slicing")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO sliced_files (series_id, "
                     "source_media_item_unique_id, episode_number, "
                     "file_path, status) VALUES (2, 'comp1', 11, "
                     "'Show s01e11.mp4', 'completed'), (2, 'comp1', 12, "
                     "'Show s01e12.mp4', 'completed'), (2, 'comp1', 13, "
                     "'Show s01e13.mp4', 'completed')")
        conn.commit()
    for ep in (11, 12, 13):
        open(os.path.join(media, f"Show s01e{ep}.mp4"), "wb").close()

    reply = await probe.request("slicing.verify", {"unique_id": "comp1"},
                                timeout=10)
    assert reply == {"status": "completed",
                     "has_missing_files": False,
                     "missing_episodes": []}


@pytest.mark.asyncio
async def test_sliced_files_contribute_ready(system):
    """Готовность нарезанных: даже когда компиляция-исходник помечена
    ignored (как после нарезки) и из плановой свёртки выпала, наличие
    готового нарезанного файла даёт флаг ready."""
    bus, _, _, probe, db_path, media = system
    _add_compilation(db_path, slicing_status="completed")
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE media_items SET is_ignored_by_user=1 "
                     "WHERE unique_id='comp1'")
        conn.execute("INSERT INTO sliced_files (series_id, "
                     "source_media_item_unique_id, episode_number, "
                     "file_path, status) VALUES (2, 'comp1', 11, "
                     "'Show s01e11.mp4', 'completed')")
        conn.commit()
    open(os.path.join(media, "Show s01e11.mp4"), "wb").close()

    sub = bus.subscribe("series.status.contribution")
    await probe.request("slicing.verify", {"unique_id": "comp1"}, timeout=10)
    seen = [e.payload["flags"] for e in iter(
        lambda: sub.queue.get_nowait() if not sub.queue.empty() else None,
        None)]
    assert any(f.get("ready") for f in seen)
    # компиляция ignored → в slicing-свёртке её активность не светится
    assert all(not f.get("slicing") for f in seen)


@pytest.mark.asyncio
async def test_verify_missing_returns_source_to_download(system):
    """Задача 2: пропал ранее нарезанный файл → исходник возвращается
    в дозагрузку (снять игнор + status pending), чтобы downloads его
    усыновил/закачал, а пользователь смог перенарезать."""
    _, fake, _, probe, db_path, media = system
    _add_compilation(db_path, slicing_status="completed")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO sliced_files (series_id, "
                     "source_media_item_unique_id, episode_number, "
                     "file_path, status) VALUES (2, 'comp1', 11, "
                     "'Show s01e11.mp4', 'completed'), (2, 'comp1', 12, "
                     "'Show s01e12.mp4', 'completed'), (2, 'comp1', 13, "
                     "'Show s01e13.mp4', 'completed')")
        conn.commit()
    open(os.path.join(media, "Show s01e11.mp4"), "wb").close()
    open(os.path.join(media, "Show s01e12.mp4"), "wb").close()  # 13 пропал

    reply = await probe.request("slicing.verify", {"unique_id": "comp1"},
                                timeout=10)
    assert reply["status"] == "completed_with_errors"
    assert reply["has_missing_files"] is True
    assert reply["missing_episodes"] == [13]
    assert {"unique_id": "comp1", "is_ignored": False} in fake.ignored_calls
    assert ("status", {"unique_id": "comp1", "status": "pending"}) \
        in fake.status_calls


@pytest.mark.asyncio
async def test_verify_incomplete_does_not_return_to_download(system):
    """incomplete без пропаж (нарезка просто не доходила до всех серий)
    исходника не теряла — в дозагрузку не возвращаем."""
    _, fake, _, probe, db_path, media = system
    _add_compilation(db_path, slicing_status="slicing")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO sliced_files (series_id, "
                     "source_media_item_unique_id, episode_number, "
                     "file_path, status) VALUES (2, 'comp1', 11, "
                     "'Show s01e11.mp4', 'completed'), (2, 'comp1', 12, "
                     "'Show s01e12.mp4', 'completed')")
        conn.commit()
    open(os.path.join(media, "Show s01e11.mp4"), "wb").close()
    open(os.path.join(media, "Show s01e12.mp4"), "wb").close()  # 13 не нарезан

    reply = await probe.request("slicing.verify", {"unique_id": "comp1"},
                                timeout=10)
    assert reply["status"] == "completed_with_errors"
    assert reply["has_missing_files"] is False
    assert reply["missing_episodes"] == [13]
    assert fake.ignored_calls == []  # в дозагрузку не возвращали


@pytest.mark.asyncio
async def test_task_create_source_missing(system):
    """Задача 3: нажали нарезку, а исходника нет на диске → задача не
    создаётся, компиляция уходит в дозагрузку, ответ source_missing."""
    _, fake, _, probe, db_path, _ = system
    _add_compilation(db_path)  # filename компиляция.mp4 — файла на диске нет

    reply = await probe.request("slicing.task.create",
                                {"unique_id": "comp1"}, timeout=10)
    assert reply == {"created": False, "source_missing": True}
    q = await probe.request("slicing.queue.get", {}, timeout=5)
    assert q["count"] == 0  # задача нарезки не создана
    assert {"unique_id": "comp1", "is_ignored": False} in fake.ignored_calls
    assert ("status", {"unique_id": "comp1", "status": "pending"}) \
        in fake.status_calls


@pytest.mark.asyncio
async def test_source_delete_removes_file_and_clears_filename(system):
    """Задача 1: ручное удаление исходника нарезанной компиляции."""
    _, fake, _, probe, db_path, media = system
    _add_compilation(db_path, slicing_status="completed", filename="comp.mp4")
    src = os.path.join(media, "comp.mp4")
    open(src, "wb").close()

    reply = await probe.request("slicing.source.delete",
                                {"unique_id": "comp1"}, timeout=10)
    assert reply == {"deleted": True}
    assert not os.path.exists(src)
    assert ("filename", {"unique_id": "comp1", "filename": None}) \
        in fake.status_calls


@pytest.mark.asyncio
async def test_source_delete_already_absent(system):
    _, fake, _, probe, db_path, _ = system
    _add_compilation(db_path, slicing_status="completed", filename="нет.mp4")
    reply = await probe.request("slicing.source.delete",
                                {"unique_id": "comp1"}, timeout=10)
    assert reply == {"deleted": False, "reason": "already_absent"}
    assert ("filename", {"unique_id": "comp1", "filename": None}) \
        in fake.status_calls


@pytest.mark.asyncio
async def test_source_delete_rejects_unsliced(system):
    _, _, _, probe, db_path, _ = system
    _add_compilation(db_path, slicing_status="none", filename="comp.mp4")
    with pytest.raises(BusRequestError, match="нарезанной"):
        await probe.request("slicing.source.delete",
                            {"unique_id": "comp1"}, timeout=10)


@pytest.mark.asyncio
async def test_files_list_triggers_verify(system):
    """on_files_list сверяет компиляции с диском: пропавший ребёнок
    помечается и исходник уходит в дозагрузку."""
    _, fake, _, probe, db_path, _ = system
    _add_compilation(db_path, slicing_status="completed")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO sliced_files (series_id, "
                     "source_media_item_unique_id, episode_number, "
                     "file_path, status) VALUES (2, 'comp1', 11, "
                     "'Show s01e11.mp4', 'completed')")
        conn.commit()  # файла на диске нет

    await probe.request("slicing.files.list", {"series_id": 2}, timeout=10)
    assert _item(db_path, "comp1")["slicing_status"] == "completed_with_errors"
    assert {"unique_id": "comp1", "is_ignored": False} in fake.ignored_calls


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


def test_duration_handles_over_24h():
    """Находка 55: компиляции длиннее суток дают время 24:02:29 —
    длительность считается через time_to_seconds, не strptime."""
    from modules.slicing.module import SlicingModule
    chs = [{"time": "23:50:00"}, {"time": "24:05:00"}, {"time": "25:00:00"}]
    assert SlicingModule._duration(chs[0], chs, 0) == "900"   # 15 мин
    assert SlicingModule._duration(chs[1], chs, 1) == "3300"  # 55 мин
    assert SlicingModule._duration(chs[2], chs, 2) is None    # последняя
