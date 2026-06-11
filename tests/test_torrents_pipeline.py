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
        "replaces": {"torrent_id": "old", "qb_hash": "hOld"}}, timeout=5)
    assert await _wait(lambda: not _agent_tasks(db_path))

    with sqlite3.connect(db_path) as conn:
        active = conn.execute("SELECT torrent_id, is_active FROM torrents "
                              "ORDER BY torrent_id").fetchall()
        files = conn.execute("SELECT COUNT(*) FROM torrent_files "
                             "WHERE torrent_db_id=?", (old_id,)).fetchone()
    assert ("old", 0) in active and ("tid3", 1) in active
    assert files[0] == 0  # записи о файлах старой раздачи удалены
    assert ("delete", ("hOld",), False) in qbt.calls


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

    flags = None
    while not sub.queue.empty():
        env = sub.queue.get_nowait()
        if env.payload["source"] == "torrents":
            flags = env.payload["flags"]
    assert flags["ready"] is True and flags["downloading"] is False


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
