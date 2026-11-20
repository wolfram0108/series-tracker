"""Тесты торрент-конвейера (Р-14): чистая машина стадий + сквозные
сценарии с фейковым qBittorrent (стенд не нужен).
"""
import json
import sqlite3

import pytest

from core.bus import Bus
from core.module import BaseModule
from core.runner import Runner
from core.db import Database
from modules.catalog import CatalogModule
from modules.torrents import TorrentsModule
from series_factory import insert_series
from settle import wait_until
from schema_db import migrated


# --- чистая машина -----------------------------------------------------------------

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

    probe.publish_event("series.deleted", {"series_id": 1})
    assert await _wait(lambda: _progress_count(db_path) == 0)


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
