"""Тесты торрент-конвейера (Р-14): чистая машина стадий + сквозные
сценарии с фейковым qBittorrent (стенд не нужен).
"""
import asyncio
import logging
import json
import sqlite3

import pytest

from core import BaseModule, Bus, Runner
from core.db import Database
from modules.catalog import CatalogModule
from modules.torrents import TorrentsModule
from modules.torrents import pipeline as pl
from core.reasons import parse
from series_factory import insert_series
from settle import settle, wait_until
from schema_db import migrated


# --- чистая машина -----------------------------------------------------------------

def test_magnet_path_runs_only_for_metadata():
    # инвариант ядра: запуск ровно до метаданных, затем немедленная пауза
    assert pl.decide(pl.AWAITING_METADATA, {}, False) == \
        ("resume", pl.POLLING_FOR_SIZE)
    assert pl.decide(pl.POLLING_FOR_SIZE, {"total_size": 0}, False) == \
        (None, None)
    assert pl.decide(pl.POLLING_FOR_SIZE, {"total_size": 5}, False) == \
        ("pause", pl.AWAITING_PAUSE)


def test_pause_stage_forces_pause_if_active():
    assert pl.decide(pl.AWAITING_PAUSE, {"state": "downloading"}, False) == \
        ("force_pause", None)
    assert pl.decide(pl.AWAITING_PAUSE, {"state": "pausedDL"}, False) == \
        (None, pl.RENAMING)
    assert pl.decide(pl.AWAITING_PAUSE, {"state": "stoppedDL"}, False) == \
        (None, pl.RENAMING)  # имена состояний qBit 5.x


def test_rename_recheck_activate_flow():
    assert pl.decide(pl.RENAMING, {"state": "pausedDL"}, False) == \
        ("rename", pl.RECHECKING)
    assert pl.decide(pl.RECHECKING, {"state": "pausedDL"}, False) == \
        ("recheck", None)
    assert pl.decide(pl.RECHECKING, {"state": "checkingDL"}, True) == \
        (None, None)  # проверка идёт — ждём
    assert pl.decide(pl.RECHECKING, {"state": "pausedDL"}, True) == \
        (None, pl.ACTIVATING)
    assert pl.decide(pl.ACTIVATING, {"state": "pausedDL"}, False) == \
        ("resume_and_complete", None)
    assert pl.decide(pl.ACTIVATING, {"state": "downloading"}, False) == \
        ("complete", None)


# --- сквозные сценарии ----------------------------------------------------------------

class FakeQbt:
    """Управляемый qBittorrent: словарь hash -> info."""

    def __init__(self):
        self.torrents: dict[str, dict] = {}
        self.files: dict[str, list[dict]] = {}  # hash -> файлы раздачи
        self.calls: list[tuple] = []
        self._checking: dict[str, int] = {}  # hash -> тиков до конца recheck

    def _log(self, *call):
        self.calls.append(call)

    async def torrents_info(self, hashes=None, *, category=None):
        # проверка recheck завершается сама через пару опросов
        for h in list(self._checking):
            self._checking[h] -= 1
            if self._checking[h] <= 0:
                del self._checking[h]
                self.torrents[h]["state"] = "pausedDL"
        rows = [{"hash": h, **i} for h, i in self.torrents.items()]
        if hashes is not None:
            rows = [r for r in rows if r["hash"] in hashes]
        if category is not None:
            rows = [r for r in rows if r.get("category") == category]
        return rows

    async def torrent_files(self, torrent_hash):
        return self.files.get(torrent_hash, [])

    async def set_file_priority(self, torrent_hash, indices, priority):
        if not indices:
            return
        self._log("filePrio", torrent_hash, tuple(indices), priority)
        for f in self.files.get(torrent_hash, []):
            if f.get("index") in indices:
                f["priority"] = priority

    async def pause(self, hashes):
        self._log("pause", tuple(hashes))
        for h in hashes:
            self.torrents[h]["state"] = "pausedDL"

    async def resume(self, hashes):
        self._log("resume", tuple(hashes))
        for h in hashes:
            self.torrents[h]["state"] = "downloading"

    async def recheck(self, hashes):
        self._log("recheck", tuple(hashes))
        for h in hashes:
            self.torrents[h]["state"] = "checkingDL"
            self._checking[h] = 2

    async def delete(self, hashes, *, delete_files):
        self._log("delete", tuple(hashes), delete_files)
        for h in hashes:
            self.torrents.pop(h, None)

    async def create_category(self, name, save_path=""):
        self._log("create_category", name)

    async def default_save_path(self):
        return "/downloads"

    async def login(self):
        pass

    async def close(self):
        pass


class FakeRenaming(BaseModule):
    name = "fake_renaming"

    def __init__(self, bus):
        self.calls: list[dict] = []
        self.fail = False
        super().__init__(bus)

    def register(self):
        self.handle("renaming.process_torrent", self.on_process)

    async def on_process(self, env):
        self.calls.append(env.payload)
        if self.fail:
            raise RuntimeError("правила не настроены (имитация)")
        return {"renamed": 2}


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    migrated(path)
    with sqlite3.connect(path) as conn:
        insert_series(conn, id=1, name="Т", name_en="T", site="kinozal", save_path="/media/t", state="waiting",
                      url="http://x", source_type="torrent")
        # 2: a video series whose path is not there; it has no torrent files, so the module has nothing to say of it
        insert_series(conn, id=2, name="В", name_en="V", site="vk", save_path="/nonexistent/nas",
                      url="https://vkvideo.ru/@c|q", source_type="vk_video")
        conn.commit()
    return str(path)


@pytest.fixture
async def system(db_path):
    bus = Bus()
    db = Database(db_path)
    qbt = FakeQbt()
    renaming = FakeRenaming(bus)
    torrents = TorrentsModule(bus, db, qbt=qbt, pipeline_poll=0.02,
                              monitor_active=0.05, monitor_idle=0.2)
    runner = Runner(bus, [renaming, CatalogModule(bus, db), torrents,
                          Probe(bus)])
    await runner.start()
    _watch(bus, runner)
    yield bus, qbt, renaming, runner.modules[-1], db_path
    await runner.stop()
    _watch()


def _agent_tasks(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return {r["torrent_hash"]: dict(r) for r in conn.execute(
            "SELECT * FROM agent_tasks")}


_watched: tuple = ()  # the bus and the modules of the running system, whose steps `_wait` waits for


def _watch(bus=None, runner=None) -> None:
    global _watched
    _watched = (bus, *runner.modules) if runner is not None else ()


async def _wait(predicate):
    """Until the condition holds, woken by the steps of the system; the safeguard is the suite's one, a failure, not a
    pace: a short one turned red under the load of a parallel run (the pipeline moves on timers of its own)."""
    bus, *modules = _watched
    return await wait_until(predicate, bus, *modules)


def _torrent(torrent_id="tid1"):
    return {"torrent_id": torrent_id, "link": "https://dl.kz/1",
            "magnet": None, "date_time": "01.01.2026 00:00:00",
            "quality": None, "episodes": None}


@pytest.mark.asyncio
async def test_file_torrent_full_pipeline(system):
    bus, qbt, renaming, probe, db_path = system
    qbt.torrents["h1"] = {"state": "pausedDL", "total_size": 100,
                          "progress": 0.5}
    sub = bus.subscribe("torrents.queue.changed")

    reply = await probe.request("torrents.register", {
        "series_id": 1, "torrent": _torrent(), "qb_hash": "h1",
        "link_type": "file", "replaces": None}, timeout=5)
    assert reply == {"existed": False}

    # конвейер дойдёт до конца: rename → recheck → activate → done
    assert await _wait(lambda: not _agent_tasks(db_path))
    assert renaming.calls == [{"series_id": 1, "qb_hash": "h1"}]
    actions = [c[0] for c in qbt.calls]
    assert "recheck" in actions
    assert actions.index("recheck") < actions.index("resume")
    # пауза никогда не снималась до завершения переименования
    resume_at = qbt.calls.index(("resume", ("h1",)))
    assert all(c[0] != "resume" for c in qbt.calls[:resume_at])
    # раздача зарегистрирована активной
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT is_active, qb_hash FROM torrents "
                           "WHERE torrent_id='tid1'").fetchone()
    assert row == (1, "h1")
    # очередь дошла до count=0 (сигнал scan'у)
    counts = []
    while not sub.queue.empty():
        counts.append(sub.queue.get_nowait().payload["count"])
    assert counts[-1] == 0


@pytest.mark.asyncio
async def test_magnet_runs_only_until_metadata(system):
    _, qbt, _, probe, db_path = system
    # метаданных ещё нет
    qbt.torrents["h2"] = {"state": "stoppedDL", "total_size": 0,
                          "progress": 0.0}
    await probe.request("torrents.register", {
        "series_id": 1, "torrent": _torrent("tid2"), "qb_hash": "h2",
        "link_type": "magnet", "replaces": None}, timeout=5)

    # запустился ради метаданных
    assert await _wait(lambda: ("resume", ("h2",)) in qbt.calls)
    # метаданные пришли
    qbt.torrents["h2"]["total_size"] = 500
    # → немедленная пауза, дальше обычный путь до конца
    assert await _wait(lambda: ("pause", ("h2",)) in qbt.calls)
    assert await _wait(lambda: not _agent_tasks(db_path))
    # пауза случилась раньше recheck (инвариант)
    assert qbt.calls.index(("pause", ("h2",))) < \
        qbt.calls.index(("recheck", ("h2",)))


@pytest.mark.asyncio
async def test_replace_deactivates_old(system):
    _, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1, 'old', 'l', 1, 'hOld')")
        cur = conn.execute("SELECT id FROM torrents WHERE torrent_id='old'")
        old_id = cur.fetchone()[0]
        conn.execute("INSERT INTO torrent_files (torrent_db_id, "
                     "original_path, status) VALUES (?, 'a.mkv', 'renamed')",
                     (old_id,))
        conn.commit()
    qbt.torrents["hOld"] = {"state": "uploading", "progress": 1.0}
    qbt.torrents["h3"] = {"state": "pausedDL", "total_size": 9,
                          "progress": 0.0}

    await probe.request("torrents.register", {
        "series_id": 1, "torrent": _torrent("tid3"), "qb_hash": "h3",
        "link_type": "file",
        "replaces": {"torrent_id": "old", "qb_hash": "hOld",
                     "db_id": old_id}}, timeout=5)
    assert await _wait(lambda: not _agent_tasks(db_path))

    with sqlite3.connect(db_path) as conn:
        active = conn.execute("SELECT torrent_id, is_active FROM torrents "
                              "ORDER BY torrent_id").fetchall()
        files = conn.execute("SELECT COUNT(*) FROM torrent_files "
                             "WHERE torrent_db_id=?", (old_id,)).fetchone()
    # старая строка ОСТАЁТСЯ как история (is_active=0), новая активна
    assert ("old", 0) in active and ("tid3", 1) in active
    assert files[0] == 0  # записи о файлах старой раздачи сняты (И4)
    assert ("delete", ("hOld",), False) in qbt.calls  # старый hash убран из qBit


@pytest.mark.asyncio
async def test_same_hash_relist_no_duplicate_no_download(system):
    """ПУНКТ 3 / И1: трекер перевыложил ту же раздачу под новым ярлыком
    (новый torrent_id, тот же infohash). Регистрация по infohash:
    ничего не качаем, второй строки не плодим, старую раздачу из qBit НЕ
    удаляем — лишь переклеиваем ярлык/дату."""
    _, qbt, _, probe, db_path = system
    qbt.torrents["hSame"] = {"state": "pausedDL", "total_size": 100,
                             "progress": 0.5}
    # первая раздача проходит конвейер до конца
    await probe.request("torrents.register", {
        "series_id": 1, "torrent": _torrent("tidA"), "qb_hash": "hSame",
        "link_type": "file", "replaces": None}, timeout=5)
    assert await _wait(lambda: not _agent_tasks(db_path))
    qbt.calls.clear()
    qbt.torrents["hSame"].update(progress=1.0, state="uploading")  # докачана

    # перевыкладка: новый ярлык tidB, тот же infohash hSame; rolling →
    # scan укажет replaces на единственную активную строку (её же)
    with sqlite3.connect(db_path) as conn:
        old = conn.execute("SELECT id, torrent_id, qb_hash FROM torrents "
                           "WHERE qb_hash='hSame' AND is_active=1").fetchone()
    reply = await probe.request("torrents.register", {
        "series_id": 1, "torrent": _torrent("tidB"), "qb_hash": "hSame",
        "link_type": "file",
        "replaces": {"torrent_id": old[1], "qb_hash": old[2],
                     "db_id": old[0]}}, timeout=5)

    assert reply == {"existed": True}            # ничего не делаем (ПУНКТ 3)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT torrent_id, is_active FROM torrents "
                            "WHERE qb_hash='hSame'").fetchall()
    assert rows == [("tidB", 1)]                 # одна строка, ярлык обновлён (И1)
    assert not _agent_tasks(db_path)             # новой задачи нет — не качаем
    assert all(c[0] != "delete" for c in qbt.calls)  # из qBit не удаляли


@pytest.mark.asyncio
async def test_by_hash_prefers_active_and_count_ignores_history(system):
    """И2: при активной + исторической строке с ОДНИМ infohash поиск по
    hash возвращает активную. И4: счётчик скачанного считает только
    активные (исторические дубли не раздувают «16 вместо 8»)."""
    _, qbt, _, probe, db_path = system
    from modules.torrents.repository import TorrentsRepository
    from core.db import Database
    repo = TorrentsRepository(Database(db_path))
    with sqlite3.connect(db_path) as conn:
        # историческая (заменённая) строка того же hash — с «налипшим» файлом
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'hist','l',0,'hX')")
        hist = conn.execute("SELECT id FROM torrents WHERE "
                            "torrent_id='hist'").fetchone()[0]
        conn.execute("INSERT INTO torrent_files (torrent_db_id, original_path,"
                     " status) VALUES (?, 'a.mkv', 'renamed')", (hist,))
        # активная строка того же hash — настоящая
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'liveX','l',1,'hX')")
        live = conn.execute("SELECT id FROM torrents WHERE "
                            "torrent_id='liveX'").fetchone()[0]
        conn.execute("INSERT INTO torrent_files (torrent_db_id, original_path,"
                     " status) VALUES (?, 'b.mkv', 'renamed')", (live,))
        conn.commit()

    row = await repo.torrent_by_hash("hX")
    assert row["id"] == live and row["is_active"] == 1   # И2: активная

    counts = await repo.downloaded_counts()
    assert counts.get(1) == 1   # И4: только файл активной строки, не 2


@pytest.mark.asyncio
async def test_the_task_and_the_row_of_a_vanished_torrent_end_in_one_transaction(system):
    """No reader may see the task gone while the row is still active: both changes are one commit."""
    _, _, _, _, db_path = system
    from core.db import Database
    from modules.torrents.repository import TorrentsRepository
    db = Database(db_path)
    repo = TorrentsRepository(db)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, is_active, qb_hash) "
                     "VALUES (1,'tidv','l',1,'hv')")
        db_id = conn.execute("SELECT id FROM torrents WHERE torrent_id='tidv'").fetchone()[0]
        conn.execute("INSERT INTO torrent_files (torrent_db_id, original_path, status) VALUES (?, 'a.mkv', 'renamed')",
                     (db_id,))
        conn.commit()
    await repo.upsert_task({"torrent_hash": "hv", "series_id": 1, "torrent_id": "tidv", "stage": "polling_for_size"})
    commits: list[int] = []
    single = db.execute_many

    async def counted(statements):
        commits.append(len(statements))
        return await single(statements)

    db.execute_many = counted
    plain = db.execute

    async def forbidden(*args, **kwargs):
        raise AssertionError("a plain execute would commit one statement apart from the others")

    db.execute = forbidden
    await repo.drop_task_and_row("hv", db_id)
    db.execute = plain

    assert commits == [3]
    assert not _agent_tasks(db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT is_active FROM torrents WHERE id=?", (db_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM torrent_files WHERE torrent_db_id=?", (db_id,)).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_sim_C1_readd_after_qb_delete_repipelines(system):
    """Симуляция C1/Q1/Q2 (матрица ситуаций): торрент удалён из qB, строка
    БД осталась is_active=1 с 'renamed'-файлами. Пользователь жмёт скан →
    раздача добавляется заново (тот же контент → тот же infohash) → scan
    зовёт register на свежей (0%, на паузе) раздаче.

    ОЖИДАНИЕ: конвейер перезапускается и доводит раздачу (rename→recheck→
    activate). Если задача не создаётся — раздача висит на паузе 0% и не
    качается (дыра R2: ПУНКТ 3 'existed, ничего не делаю' для известного
    hash без проверки, докачан ли он реально)."""
    bus, qbt, renaming, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'tid','l',1,'hgone')")
        tid = conn.execute("SELECT id FROM torrents WHERE "
                           "torrent_id='tid'").fetchone()[0]
        conn.execute("INSERT INTO torrent_files (torrent_db_id, original_path,"
                     " renamed_path, status) VALUES (?, 'orig.mkv', "
                     "'Season 01/s01e01.mkv','renamed')", (tid,))
        conn.commit()
    # скан добавил заново → qB снова содержит infohash, СВЕЖИЙ (0%, пауза)
    qbt.torrents["hgone"] = {"state": "pausedDL", "total_size": 100,
                             "progress": 0.0}

    reply = await probe.request("torrents.register", {
        "series_id": 1,
        "torrent": {"torrent_id": "tid", "link": "l", "magnet": None,
                    "date_time": "02.02.2026", "quality": None,
                    "episodes": None},
        "qb_hash": "hgone", "link_type": "file", "replaces": None}, timeout=5)

    assert reply == {"existed": False}, "re-add не докачанной раздачи обязан запустить конвейер"
    assert await _wait(lambda: not _agent_tasks(db_path)), "конвейер должен пройти до конца"
    assert renaming.calls, "переименование должно запуститься заново"


@pytest.mark.asyncio
async def test_sim_P7_vanish_midpipeline_deactivates(system):
    """Симуляция P7: торрент исчез из qB посреди конвейера (пользователь
    удалил его во время работы). Задача снимается — и строка должна стать
    неактивной (симметрия с reconcile при старте), иначе остаётся фантомная
    is_active=1 без живой раздачи (дыра R3) → ложные счётчики и зацепка для
    повторной регистрации."""
    _, qbt, _, probe, db_path = system
    # magnet без метаданных — конвейер паркуется на ожидании размера
    qbt.torrents["hp7"] = {"state": "stoppedDL", "total_size": 0,
                           "progress": 0.0}
    await probe.request("torrents.register", {
        "series_id": 1, "torrent": _torrent("tidp7"), "qb_hash": "hp7",
        "link_type": "magnet", "replaces": None}, timeout=5)
    assert await _wait(lambda: ("resume", ("hp7",)) in qbt.calls)  # в конвейере

    qbt.torrents.pop("hp7", None)  # пользователь удалил торрент из qB
    assert await _wait(lambda: not _agent_tasks(db_path))  # задача снята

    with sqlite3.connect(db_path) as conn:
        active = conn.execute("SELECT is_active FROM torrents WHERE "
                              "torrent_id='tidp7'").fetchone()[0]
    assert active == 0, "исчезнувшая из qB раздача должна деактивироваться"


@pytest.mark.asyncio
async def test_sim_D1_downloaded_count_pushed_on_rename(system):
    """Д1: запись о файле (renaming → files.upsert) публикует
    series.downloaded.changed со счётчиком — карточка обновляет «скачано»
    по SSE без перезагрузки."""
    bus, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'td1','l',1,'hd1')")
        conn.commit()
    sub = bus.subscribe("series.downloaded.changed")

    await probe.request("torrents.db.files.upsert", {
        "qb_hash": "hd1", "files": [
            {"original_path": "a.mkv",
             "renamed_path": "Season 01/s01e01.mkv", "status": "renamed",
             "extracted_metadata": {"season": 1, "episode": 1}},
            {"original_path": "b.mkv",
             "renamed_path": "Season 01/s01e02.mkv", "status": "renamed",
             "extracted_metadata": {"season": 1, "episode": 2}}]},
        timeout=5)

    env = await asyncio.wait_for(sub.queue.get(), 2)
    assert env.payload == {"series_id": 1, "count": 2}


@pytest.mark.asyncio
async def test_sim_F7_path_unavailable_no_false_missing(system, tmp_path):
    """F7: путь/NAS недоступен → fs.verify не помечает файлы ложно missing
    и не запускает recheck."""
    _, qbt, _, probe, db_path = system
    gone = str(tmp_path / "nas_down")  # не существует
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE series SET save_path=? WHERE id=1", (gone,))
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'tf7','l',1,'hf7')")
        tid = conn.execute("SELECT id FROM torrents WHERE "
                           "torrent_id='tf7'").fetchone()[0]
        conn.execute("INSERT INTO torrent_files (torrent_db_id, original_path,"
                     " renamed_path, status) VALUES (?, 'o.mkv', "
                     "'Season 01/s01e01.mkv','renamed')", (tid,))
        conn.execute("INSERT INTO torrent_progress (qb_hash, series_id, "
                     "status, progress) VALUES ('hf7', 1, 'uploading', 100)")
        conn.commit()
    qbt.torrents["hf7"] = {"state": "uploading", "total_size": 9,
                           "progress": 1.0}

    reply = await probe.request("torrents.fs.verify", {"series_id": 1},
                                timeout=5)
    assert reply == {"missing": 0, "recheck_started": 0}
    with sqlite3.connect(db_path) as conn:
        st = conn.execute("SELECT status FROM torrent_files WHERE "
                          "torrent_db_id=?", (tid,)).fetchone()[0]
    assert st == "renamed"  # файл НЕ помечен ложно missing


@pytest.mark.asyncio
async def test_fs_verify_of_a_video_series_answers_at_once_and_says_nothing(system, caplog):
    """A video series has no torrent files: the module neither asks qBit nor warns about its path."""
    _, qbt, _, probe, _ = system
    calls_before = len(qbt.calls)
    with caplog.at_level(logging.WARNING):
        reply = await probe.request("torrents.fs.verify", {"series_id": 2}, timeout=5)
    assert reply == {"missing": 0, "recheck_started": 0}
    assert [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING] == []
    assert len(qbt.calls) == calls_before


@pytest.mark.asyncio
async def test_sim_P10_stuck_active_driven(system):
    """Симуляция P10 («Путешествие»): раздача активна и ЕСТЬ в qB, но
    застряла — на паузе 0%, без задачи конвейера. drive_incomplete (её
    зовёт скан) загоняет её в конвейер; повторно на уже возобновлённой —
    не плодит."""
    _, qbt, renaming, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'tidp10','l',1,'hp10')")
        conn.commit()
    qbt.torrents["hp10"] = {"state": "pausedDL", "total_size": 100,
                            "progress": 0.0}  # в qB, не докачан, без задачи

    reply = await probe.request("torrents.drive_incomplete",
                                {"series_id": 1}, timeout=5)
    assert reply["driven"] == 1
    assert await _wait(lambda: not _agent_tasks(db_path))  # конвейер прошёл
    assert renaming.calls  # переименование запустилось

    # после конвейера раздача возобновлена (downloading) → повторно не гоним
    reply2 = await probe.request("torrents.drive_incomplete",
                                 {"series_id": 1}, timeout=5)
    assert reply2["driven"] == 0


@pytest.mark.asyncio
async def test_rename_error_becomes_carrier(system):
    bus, qbt, renaming, probe, db_path = system
    renaming.fail = True
    qbt.torrents["h4"] = {"state": "pausedDL", "total_size": 9,
                          "progress": 0.0}
    sub = bus.subscribe("series.status.contribution")

    await probe.request("torrents.register", {
        "series_id": 1, "torrent": _torrent("tid4"), "qb_hash": "h4",
        "link_type": "file", "replaces": None}, timeout=5)
    assert await _wait(
        lambda: _agent_tasks(db_path).get("h4", {}).get("stage") == "error")

    # повторная регистрация (следующий скан) перезапускает задачу
    renaming.fail = False
    reply = await probe.request("torrents.register", {
        "series_id": 1, "torrent": _torrent("tid4"), "qb_hash": "h4",
        "link_type": "file", "replaces": None}, timeout=5)
    assert reply == {"existed": False}
    assert await _wait(lambda: not _agent_tasks(db_path))
    # свёртка по дороге побывала в error
    flags_seen = []
    while not sub.queue.empty():
        env = sub.queue.get_nowait()
        if env.payload["source"] == "torrents":
            flags_seen.append(env.payload["flags"]["error"])
    assert True in flags_seen and flags_seen[-1] is False


@pytest.mark.asyncio
async def test_monitor_progress_and_contribution(system):
    bus, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1, 't5', 'l', 1, 'h5')")
        conn.commit()
    qbt.torrents["h5"] = {"state": "downloading", "progress": 0.4,
                          "dlspeed": 1000, "eta": 60}
    sub = bus.subscribe("series.status.contribution")

    def _progress_row():
        with sqlite3.connect(db_path) as conn:
            return conn.execute("SELECT progress, status FROM torrent_progress "
                                "WHERE qb_hash='h5'").fetchone()

    assert await _wait(lambda: _progress_row() == (40, "downloading"))
    qbt.torrents["h5"].update(progress=1.0, state="uploading")
    assert await _wait(lambda: _progress_row() == (100, "uploading"))

    # финальная свёртка публикуется следующим тактом монитора — ждём её
    flags = await asyncio.wait_for(_flags_until(sub, lambda each: each["ready"]), 3)
    assert flags["ready"] is True and flags["downloading"] is False


@pytest.mark.asyncio
async def test_progress_lives_in_the_table_of_torrents_and_downloads_never_sees_it(system):
    """One row per infohash in torrent_progress, announced to the interface."""
    bus, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, is_active, qb_hash) "
                     "VALUES (1, 't5', 'l', 1, 'h5')")
        conn.commit()
    qbt.torrents["h5"] = {"state": "downloading", "progress": 0.4, "dlspeed": 1000, "eta": 60}
    sub = bus.subscribe("torrents.progress.changed")

    announced = (await asyncio.wait_for(sub.queue.get(), 3)).payload["tasks"]
    while not announced:  # the first tick may come before the row is written
        announced = (await asyncio.wait_for(sub.queue.get(), 3)).payload["tasks"]
    qbt.torrents["h5"].update(progress=0.7, state="uploading")
    assert await _wait(lambda: _progress_count(db_path) == 1 and _progress_value(db_path, "h5") == 70)

    assert announced[0]["qb_hash"] == "h5" and announced[0]["series_id"] == 1
    assert announced[0]["status"] == "downloading" and announced[0]["dlspeed"] == 1000
    assert announced[0]["series_name"] == "Т"
    assert not {"task_key", "task_type", "video_url", "save_path"} & set(announced[0])
    queue = await probe.request("torrents.db.progress.list", {}, timeout=5)
    assert [t["qb_hash"] for t in queue["tasks"]] == ["h5"]


async def _flags_until(sub, condition):
    """The flags of the next contribution of the module that satisfy `condition`: the monitor publishes them."""
    while True:
        env = await sub.queue.get()
        if env.payload["source"] == "torrents" and condition(env.payload["flags"]):
            return env.payload["flags"]


def _progress_count(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM torrent_progress").fetchone()[0]


def _progress_value(db_path, qb_hash):
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT progress FROM torrent_progress WHERE qb_hash=?", (qb_hash,)).fetchone()
    return row[0] if row else None


@pytest.mark.asyncio
async def test_progress_of_a_torrent_that_left_qbit_goes_and_a_deleted_series_takes_the_rest(system):
    bus, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, is_active, qb_hash) "
                     "VALUES (1, 'ta', 'l', 1, 'ha'), (1, 'tb', 'l', 1, 'hb')")
        conn.commit()
    qbt.torrents["ha"] = {"state": "downloading", "progress": 0.2}
    qbt.torrents["hb"] = {"state": "downloading", "progress": 0.3}
    assert await _wait(lambda: _progress_count(db_path) == 2)

    qbt.torrents.pop("hb")
    assert await _wait(lambda: _progress_count(db_path) == 1 and _progress_value(db_path, "ha") == 20)

    probe.publish_event("series.deleted", {"series_id": 1, "delete_from_qb": False})
    assert await _wait(lambda: _progress_count(db_path) == 0)


@pytest.mark.asyncio
async def test_sim_P3_P4_P6_states_map_to_flags(system):
    """P3/P4/P6: stalledDL→idle, queuedDL→queued, missingFiles→error
    (а не «загрузка»). Свёртка _contribute по картам _IDLE/_QUEUED/_ERROR."""
    bus, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'tst','l',1,'hst')")
        conn.commit()
    sub = bus.subscribe("series.status.contribution")

    async def _has_flag(state, flag):
        qbt.torrents["hst"] = {"state": state, "progress": 0.5,
                               "dlspeed": 0, "eta": 0}
        try:
            await asyncio.wait_for(_flags_until(sub, lambda each: each.get(flag)), 3)
        except asyncio.TimeoutError:
            return False
        return True

    assert await _has_flag("stalledDL", "idle")
    assert await _has_flag("queuedDL", "queued")
    assert await _has_flag("missingFiles", "error")


@pytest.mark.asyncio
async def test_qbit_error_state_is_error_not_downloading(system):
    """Находка 43: qBit-state error → флаг error (не downloading) +
    текст ошибки в torrent_progress для модалки."""
    bus, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1, 't9', 'l', 1, 'h9')")
        conn.commit()
    # неверный путь → qBit держит раздачу в error, прогресс 0
    qbt.torrents["h9"] = {"state": "error", "progress": 0.0,
                          "dlspeed": 0, "eta": 0}
    sub = bus.subscribe("series.status.contribution")

    flags = await asyncio.wait_for(_flags_until(sub, lambda each: each["error"]), 3)
    assert flags["error"] is True
    assert flags["downloading"] is False
    with sqlite3.connect(db_path) as conn:
        status, msg = conn.execute(
            "SELECT status, error_message FROM torrent_progress "
            "WHERE qb_hash='h9'").fetchone()
    assert status == "error"
    assert parse(msg)["code"] == "qbt_error"


@pytest.mark.asyncio
async def test_fs_verify_marks_missing_and_rechecks(system, tmp_path):
    _, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE series SET save_path=? WHERE id=1",
                     (str(tmp_path),))
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1, 't6', 'l', 1, 'h6')")
        cur = conn.execute("SELECT id FROM torrents WHERE torrent_id='t6'")
        conn.execute("INSERT INTO torrent_files (torrent_db_id, "
                     "original_path, renamed_path, status) VALUES "
                     "(?, 'orig.mkv', 'Season 01/s01e01.mkv', 'renamed')",
                     (cur.fetchone()[0],))
        conn.execute("INSERT INTO torrent_progress (qb_hash, series_id, "
                     "status, progress) VALUES ('h6', 1, 'uploading', 100)")
        conn.commit()
    qbt.torrents["h6"] = {"state": "uploading", "total_size": 9,
                          "progress": 1.0}

    reply = await probe.request("torrents.fs.verify", {"series_id": 1},
                                timeout=5)
    assert reply == {"missing": 1, "recheck_started": 1}
    # recheck-задача дойдёт до конца, файл будет «восстановлен» qBit'ом
    assert await _wait(lambda: ("recheck", ("h6",)) in qbt.calls)


@pytest.mark.asyncio
async def test_sim_C7_new_release_while_old_in_pipeline(system):
    """C7: новый релиз пришёл, пока старый ещё в конвейере. Старый
    заменяется (удалён из qB), его задача снимается без зависания, новый
    доходит до конца."""
    _, qbt, renaming, probe, db_path = system
    # старый — magnet без метаданных: припаркован в конвейере (в _pipe)
    qbt.torrents["hOld"] = {"state": "stoppedDL", "total_size": 0,
                            "progress": 0.0}
    await probe.request("torrents.register", {
        "series_id": 1, "torrent": _torrent("old"), "qb_hash": "hOld",
        "link_type": "magnet", "replaces": None}, timeout=5)
    assert await _wait(lambda: ("resume", ("hOld",)) in qbt.calls)  # в конвейере
    with sqlite3.connect(db_path) as conn:
        old_id = conn.execute("SELECT id FROM torrents WHERE "
                              "torrent_id='old'").fetchone()[0]

    # приходит НОВЫЙ релиз (другой infohash), заменяет старый
    qbt.torrents["hNew"] = {"state": "pausedDL", "total_size": 99,
                            "progress": 0.0}
    await probe.request("torrents.register", {
        "series_id": 1, "torrent": _torrent("new"), "qb_hash": "hNew",
        "link_type": "file",
        "replaces": {"torrent_id": "old", "qb_hash": "hOld",
                     "db_id": old_id}}, timeout=5)

    assert ("delete", ("hOld",), False) in qbt.calls  # старый убран из qB
    # новый доходит до конца, старая задача не зависла
    assert await _wait(lambda: not _agent_tasks(db_path))
    with sqlite3.connect(db_path) as conn:
        rows = dict(conn.execute(
            "SELECT torrent_id, is_active FROM torrents").fetchall())
    assert rows["old"] == 0 and rows["new"] == 1  # старый заменён, новый активен


@pytest.mark.asyncio
async def test_sim_L3_pipeline_task_recovered_on_start(db_path):
    """L2/L3: рестарт посреди конвейера — незавершённая задача из
    agent_tasks восстанавливается в работу и доводится (торрент жив в qB)."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'tl3','l',1,'hl3')")
        conn.execute("INSERT INTO agent_tasks (torrent_hash, series_id, "
                     "torrent_id, old_torrent_id, stage) VALUES "
                     "('hl3',1,'tl3','None','awaiting_pause_before_rename')")
        conn.commit()
    bus = Bus()
    db = Database(db_path)
    qbt = FakeQbt()
    qbt.torrents = {"hl3": {"state": "pausedDL", "total_size": 9,
                            "progress": 0.0}}
    renaming = FakeRenaming(bus)
    torrents = TorrentsModule(bus, db, qbt=qbt, pipeline_poll=0.02,
                              monitor_active=0.05, monitor_idle=0.2)
    runner = Runner(bus, [renaming, CatalogModule(bus, db), torrents,
                          Probe(bus)])
    await runner.start()
    _watch(bus, runner)
    try:
        assert await _wait(lambda: not _agent_tasks(db_path))  # доведена
        assert renaming.calls  # переименование отработало после рестарта
    finally:
        await runner.stop()
        _watch()


@pytest.mark.asyncio
async def test_reconcile_drops_orphan_torrent_on_start(db_path):
    """Crash-tolerance (reconcile при старте): торрент есть в БД и
    активен, но отсутствует в реальном qBit (удалён вручную) → запись
    сбрасывается (is_active=0, файлы удалены). Торрент, который в qBit
    есть, не трогается."""
    with sqlite3.connect(db_path) as conn:
        # orphan — в qBit его не будет
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1, 'torph', 'l', 1, 'horphan')")
        tid = conn.execute("SELECT id FROM torrents WHERE "
                           "torrent_id='torph'").fetchone()[0]
        conn.execute("INSERT INTO torrent_files (torrent_db_id, original_path, "
                     "status) VALUES (?, 'a.mkv', 'renamed')", (tid,))
        # live — будет присутствовать в qBit
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1, 'tlive', 'l', 1, 'hlive')")
        conn.commit()

    bus = Bus()
    db = Database(db_path)
    qbt = FakeQbt()
    qbt.torrents = {"hlive": {"state": "pausedUP", "progress": 1.0}}
    torrents = TorrentsModule(bus, db, qbt=qbt, pipeline_poll=0.02,
                              monitor_active=0.05, monitor_idle=0.2)
    runner = Runner(bus, [CatalogModule(bus, db), torrents, Probe(bus)])
    await runner.start()
    try:
        with sqlite3.connect(db_path) as conn:
            orphan = conn.execute("SELECT is_active FROM torrents WHERE "
                                  "torrent_id='torph'").fetchone()[0]
            files = conn.execute("SELECT count(*) FROM torrent_files WHERE "
                                 "torrent_db_id=?", (tid,)).fetchone()[0]
            live = conn.execute("SELECT is_active FROM torrents WHERE "
                                "torrent_id='tlive'").fetchone()[0]
        assert orphan == 0, "осиротевший торрент должен быть деактивирован"
        assert files == 0, "записи о файлах осиротевшего торрента удалены"
        assert live == 1, "присутствующий в qBit торрент не трогается"
    finally:
        await runner.stop()


# --- выборочная загрузка ---------------------------------------------------------------

def _qb_file(name, index, priority=1):
    return {"name": name, "index": index, "priority": priority,
            "size": 100, "progress": 0}


def _seed_files(probe, qb_hash, count=2, season=1):
    """Состав раздачи в БД: серии season сезона, по одному файлу."""
    return probe.request("torrents.db.files.upsert", {
        "qb_hash": qb_hash, "files": [
            {"original_path": f"raw{n}.mkv",
             "renamed_path": f"Season 0{season}/s0{season}e0{n}.mkv",
             "status": "renamed", "qb_file_index": n - 1,
             "extracted_metadata": {"season": season, "episode": n}}
            for n in range(1, count + 1)]}, timeout=5)


async def _memory(probe, series_id, *, seasons=None, episodes=None):
    fields = {}
    if seasons is not None:
        fields["ignored_seasons"] = json.dumps(seasons)
    if episodes is not None:
        fields["ignored_episodes"] = json.dumps(episodes)
    return await probe.request("catalog.series.update", {
        "series_id": series_id, "fields": fields}, timeout=5)


@pytest.mark.asyncio
async def test_prepare_builds_composition_without_pipeline(system):
    """«Получить данные»: состав в БД, задачи конвейера нет, пауза цела."""
    _, qbt, renaming, probe, db_path = system
    qbt.torrents["hp"] = {"state": "pausedDL", "total_size": 100,
                          "progress": 0.0}
    qbt.files["hp"] = [_qb_file("raw1.mkv", 0), _qb_file("cover.jpg", 1)]

    reply = await probe.request("torrents.register", {
        "series_id": 1, "torrent": _torrent("tprep"), "qb_hash": "hp",
        "link_type": "file", "replaces": None,
        "start_pipeline": False}, timeout=5)

    assert reply["prepared"] is True
    assert renaming.calls[0]["dry_run"] is True
    assert not _agent_tasks(db_path), "конвейер не должен запускаться"
    assert qbt.torrents["hp"]["state"] == "pausedDL"
    assert ("resume", ("hp",)) not in qbt.calls


@pytest.mark.asyncio
async def test_unrecognized_files_disabled_in_qbit(system):
    """Файл без номера серии не качается: приоритет 0 сразу."""
    _, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'tu','l',1,'hu')")
        conn.commit()
    qbt.torrents["hu"] = {"state": "pausedDL", "progress": 0.0}
    qbt.files["hu"] = [_qb_file("raw1.mkv", 0), _qb_file("extras.mkv", 1),
                       _qb_file("cover.jpg", 2)]

    await probe.request("torrents.db.files.upsert", {
        "qb_hash": "hu", "files": [
            {"original_path": "raw1.mkv", "renamed_path": None,
             "status": "pending_rename", "qb_file_index": 0,
             "extracted_metadata": {"season": 1, "episode": 1}},
            {"original_path": "extras.mkv", "renamed_path": None,
             "status": "pending_rename", "qb_file_index": 1,
             "extracted_metadata": {"season": 1}}]}, timeout=5)

    prios = {f["name"]: f["priority"] for f in qbt.files["hu"]}
    assert prios == {"raw1.mkv": 1, "extras.mkv": 0, "cover.jpg": 0}


@pytest.mark.asyncio
async def test_memory_change_applies_to_live_torrent(system):
    """Отключил серию — qBit перестаёт её качать."""
    _, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'tm','l',1,'hm')")
        conn.commit()
    qbt.torrents["hm"] = {"state": "downloading", "progress": 0.1}
    qbt.files["hm"] = [_qb_file("raw1.mkv", 0), _qb_file("raw2.mkv", 1)]
    await _seed_files(probe, "hm")

    await _memory(probe, 1, episodes={"1": [2]})

    assert await _wait(lambda: qbt.files["hm"][1]["priority"] == 0)
    assert qbt.files["hm"][0]["priority"] == 1


@pytest.mark.asyncio
async def test_ignored_season_applies_to_whole_season(system):
    _, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'ts','l',1,'hs')")
        conn.commit()
    qbt.torrents["hs"] = {"state": "downloading", "progress": 0.1}
    qbt.files["hs"] = [_qb_file("raw1.mkv", 0), _qb_file("raw2.mkv", 1)]
    await _seed_files(probe, "hs")

    await _memory(probe, 1, seasons=[1])

    assert await _wait(lambda: all(f["priority"] == 0
                                   for f in qbt.files["hs"]))
    seasons = await probe.request("torrents.seasons", {"series_id": 1},
                                  timeout=5)
    assert seasons == {"seasons": []}, "выключенный сезон уходит из агрегата"


@pytest.mark.asyncio
async def test_selection_survives_release_replacement(system):
    """Главное требование: выбор помнится по номерам серий, а не по файлам
    релиза — новая раздача стартует уже с нужными приоритетами."""
    _, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'told','l',1,'hold')")
        old_id = conn.execute("SELECT id FROM torrents WHERE "
                              "torrent_id='told'").fetchone()[0]
        conn.commit()
    qbt.torrents["hold"] = {"state": "pausedDL", "progress": 0.0}
    qbt.files["hold"] = [_qb_file("raw1.mkv", 0), _qb_file("raw2.mkv", 1)]
    await _seed_files(probe, "hold")
    await _memory(probe, 1, episodes={"1": [2]})
    assert await _wait(lambda: qbt.files["hold"][1]["priority"] == 0)

    qbt.torrents["hnew"] = {"state": "pausedDL", "total_size": 100,
                            "progress": 0.0}
    qbt.files["hnew"] = [_qb_file("new1.mkv", 0), _qb_file("new2.mkv", 1)]
    await probe.request("torrents.register", {
        "series_id": 1, "torrent": _torrent("tnew"), "qb_hash": "hnew",
        "link_type": "file", "start_pipeline": False,
        "replaces": {"torrent_id": "told", "qb_hash": "hold",
                     "db_id": old_id}}, timeout=5)
    await probe.request("torrents.db.files.upsert", {
        "qb_hash": "hnew", "files": [
            {"original_path": "new1.mkv", "renamed_path": None,
             "status": "pending_rename", "qb_file_index": 0,
             "extracted_metadata": {"season": 1, "episode": 1}},
            {"original_path": "new2.mkv", "renamed_path": None,
             "status": "pending_rename", "qb_file_index": 1,
             "extracted_metadata": {"season": 1, "episode": 2}}]}, timeout=5)

    assert qbt.files["hnew"][0]["priority"] == 1
    assert qbt.files["hnew"][1]["priority"] == 0, \
        "серия, отключённая до замены раздачи, обязана остаться отключённой"


@pytest.mark.asyncio
async def test_reenable_resumes_paused_torrent(system):
    """Вернули серию в план — раздача снимается с паузы, иначе не докачает."""
    _, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'tr','l',1,'hr')")
        conn.commit()
    qbt.torrents["hr"] = {"state": "pausedDL", "progress": 0.3}
    qbt.files["hr"] = [_qb_file("raw1.mkv", 0), _qb_file("raw2.mkv", 1)]
    await _seed_files(probe, "hr")
    await _memory(probe, 1, episodes={"1": [2]})
    assert await _wait(lambda: qbt.files["hr"][1]["priority"] == 0)

    await _memory(probe, 1, episodes={})

    assert await _wait(lambda: qbt.files["hr"][1]["priority"] == 1)
    assert await _wait(lambda: ("resume", ("hr",)) in qbt.calls)


@pytest.mark.asyncio
async def test_no_resume_while_task_in_pipeline(system):
    """Инвариант ядра: раздача в конвейере не резюмится выбором серий."""
    _, qbt, _, probe, db_path = system
    qbt.torrents["hq"] = {"state": "pausedDL", "total_size": 100,
                          "progress": 0.0}
    qbt.files["hq"] = [_qb_file("raw1.mkv", 0), _qb_file("raw2.mkv", 1)]
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'tq','l',1,'hq')")
        conn.execute("INSERT INTO agent_tasks (torrent_hash, series_id, "
                     "torrent_id, old_torrent_id, stage) VALUES "
                     "('hq', 1, 'tq', 'None', 'renaming')")
        conn.commit()
    await _seed_files(probe, "hq")
    await _memory(probe, 1, episodes={"1": [1, 2]})
    assert await _wait(lambda: all(f["priority"] == 0
                                   for f in qbt.files["hq"]))
    qbt.calls.clear()

    await _memory(probe, 1, episodes={})

    assert await _wait(lambda: qbt.files["hq"][0]["priority"] == 1)
    await settle(*_watched)  # quiet: the handler of the change has ended, so a resume it might make is made
    assert ("resume", ("hq",)) not in qbt.calls


@pytest.mark.asyncio
async def test_fs_verify_skips_excluded_files(system, tmp_path):
    """Исключённого файла на диске нет по замыслу — recheck не заводим."""
    _, qbt, _, probe, db_path = system
    save_path = tmp_path / "media"
    save_path.mkdir()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE series SET save_path=? WHERE id=1",
                     (str(save_path),))
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'tv','l',1,'hv')")
        conn.execute("INSERT INTO torrent_progress (qb_hash, series_id, "
                     "status, progress) VALUES ('hv', 1, 'pausedUP', 100)")
        conn.commit()
    qbt.torrents["hv"] = {"state": "pausedUP", "progress": 1.0}
    qbt.files["hv"] = [_qb_file("raw1.mkv", 0), _qb_file("raw2.mkv", 1)]
    await _seed_files(probe, "hv")
    await _memory(probe, 1, episodes={"1": [1, 2]})
    assert await _wait(lambda: all(f["priority"] == 0
                                   for f in qbt.files["hv"]))

    reply = await probe.request("torrents.fs.verify", {"series_id": 1},
                                timeout=10)

    assert reply == {"missing": 0, "recheck_started": 0}
    assert not _agent_tasks(db_path)


@pytest.mark.asyncio
async def test_fully_excluded_release_is_not_ready(system):
    """qBit по пустому набору отдаёт 100% — «готов» это не значит."""
    bus, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1,'tf','l',1,'hf')")
        conn.execute("INSERT INTO torrent_progress (qb_hash, series_id, "
                     "status, progress) VALUES ('hf', 1, 'pausedUP', 100)")
        conn.commit()
    qbt.torrents["hf"] = {"state": "pausedUP", "progress": 1.0}
    qbt.files["hf"] = [_qb_file("raw1.mkv", 0)]
    await _seed_files(probe, "hf", count=1)
    sub = bus.subscribe("series.status.contribution")

    await _memory(probe, 1, seasons=[1])

    env = await asyncio.wait_for(sub.queue.get(), 3)
    assert env.payload["flags"]["ready"] is False


# --- composition: the profile of the torrent source goes to the constructor ------------------

class FakeRules(BaseModule):
    name = "fake_rules"

    def __init__(self, bus):
        self.asked: list[dict] = []
        super().__init__(bus)

    def register(self):
        self.handle("rules.format_torrent_file", self.on_format)

    async def on_format(self, env):
        self.asked.append(env.payload)
        return {"filename": "Season 01/T e01.mkv", "extracted": {"episode": 1}}


@pytest.fixture
async def composition_system(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO parser_profiles (id, name) VALUES (7, 'профиль')")
        conn.execute("INSERT INTO torrents (id, series_id, torrent_id, link, is_active, qb_hash) "
                     "VALUES (10, 1, 'tc', 'l', 1, 'hc')")
        conn.execute("INSERT INTO torrent_files (torrent_db_id, original_path, status, extracted_metadata) "
                     "VALUES (10, 'raw/01.mkv', 'renamed', '{\"episode\": 1}')")
        conn.execute("INSERT INTO torrent_progress (qb_hash, series_id, status, progress) "
                     "VALUES ('hc', 1, 'pausedUP', 100)")
        conn.commit()
    bus = Bus()
    db = Database(db_path)
    rules = FakeRules(bus)
    qbt = FakeQbt()  # the release is there, or the start of the module drops it as lost
    qbt.torrents["hc"] = {"state": "pausedUP", "progress": 1.0}
    qbt.files["hc"] = [_qb_file("raw/01.mkv", 0)]
    torrents = TorrentsModule(bus, db, qbt=qbt, pipeline_poll=0.02, monitor_active=0.05, monitor_idle=0.2)
    runner = Runner(bus, [rules, CatalogModule(bus, db), torrents, Probe(bus)])
    await runner.start()
    yield rules, runner.modules[-1], db_path
    await runner.stop()


@pytest.mark.asyncio
async def test_composition_asks_the_constructor_with_the_profile_of_the_torrent_source(composition_system):
    rules, probe, db_path = composition_system
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE series_sources SET parser_profile_id=7 WHERE series_id=1")
        conn.commit()

    plan = await probe.request("torrents.composition", {"series_id": 1}, timeout=10)

    assert [asked["profile_id"] for asked in rules.asked] == [7]
    assert plan[0]["renamed_path_preview"] == "Season 01/T e01.mkv"


@pytest.mark.asyncio
async def test_composition_without_a_profile_keeps_the_stored_names_and_asks_nobody(composition_system):
    rules, probe, _ = composition_system

    plan = await probe.request("torrents.composition", {"series_id": 1}, timeout=10)

    assert rules.asked == []
    assert plan[0]["renamed_path_preview"] == "raw/01.mkv"
