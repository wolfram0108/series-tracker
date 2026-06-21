"""Сквозные тесты оркестратора scan: catalog настоящий, остальные
соседи — фейк (контракты будущих модулей фиксируются здесь).
"""
import asyncio
import base64
import json
import sqlite3
import subprocess
import sys

import pytest

from core import BaseModule, Bus, BusRequestError, Runner
from core.db import Database
from modules.catalog import CatalogModule
from modules.scan import ScanModule
from core import ids


class FakeBackend(BaseModule):
    """sources + torrents + renaming + settings + rules одним фейком."""
    name = "fake_backend"

    def __init__(self, bus):
        self.service = "kinozal"
        self.releases: list[dict] = []
        self.vk_videos: list[dict] = []
        self.rules_results: list[dict] = []
        self.active: list[dict] = []
        self.settings = {"scan_interval_minutes": "60"}
        self.registered: list[dict] = []
        self.register_existed = False  # ПУНКТ 3: тот же infohash → existed
        self.fs_verify_calls = 0       # R1: скан должен сверять файлы
        self.drive_calls = 0           # P10: скан гонит застрявших
        self.fs_sync_calls = 0         # R4: VK-скан должен сверять диск
        self.verify_calls: list[str] = []  # VF2: скан сверяет нарезку
        self.added: list[dict] = []
        self.deactivate_calls = 0
        self.reprocess_calls = 0
        self.fail_adds = False
        self.hold_reprocess: asyncio.Event | None = None
        super().__init__(bus)

    def register(self):
        h = self.handle
        h("renaming.reprocess", self.on_reprocess)
        h("sources.parse", self.on_parse)
        h("sources.torrent_file.get", self.on_file)
        h("sources.vk.scan", self.on_vk)
        h("downloads.fs.sync", self.on_fs_sync_vk)
        h("slicing.verify", self.on_slicing_verify)
        h("rules.apply", self.on_rules)
        h("torrents.db.active", self.on_active)
        h("torrents.db.deactivate_all", self.on_deactivate)
        h("torrents.add", self.on_add)
        h("torrents.fs.verify", self.on_fs_verify)
        h("torrents.drive_incomplete", self.on_drive_incomplete)
        h("torrents.register", self.on_register)
        h("torrents.queue.get", self.on_queue)
        h("settings.value.get", self.on_get)
        h("settings.value.set", self.on_set)

    async def on_reprocess(self, env):
        self.reprocess_calls += 1
        if self.hold_reprocess:
            await self.hold_reprocess.wait()
        return {"ok": True}

    async def on_parse(self, env):
        return {"service": self.service, "title": {"ru": "Т", "en": None},
                "releases": self.releases}

    async def on_file(self, env):
        content = f"torrent:{env.payload['torrent_id']}".encode()
        return {"content_b64": base64.b64encode(content).decode(),
                "from_cache": False}

    async def on_vk(self, env):
        return {"videos": self.vk_videos}

    async def on_fs_sync_vk(self, env):
        self.fs_sync_calls += 1
        return {"adopted": 0, "lost": 0}

    async def on_slicing_verify(self, env):
        self.verify_calls.append(env.payload["unique_id"])
        return {"status": "completed"}

    async def on_rules(self, env):
        return {"results": self.rules_results, "invalid_rules": []}

    async def on_active(self, env):
        return self.active

    async def on_deactivate(self, env):
        self.deactivate_calls += 1
        self.active = []
        return {"ok": True}

    async def on_add(self, env):
        if self.fail_adds:
            raise RuntimeError("qBittorrent недоступен (имитация)")
        self.added.append(env.payload)
        if "content_b64" in env.payload:
            content = base64.b64decode(env.payload["content_b64"]).decode()
            tid = content.split(":", 1)[1]  # "torrent:<id>" из on_file
            return {"hash": f"hash-{tid}", "link_type": "file",
                    "existed": False}
        return {"hash": "hash-magnet", "link_type": "magnet",
                "existed": False}

    async def on_fs_verify(self, env):
        self.fs_verify_calls += 1
        return {"missing": 0, "recheck_started": 0}

    async def on_drive_incomplete(self, env):
        self.drive_calls += 1
        return {"driven": 0}

    async def on_register(self, env):
        self.registered.append(env.payload)
        return {"ok": True, "existed": self.register_existed}

    async def on_queue(self, env):
        return {"count": 0}

    async def on_get(self, env):
        return {"key": env.payload["key"],
                "value": self.settings.get(env.payload["key"])}

    async def on_set(self, env):
        self.settings[env.payload["key"]] = env.payload["value"]
        return env.payload


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
        conn.execute("INSERT INTO parser_profiles (id, name) "
                     "VALUES (1, 'профиль')")
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode, "
            "parser_profile_id, quality) VALUES "
            "(1, 'https://kinozal.me/details.php?id=7', 'Т1', 'T1', "
            "'kinozal.me', '/media/t1', 'waiting', 'torrent', 0, 'search', "
            "1, NULL), "
            "(2, 'https://vkvideo.ru/@chan|тайтл', 'Т2', 'T2', 'vk', "
            "'/media/t2', 'waiting', 'vk_video', 0, 'get_all', 1, NULL)")
        conn.commit()
    return str(path)


def _system_factory(db_path, **scan_kwargs):
    bus = Bus()
    db = Database(db_path)
    fake = FakeBackend(bus)
    catalog = CatalogModule(bus, db)
    scan = ScanModule(bus, db, scheduler_tick=None, **scan_kwargs)
    probe = Probe(bus)
    runner = Runner(bus, [fake, catalog, scan, probe])
    return bus, fake, scan, probe, runner


@pytest.fixture
async def system(db_path):
    bus, fake, scan, probe, runner = _system_factory(db_path)
    await runner.start()
    yield bus, fake, probe, db_path
    await runner.stop()


def _release(link, date_marker, quality=None, episodes=None):
    return {"link": link, "magnet": None, "date_marker": date_marker,
            "episodes": episodes, "quality": quality}


# --- торрент-ветка -------------------------------------------------------------

@pytest.mark.asyncio
async def test_rolling_replace_single_active(system):
    _, fake, probe, db_path = system
    old_id = ids.torrent_id("https://dl.kinozal.me/download.php?id=1",
                            "01.01.2026 00:00:00")
    fake.active = [{"id": 10, "torrent_id": old_id, "qb_hash": "oldhash",
                    "episodes": None}]
    fake.releases = [_release("https://dl.kinozal.me/download.php?id=1",
                              "05.01.2026 12:00:00")]  # новая дата → новый id

    reply = await probe.request("scan.series.run", {"series_id": 1},
                                timeout=10)
    assert reply["tasks_created"] == 1
    assert fake.reprocess_calls == 1
    assert fake.fs_verify_calls == 1  # R1: скан сверил файлы (F1/F2)
    assert fake.drive_calls == 1      # P10: скан реконсилил застрявших
    reg = fake.registered[0]
    assert reg["replaces"]["qb_hash"] == "oldhash"
    assert reg["link_type"] == "file"
    # журнал подчищен
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM scan_tasks").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_unchanged_release_is_skipped(system):
    _, fake, probe, _ = system
    link, date = "https://dl.kinozal.me/download.php?id=1", "01.01.2026 00:00:00"
    fake.active = [{"id": 10, "torrent_id": ids.torrent_id(link, date),
                    "qb_hash": "h", "episodes": None}]
    fake.releases = [_release(link, date)]  # та же дата → тот же id
    reply = await probe.request("scan.series.run", {"series_id": 1},
                                timeout=10)
    assert reply["tasks_created"] == 0
    assert reply["changed"] is False  # «Обновлений нет»
    assert fake.added == []


@pytest.mark.asyncio
async def test_relist_same_hash_reports_unchanged(system):
    """Дата сменилась, но контент тот же (перевыкладка): торрент добавлен в
    qBit, но register вернул existed=True (ПУНКТ 3) → ничего не создано →
    changed=False, тост «Обновлений нет», а не ложное «завершено»."""
    _, fake, probe, _ = system
    link = "https://dl.kinozal.me/download.php?id=1"
    fake.active = [{"id": 10, "torrent_id": ids.torrent_id(link, "01.01.2026"),
                    "qb_hash": "samehash", "episodes": None}]
    fake.releases = [_release(link, "09.01.2026 12:00:00")]  # новая дата
    fake.register_existed = True  # qBit вернул тот же infohash

    reply = await probe.request("scan.series.run", {"series_id": 1},
                                timeout=10)
    assert fake.added != []          # в qBit добавляли (узнать infohash)
    assert reply["tasks_created"] == 0   # но задач не создано
    assert reply["changed"] is False     # «Обновлений нет»


@pytest.mark.asyncio
async def test_sim_T4_release_vanished_keeps_active(system):
    """T4: релиз пропал с трекера (parse его не вернул и ничего нового нет).
    Активную раздачу НЕ трогаем — она в qB, файлы на диске; ничего не
    заменяем и не добавляем."""
    _, fake, probe, _ = system
    link = "https://dl.kinozal.me/download.php?id=1"
    fake.active = [{"id": 10, "torrent_id": ids.torrent_id(link, "01.01.2026"),
                    "qb_hash": "hkeep", "episodes": None}]
    fake.releases = []  # на трекере больше нет этой раздачи

    reply = await probe.request("scan.series.run", {"series_id": 1}, timeout=10)
    assert reply["tasks_created"] == 0
    assert reply["changed"] is False
    assert fake.registered == []  # ничего не заменяли
    assert fake.added == []       # ничего не добавляли
    assert fake.deactivate_calls == 0  # активную не деактивировали


@pytest.mark.asyncio
async def test_fixed_mode_replaces_by_episodes(system):
    _, fake, probe, _ = system
    fake.service = "astar"
    fake.active = [
        {"id": 1, "torrent_id": "a", "qb_hash": "h1", "episodes": "1-10"},
        {"id": 2, "torrent_id": "b", "qb_hash": "h2", "episodes": "11"},
    ]
    fake.releases = [_release("https://astar.bz/x.torrent",
                              "06.01.2026", episodes="11")]
    await probe.request("scan.series.run", {"series_id": 1}, timeout=10)
    assert fake.registered[0]["replaces"]["qb_hash"] == "h2"


@pytest.mark.asyncio
async def test_quality_filter(system):
    _, fake, probe, db_path = system
    with sqlite3.connect(db_path) as conn:  # имитация данных catalog
        conn.execute("UPDATE series SET quality='1080p;2160p' WHERE id=1")
        conn.commit()
    fake.active = []
    fake.releases = [
        _release("https://dl.kinozal.me/download.php?id=1",
                 "01.01.2026 00:00:00", quality="720p"),
        _release("https://dl.kinozal.me/download.php?id=2",
                 "01.01.2026 00:00:00", quality="1080p"),
    ]
    reply = await probe.request("scan.series.run", {"series_id": 1},
                                timeout=10)
    assert reply["tasks_created"] == 1
    assert fake.registered[0]["torrent"]["quality"] == "1080p"


@pytest.mark.asyncio
async def test_error_gets_carrier_and_reset_by_next_scan(system):
    _, fake, probe, db_path = system
    fake.active = []
    fake.releases = [_release("https://dl.kinozal.me/download.php?id=9",
                              "07.01.2026 10:00:00")]
    fake.fail_adds = True
    sub_status = None

    with pytest.raises(BusRequestError, match="qBittorrent недоступен"):
        await probe.request("scan.series.run", {"series_id": 1}, timeout=10)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT status, results_data FROM scan_tasks "
                           "WHERE series_id=1").fetchone()
    assert row[0] == "error"
    assert "qBittorrent недоступен" in row[1]

    # повторный скан сбрасывает носителя ошибки и завершается успехом
    fake.fail_adds = False
    reply = await probe.request("scan.series.run", {"series_id": 1},
                                timeout=10)
    assert reply["tasks_created"] == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM scan_tasks").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_concurrent_scan_rejected(system):
    _, fake, probe, _ = system
    fake.hold_reprocess = asyncio.Event()
    fake.releases = []
    first = asyncio.create_task(
        probe.request("scan.series.run", {"series_id": 1}, timeout=10))
    await asyncio.sleep(0.05)  # первый скан повис на переобработке
    with pytest.raises(BusRequestError, match="уже запущен"):
        await probe.request("scan.series.run", {"series_id": 1}, timeout=10)
    fake.hold_reprocess.set()
    await first


@pytest.mark.asyncio
async def test_resume_continues_from_journal(db_path):
    """Имитация падения посреди добавления: в журнале 2 пункта, первый
    выполнен. После старта модуль доделывает ТОЛЬКО второй и
    регистрирует оба."""
    items = []
    for i in (1, 2):
        items.append({"site_torrent": {
            "torrent_id": f"tid{i}", "link": f"https://dl.kinozal.me/{i}",
            "magnet": None, "date_time": "01.01.2026 00:00:00",
            "quality": None, "episodes": None}, "old": None})
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO scan_tasks (series_id, status, task_data, "
            "results_data) VALUES (1, 'processing', ?, ?)",
            (json.dumps(items),
             json.dumps({"0": {"hash": "done0", "link_type": "file"}})))
        conn.commit()

    bus, fake, scan, probe, runner = _system_factory(db_path)
    await runner.start()
    try:
        for _ in range(100):
            if len(fake.registered) == 2:
                break
            await asyncio.sleep(0.02)
        assert [r["qb_hash"] for r in fake.registered] == \
            ["done0", "hash-tid2"]
        assert len(fake.added) == 1  # первый пункт не добавлялся повторно
        with sqlite3.connect(db_path) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM scan_tasks").fetchone()[0] == 0
    finally:
        await runner.stop()


# --- VK-ветка ---------------------------------------------------------------------

def _vk_video(vid, title, date="2026-03-17T20:13:16Z", res=1080):
    return {"title": title, "url": f"https://vk.com/video-1_{vid}",
            "publication_date": date, "resolution": res}


@pytest.mark.asyncio
async def test_vk_scan_upserts_plans_and_publishes(system):
    bus, fake, probe, db_path = system
    fake.vk_videos = [_vk_video(1, "Тайтл 1 серия"),
                      _vk_video(2, "Тайтл 1-3 серии"),
                      _vk_video(3, "Трейлер")]
    fake.rules_results = [
        {"excluded": False, "extracted": {"episode": 1}},
        {"excluded": False, "extracted": {"start": 1, "end": 3}},
        {"excluded": True, "extracted": {}},  # исключён правилами
    ]
    sub = bus.subscribe("scan.plan.updated")

    reply = await probe.request("scan.series.run", {"series_id": 2},
                                timeout=10)
    assert reply["candidates"] == {"added": 2, "updated": 0, "deleted": 0,
                                   "kept_phantoms": 0}
    assert reply["changed"] is True  # появились кандидаты → «завершено»
    env = await asyncio.wait_for(sub.queue.get(), 2)
    assert env.payload == {"series_id": 2}

    # повторный скан тем же составом: added/deleted=0 (updated растёт сам по
    # себе — он не «обновление») → changed=False → «Обновлений нет»
    reply2 = await probe.request("scan.series.run", {"series_id": 2},
                                 timeout=10)
    assert reply2["candidates"]["added"] == 0
    assert reply2["candidates"]["deleted"] == 0
    assert reply2["changed"] is False

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = {r["source_title"]: dict(r) for r in conn.execute(
            "SELECT * FROM media_items WHERE series_id=2")}
    assert len(rows) == 2  # трейлер не прошёл
    # компиляция 1-3 покрывает одиночку → план выбирает её
    assert rows["Тайтл 1-3 серии"]["plan_status"] == "in_plan_compilation"
    assert rows["Тайтл 1 серия"]["plan_status"] == "replaced"
    # unique_id — по формуле-констрейнту
    assert rows["Тайтл 1 серия"]["unique_id"] == ids.media_unique_id(
        "https://vk.com/video-1_1", "2026-03-17T20:13:16Z", 2)


@pytest.mark.asyncio
async def test_sim_R4_vk_scan_syncs_disk(system):
    """R4 (ситуации VF1/VF3/VC1): VK-скан ОБЯЗАН сверить диск
    (downloads.fs.sync) — иначе пропавший скачанный .mp4 остаётся
    «completed» навсегда и скан его не перекачивает. Поведение самой
    сверки (lost→pending) доказано в test_downloads_module
    (test_fs_sync_detects_lost_and_adopted); здесь — что скан её ЗОВЁТ,
    симметрично торрент-ветке (fs_verify_calls)."""
    _, fake, probe, _ = system
    fake.vk_videos = [_vk_video(1, "Тайтл 1 серия")]
    fake.rules_results = [{"excluded": False, "extracted": {"episode": 1}}]
    await probe.request("scan.series.run", {"series_id": 2}, timeout=10)
    assert fake.fs_sync_calls == 1
    # торрент-сверки на VK-сериале не зовутся
    assert fake.fs_verify_calls == 0


@pytest.mark.asyncio
async def test_sim_VF2_vk_scan_verifies_sliced_children(system):
    """VF2: обычный VK-скан сверяет нарезанных детей (slicing.verify), не
    только открытие композиции — иначе удалённый sliced-файл незаметен до
    композиции. downloads.fs.sync компиляции пропускает (владелец slicing)."""
    _, fake, probe, db_path = system
    # уже нарезанная компиляция (slicing_status=completed)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO media_items (series_id, unique_id, source_url, "
            "source_title, publication_date, plan_status, status, "
            "slicing_status, episode_start, episode_end, is_ignored_by_user, "
            "is_available) VALUES "
            "(2,'comp1','u','Компиляция 1-3','2026-03-17 20:13:16',"
            "'in_plan_compilation','completed','completed',1,3,0,1)")
        conn.commit()
    fake.vk_videos, fake.rules_results = [], []  # скрейп пуст: фантом не-виргин → остаётся
    await probe.request("scan.series.run", {"series_id": 2}, timeout=10)
    assert "comp1" in fake.verify_calls


@pytest.mark.asyncio
async def test_vk_phantom_rules(system):
    _, fake, probe, db_path = system
    fake.vk_videos = [_vk_video(1, "Тайтл 1 серия"),
                      _vk_video(2, "Тайтл 2 серия")]
    fake.rules_results = [
        {"excluded": False, "extracted": {"episode": 1}},
        {"excluded": False, "extracted": {"episode": 2}},
    ]
    await probe.request("scan.series.run", {"series_id": 2}, timeout=10)

    # вторая серия «скачана» (чужая колонка — имитируем downloads)
    uid2 = ids.media_unique_id("https://vk.com/video-1_2",
                               "2026-03-17T20:13:16Z", 2)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE media_items SET status='completed' "
                     "WHERE unique_id=?", (uid2,))
        conn.commit()

    # следующий скан: оба видео исчезли из выдачи
    fake.vk_videos, fake.rules_results = [], []
    reply = await probe.request("scan.series.run", {"series_id": 2},
                                timeout=10)
    assert reply["candidates"]["deleted"] == 1        # девственный — удалён
    assert reply["candidates"]["kept_phantoms"] == 1  # скачанный — оставлен
    with sqlite3.connect(db_path) as conn:
        left = conn.execute("SELECT unique_id FROM media_items "
                            "WHERE series_id=2").fetchall()
    assert left == [(uid2,)]
