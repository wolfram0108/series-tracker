"""Репо-тесты многосезонной фичи TMDB: реальные сезоны из нейминга
(torrents.seasons / scan.seasons) и счётчик «скачано» без спешелов (сезон 0).
Сам агрегатор (metadata.seasons.recompute) — в test_settings_metadata_library.
"""
import sqlite3
import subprocess
import sys

import pytest

from core.db import Database
from modules.downloads.repository import DownloadsRepository
from modules.scan.repository import ScanRepository
from modules.torrents.repository import TorrentsRepository


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "t.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{path}",
                        "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode) VALUES "
            "(1, 'u', 'T', 'T', 'kinozal', '/m', 'w', 'torrent', 0, 'search')")
        # активная раздача + неактивная (её сезоны не считаются)
        conn.execute("INSERT INTO torrents (id, series_id, torrent_id, link, "
                     "is_active) VALUES (10,1,'t1','l1',1),(11,1,'t2','l2',0)")
        conn.executemany(
            "INSERT INTO torrent_files (torrent_db_id, original_path, status, "
            "extracted_metadata) VALUES (?,?,?,?)",
            [(10, "a", "renamed", '{"season":9,"episode":1}'),
             (10, "b", "renamed", '{"season":9,"episode":2}'),
             (10, "c", "renamed", '{"season":8,"episode":1}'),
             (10, "d", "renamed", '{"season":0,"episode":1}'),   # спешел
             (10, "e", "renamed", '{"episode":1}'),               # без сезона
             (11, "f", "renamed", '{"season":7,"episode":1}')])   # неактивна
        for uid, ep, se in [("u1", 1, 1), ("u2", 2, 1), ("u3", 1, 2),
                            ("u0", 9, 0)]:  # u0 — спешел (сезон 0)
            conn.execute(
                "INSERT INTO media_items (series_id, unique_id, source_url, "
                "publication_date, plan_status, status, is_ignored_by_user, "
                "is_available, slicing_status, final_filename, episode_start, "
                "season) VALUES (1,?,?,?,?,?,0,1,'none',?,?,?)",
                (uid, "x", "2026-01-01 00:00:00", "candidate", "completed",
                 f"{uid}.mp4", ep, se))
        conn.commit()
    return str(path)


@pytest.mark.asyncio
async def test_torrents_seasons_distinct_active_no_null(db_path):
    repo = TorrentsRepository(Database(db_path))
    # активные раздачи, distinct, NULL отброшен, неактивная (сезон 7) мимо
    assert sorted(await repo.seasons_for_series(1)) == [0, 8, 9]


@pytest.mark.asyncio
async def test_scan_seasons_distinct(db_path):
    repo = ScanRepository(Database(db_path))
    assert sorted(await repo.seasons_for_series(1)) == [0, 1, 2]


@pytest.mark.asyncio
async def test_torrent_downloaded_count_excludes_specials(db_path):
    repo = TorrentsRepository(Database(db_path))
    # файлы активной раздачи: s9×2, s8×1, s0×1 (спешел), без сезона×1.
    # спешел не считается; без сезона (NULL) считается → 4
    assert (await repo.downloaded_counts())[1] == 4
    assert await repo.downloaded_count_for_series(1) == 4


@pytest.mark.asyncio
async def test_vk_downloaded_count_excludes_specials(db_path):
    scan = ScanRepository(Database(db_path))
    downloads = DownloadsRepository(Database(db_path))
    # media_items с final_filename: s1×2, s2×1, s0×1 (спешел) → спешел мимо → 3
    assert (await scan.downloaded_counts())[1] == 3
    assert await downloads.downloaded_count(1) == 3
