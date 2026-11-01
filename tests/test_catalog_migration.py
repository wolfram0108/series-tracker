"""Migration 0025: sources of a series, proved on data.

The database is brought to revision 0024 and filled with series of every kind the system holds, then migrated up
and back down by the real alembic. What is checked is what the owner would lose: no series, no address, no choice
and no id may go missing, and nothing that points at `series` may stop pointing at it.
"""
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

KEPT = ["id", "name", "name_en", "site", "save_path", "season", "quality", "state", "last_scan_time",
        "auto_scan_enabled", "quality_override", "resolution_override", "ignored_seasons", "ignored_episodes"]

# id -> (url, source_type, parser_profile_id, vk_search_mode, vk_quality_priority)
OLD_SERIES = {
    1: ("https://kinozal.tv/details.php?id=7", "torrent", 1, "search", None),
    2: ("https://vkvideo.ru/@chan|тайтл", "vk_video", 1, "get_all", '["1080","720"]'),
    3: ("https://1.ruchime.org/release/a?vibix=2&off=vk", "web_video", 1, "search", None),
    4: ("https://dunhuatv.ru/title/x?player=cvh&voice=AniStar&kind=Многоголосый", "web_video", 1, "search", None),
    5: ("https://1.ruchime.org/release/b?off=vibix,vk", "web_video", None, "search", None),
    6: ("https://1.ruchime.org/release/c?vibix=two&off=nothing", "web_video", 1, "search", None),
    7: ("https://kinozal.tv/details.php?id=9", "torrent", 99, "search", None),  # the profile 99 does not exist
    8: ("https://1.ruchime.org/release/d", "web_video", 1, "search", None),
}


def alembic(db_path, *args):
    done = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, capture_output=True, text=True,
                          env={"ST_DB_URL": f"sqlite:///{db_path}", "PATH": "/usr/bin:/bin"})
    assert done.returncode == 0, done.stderr


def query(db_path, sql, params=()):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params)]


def columns(db_path, table):
    return [row["name"] for row in query(db_path, f"PRAGMA table_info({table})")]


def pointers_to_series(db_path):
    """Every foreign key that names `series`, whatever table carries it."""
    found = {}
    for table in (row["name"] for row in query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")):
        for key in query(db_path, f"PRAGMA foreign_key_list({table})"):
            if key["table"] == "series":
                found[(table, key["from"])] = key["to"]
    return found


@pytest.fixture(scope="module")
def old_db(tmp_path_factory):
    """A database at revision 0024 that holds series of every kind and rows of tables that point at them."""
    path = tmp_path_factory.mktemp("migration") / "old.db"
    alembic(path, "upgrade", "0024")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO parser_profiles (id, name) VALUES (1, 'профиль')")
        for series_id, (url, source_type, profile_id, search_mode, priority) in OLD_SERIES.items():
            conn.execute(
                "INSERT INTO series (id, url, name, name_en, site, save_path, season, state, auto_scan_enabled, "
                "source_type, parser_profile_id, ignored_seasons, vk_search_mode, vk_quality_priority, "
                "ignored_episodes) VALUES (?, ?, ?, ?, 'site', ?, '2', 'waiting', 1, ?, ?, '[1]', ?, ?, '{\"1\":[3]}')",
                (series_id, url, f"Сериал {series_id}", f"Series {series_id}", f"/media/{series_id}", source_type,
                 profile_id, search_mode, priority))
        # A series that was deleted: its number stays used up.
        conn.execute("INSERT INTO series (id, url, name, name_en, site, save_path, auto_scan_enabled, source_type, "
                     "vk_search_mode) VALUES (9, 'u', 'x', 'x', 's', '/m', 0, 'torrent', 'search')")
        conn.execute("DELETE FROM series WHERE id=9")
        for series_id in (1, 3):
            conn.execute("INSERT INTO scan_tasks (series_id, created_at, status, task_data, results_data) "
                         "VALUES (?, '2026-01-01', 'processing', '[]', '{}')", (series_id,))
            conn.execute("INSERT INTO download_tasks (task_key, series_id, status, task_type) "
                         "VALUES (?, ?, 'pending', 'vk_video')", (f"k{series_id}", series_id))
            conn.execute("INSERT INTO relocation_tasks (series_id, new_path, status) VALUES (?, '/m', 'done')",
                         (series_id,))
        conn.commit()
    return path


@pytest.fixture
def db(old_db, tmp_path):
    path = tmp_path / "work.db"
    shutil.copy(old_db, path)
    return path


def sources_of(db_path):
    rows = query(db_path, "SELECT * FROM series_sources ORDER BY series_id, position, id")
    return {row["series_id"]: row for row in rows}


def test_every_series_gets_exactly_one_source_with_its_values(db):
    alembic(db, "upgrade", "0025")
    sources = sources_of(db)
    assert sorted(sources) == sorted(OLD_SERIES)
    assert len(query(db, "SELECT id FROM series_sources")) == len(OLD_SERIES)
    for series_id, source in sources.items():
        url, source_type, _, search_mode, _ = OLD_SERIES[series_id]
        assert source["position"] == 0
        assert source["source_type"] == source_type
        assert source["vk_search_mode"] == search_mode
    # a torrent and a VK channel keep the address whole, query and bar included
    assert sources[1]["url"] == "https://kinozal.tv/details.php?id=7"
    assert sources[2]["url"] == "https://vkvideo.ru/@chan|тайтл"
    assert (sources[1]["parser_profile_id"], sources[2]["parser_profile_id"]) == (1, 1)


def test_the_choice_of_a_web_address_moves_into_the_source(db):
    alembic(db, "upgrade", "0025")
    sources = sources_of(db)
    assert sources[3]["url"] == "https://1.ruchime.org/release/a"
    assert (sources[3]["vibix_season"], json.loads(sources[3]["players_off"])) == (2, ["vk"])
    # the players come in the fixed order whatever order the address named them
    assert (sources[5]["vibix_season"], json.loads(sources[5]["players_off"])) == (None, ["vk", "vibix"])
    # nonsense in the address is not carried over, as the old reader ignored it
    assert sources[6]["url"] == "https://1.ruchime.org/release/c"
    assert (sources[6]["vibix_season"], json.loads(sources[6]["players_off"])) == (None, [])
    assert (sources[8]["vibix_season"], sources[8]["players_off"]) == (None, "[]")


def test_the_dunhuatv_choice_is_dropped_and_the_page_stays(db):
    alembic(db, "upgrade", "0025")
    source = sources_of(db)[4]
    assert source["url"] == "https://dunhuatv.ru/title/x"
    assert (source["vibix_season"], json.loads(source["players_off"])) == (None, [])


def test_a_profile_that_does_not_exist_is_no_profile(db):
    alembic(db, "upgrade", "0025")
    assert sources_of(db)[7]["parser_profile_id"] is None
    assert query(db, "PRAGMA foreign_key_check") == []


def test_series_loses_exactly_the_moved_columns_and_keeps_the_rest(db):
    before = {row["id"]: row for row in query(db, f"SELECT {', '.join(KEPT)} FROM series")}
    alembic(db, "upgrade", "0025")
    assert columns(db, "series") == KEPT
    after = {row["id"]: row for row in query(db, "SELECT * FROM series")}
    assert after == before
    assert "AUTOINCREMENT" in query(db, "SELECT sql FROM sqlite_master WHERE name='series'")[0]["sql"]


def test_what_pointed_at_series_still_points_at_it(db):
    pointers = pointers_to_series(db)
    assert ("scan_tasks", "series_id") in pointers
    alembic(db, "upgrade", "0025")
    assert pointers_to_series(db) == {**pointers, ("series_sources", "series_id"): "id"}
    assert query(db, "PRAGMA foreign_key_check") == []
    for table in ("scan_tasks", "download_tasks", "relocation_tasks"):
        assert len(query(db, f"SELECT id FROM {table}")) == 2


def test_a_number_that_was_used_is_not_given_again(db):
    alembic(db, "upgrade", "0025")
    with sqlite3.connect(db) as conn:
        cursor = conn.execute("INSERT INTO series (name, name_en, site, save_path, auto_scan_enabled) "
                              "VALUES ('новый', 'new', 's', '/m', 0)")
    assert cursor.lastrowid == 10  # 9 was deleted before the migration


def test_a_source_cannot_name_a_series_twice_or_a_missing_one(db):
    alembic(db, "upgrade", "0025")
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO series_sources (series_id, url, source_type) VALUES (1, "
                         "'https://kinozal.tv/details.php?id=7', 'torrent')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO series_sources (series_id, url, source_type) VALUES (999, 'u', 'torrent')")


def test_downgrade_brings_the_old_shape_and_the_values_back(db):
    old_columns = columns(db, "series")
    old_rows = {row["id"]: row for row in query(db, "SELECT * FROM series")}
    old_pointers = pointers_to_series(db)
    alembic(db, "upgrade", "0025")
    alembic(db, "downgrade", "0024")

    assert columns(db, "series") == old_columns
    assert "AUTOINCREMENT" in query(db, "SELECT sql FROM sqlite_master WHERE name='series'")[0]["sql"]
    assert query(db, "SELECT name FROM sqlite_master WHERE name='series_sources'") == []
    assert pointers_to_series(db) == old_pointers
    assert query(db, "PRAGMA foreign_key_check") == []
    rows = {row["id"]: row for row in query(db, "SELECT * FROM series")}
    assert sorted(rows) == sorted(old_rows)
    for series_id, row in rows.items():
        old = old_rows[series_id]
        for name in KEPT:
            assert row[name] == old[name], (series_id, name)
    # what the new shape can hold comes back; what it never held does not
    assert rows[1]["url"] == "https://kinozal.tv/details.php?id=7"
    assert rows[2]["url"] == "https://vkvideo.ru/@chan|тайтл"
    assert rows[2]["vk_search_mode"] == "get_all"
    assert rows[3]["url"] == "https://1.ruchime.org/release/a?vibix=2&off=vk"
    assert rows[5]["url"] == "https://1.ruchime.org/release/b?off=vk,vibix"
    assert rows[4]["url"] == "https://dunhuatv.ru/title/x"
    assert rows[2]["vk_quality_priority"] is None
    assert rows[7]["parser_profile_id"] is None
    assert (rows[1]["source_type"], rows[3]["source_type"], rows[3]["parser_profile_id"]) == ("torrent", "web_video", 1)


def test_downgrade_takes_the_source_with_the_lowest_position(db):
    alembic(db, "upgrade", "0025")
    with sqlite3.connect(db) as conn:
        # added later, so its id is the higher one, but it stands first
        conn.execute("INSERT INTO series_sources (series_id, position, url, source_type, parser_profile_id) "
                     "VALUES (2, -1, 'https://vkvideo.ru/@other|запрос', 'vk_video', 1)")
        conn.execute("INSERT INTO series_sources (series_id, position, url, source_type) "
                     "VALUES (2, 5, 'https://vkvideo.ru/@third|запрос', 'vk_video')")
    alembic(db, "downgrade", "0024")
    assert query(db, "SELECT url FROM series WHERE id=2")[0]["url"] == "https://vkvideo.ru/@other|запрос"


def test_downgrade_keeps_the_numbers_used_up(db):
    alembic(db, "upgrade", "0025")
    alembic(db, "downgrade", "0024")
    with sqlite3.connect(db) as conn:
        cursor = conn.execute("INSERT INTO series (url, name, name_en, site, save_path, auto_scan_enabled, "
                              "source_type, vk_search_mode) VALUES ('u', 'n', 'n', 's', '/m', 0, 'torrent', 'search')")
    assert cursor.lastrowid == 10


def test_a_series_that_lost_every_source_still_goes_back(db):
    alembic(db, "upgrade", "0025")
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM series_sources WHERE series_id=1")
    alembic(db, "downgrade", "0024")
    row = query(db, "SELECT url, source_type, vk_search_mode FROM series WHERE id=1")[0]
    assert (row["url"], row["source_type"], row["vk_search_mode"]) == ("", "torrent", "search")


def test_the_revision_follows_0024_and_back(db):
    alembic(db, "upgrade", "0025")
    assert query(db, "SELECT version_num FROM alembic_version")[0]["version_num"] == "0025"
    alembic(db, "downgrade", "0024")
    assert query(db, "SELECT version_num FROM alembic_version")[0]["version_num"] == "0024"
