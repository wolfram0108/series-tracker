"""Тесты торрент-конвейера (Р-14): чистая машина стадий + сквозные
сценарии с фейковым qBittorrent (стенд не нужен).
"""
import asyncio
import sqlite3
import subprocess
import sys

import pytest

from core import BaseModule, Bus, Runner
from core.db import Database
from modules.catalog import CatalogModule
from modules.torrents import TorrentsModule
from modules.torrents import pipeline as pl


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
        self.calls: list[tuple] = []
        self._checking: dict[str, int] = {}  # hash -> тиков до конца recheck

    def _log(self, *call):
        self.calls.append(call)

    async def torrents_info(self, hashes=None):
        # проверка recheck завершается сама через пару опросов
        for h in list(self._checking):
            self._checking[h] -= 1
            if self._checking[h] <= 0:
                del self._checking[h]
                self.torrents[h]["state"] = "pausedDL"
        rows = [{"hash": h, **i} for h, i in self.torrents.items()]
        if hashes is not None:
            rows = [r for r in rows if r["hash"] in hashes]
        return rows

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
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{path}",
                        "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode) VALUES "
            "(1, 'http://x', 'Т', 'T', 'kinozal', '/media/t', 'waiting', "
            "'torrent', 0, 'search')")
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
    yield bus, qbt, renaming, runner.modules[-1], db_path
    await runner.stop()


def _agent_tasks(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return {r["torrent_hash"]: dict(r) for r in conn.execute(
            "SELECT * FROM agent_tasks")}


async def _wait(predicate, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


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
            return conn.execute("SELECT progress, status FROM download_tasks "
                                "WHERE task_key='h5'").fetchone()

    assert await _wait(lambda: _progress_row() == (40, "downloading"))
    qbt.torrents["h5"].update(progress=1.0, state="uploading")
    assert await _wait(lambda: _progress_row() == (100, "uploading"))

    # финальная свёртка публикуется следующим тактом монитора — ждём её
    flags = None
    for _ in range(100):
        while not sub.queue.empty():
            env = sub.queue.get_nowait()
            if env.payload["source"] == "torrents":
                flags = env.payload["flags"]
        if flags and flags["ready"]:
            break
        await asyncio.sleep(0.02)
    assert flags["ready"] is True and flags["downloading"] is False


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
        for _ in range(150):
            while not sub.queue.empty():
                env = sub.queue.get_nowait()
                if env.payload["source"] == "torrents" and \
                        env.payload["flags"].get(flag):
                    return True
            await asyncio.sleep(0.02)
        return False

    assert await _has_flag("stalledDL", "idle")
    assert await _has_flag("queuedDL", "queued")
    assert await _has_flag("missingFiles", "error")


@pytest.mark.asyncio
async def test_qbit_error_state_is_error_not_downloading(system):
    """Находка 43: qBit-state error → флаг error (не downloading) +
    текст ошибки в download_tasks для модалки."""
    bus, qbt, _, probe, db_path = system
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO torrents (series_id, torrent_id, link, "
                     "is_active, qb_hash) VALUES (1, 't9', 'l', 1, 'h9')")
        conn.commit()
    # неверный путь → qBit держит раздачу в error, прогресс 0
    qbt.torrents["h9"] = {"state": "error", "progress": 0.0,
                          "dlspeed": 0, "eta": 0}
    sub = bus.subscribe("series.status.contribution")

    flags = None
    for _ in range(100):
        while not sub.queue.empty():
            env = sub.queue.get_nowait()
            if env.payload["source"] == "torrents":
                flags = env.payload["flags"]
        if flags and flags["error"]:
            break
        await asyncio.sleep(0.02)
    assert flags["error"] is True
    assert flags["downloading"] is False
    with sqlite3.connect(db_path) as conn:
        status, msg = conn.execute(
            "SELECT status, error_message FROM download_tasks "
            "WHERE task_key='h9'").fetchone()
    assert status == "error"
    assert "путь сохранения" in msg


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
        conn.execute("INSERT INTO download_tasks (task_key, series_id, "
                     "task_type, status, progress) VALUES "
                     "('h6', 1, 'torrent', 'uploading', 100)")
        conn.commit()
    qbt.torrents["h6"] = {"state": "uploading", "total_size": 9,
                          "progress": 1.0}

    reply = await probe.request("torrents.fs.verify", {"series_id": 1},
                                timeout=5)
    assert reply == {"missing": 1, "recheck_started": 1}
    # recheck-задача дойдёт до конца, файл будет «восстановлен» qBit'ом
    assert await _wait(lambda: ("recheck", ("h6",)) in qbt.calls)


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
    try:
        assert await _wait(lambda: not _agent_tasks(db_path))  # доведена
        assert renaming.calls  # переименование отработало после рестарта
    finally:
        await runner.stop()


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
