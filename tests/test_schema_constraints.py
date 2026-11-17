"""What the schema itself refuses, on the head of the migrations: a second record of one thing, a record of a series that
is not there, an id given twice. The data of the owner leans on these; the migrations that brought them are proved once,
on the way to the production database, and their tests are gone with them."""
import sqlite3
from contextlib import closing

import pytest

from schema_db import migrated
from series_factory import insert_series


@pytest.fixture
def conn(tmp_path):
    with closing(sqlite3.connect(migrated(tmp_path / "schema.db"))) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        insert_series(connection, id=5, name="s", name_en="s", site="kinozal", save_path="/m",
                      url="https://kinozal.tv/details.php?id=7", source_type="torrent")
        connection.commit()
        yield connection


def test_an_id_of_a_series_that_was_used_is_not_given_again(conn):
    insert_series(conn, id=9, name="gone", name_en="gone", site="s", save_path="/m")
    conn.execute("DELETE FROM series_sources WHERE series_id = 9")
    conn.execute("DELETE FROM series WHERE id = 9")
    assert insert_series(conn, name="new", name_en="new", site="s", save_path="/m").series_id == 10


def test_a_source_cannot_name_a_series_twice_or_a_missing_one(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO series_sources (series_id, url, source_type) "
                     "VALUES (5, 'https://kinozal.tv/details.php?id=7', 'torrent')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO series_sources (series_id, url, source_type) VALUES (999, 'u', 'torrent')")


def test_a_torrent_is_one_row_per_hash_of_a_series_that_is_there(conn):
    conn.execute("INSERT INTO torrent_progress (qb_hash, series_id, status) VALUES ('hash-a', 5, 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO torrent_progress (qb_hash, series_id, status) VALUES ('hash-a', 5, 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO torrent_progress (qb_hash, series_id, status) VALUES ('hash-c', 99, 'x')")


def _release(conn, url="https://kinozal.tv/details.php?id=7", release="t1", infohash=None) -> int:
    return conn.execute("INSERT INTO torrent_releases (url, release, state, infohash, updated_at) "
                        "VALUES (?, ?, 'listed', ?, 'now')", (url, release, infohash)).lastrowid


def test_a_release_is_one_per_page_and_id_and_a_version_is_in_one_release_only(conn):
    first = _release(conn, infohash="h1")
    with pytest.raises(sqlite3.IntegrityError):
        _release(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _release(conn, release="t2", infohash="h1")
    _release(conn, release="t3")
    _release(conn, release="t4")  # releases not added to the client yet have no version
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE torrent_releases SET state = 'paused' WHERE id = ?", (first,))


def test_the_files_and_versions_of_a_release_go_with_it(conn):
    rid = _release(conn, infohash="h1")
    conn.execute("INSERT INTO torrent_release_files (release_id, path, size) VALUES (?, 'Show/E01.mkv', 10)", (rid,))
    conn.execute("INSERT INTO torrent_release_versions (release_id, infohash, added_at) VALUES (?, 'h1', 'now')", (rid,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO torrent_release_files (release_id, path, size) VALUES (?, 'Show/E01.mkv', 10)", (rid,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO torrent_release_files (release_id, path, size, progress) VALUES (?, 'b', 1, 1.5)", (rid,))
    conn.execute("DELETE FROM torrent_releases WHERE id = ?", (rid,))
    assert conn.execute("SELECT COUNT(*) FROM torrent_release_files").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM torrent_release_versions").fetchone()[0] == 0


def test_a_manual_place_is_an_episode_of_a_season_for_a_file_and_a_folder_for_a_track(conn):
    """The hand places, it never leaves out: a file has a season and an episode, a track a folder; nothing else is stored."""
    insert = ("INSERT INTO unpack_assignments (url, tab, item, season, episode, episode_last, folder) "
              "VALUES ('u', ?, ?, ?, ?, ?, ?)")
    conn.execute(insert, ("files", "Show/E01.mkv", 1, 1, None, None))
    conn.execute(insert, ("files", "Show/E01-E02.mkv", 1, 1, 2, None))
    conn.execute(insert, ("voices", "Show/Sound/E01.mka", None, None, None, "Студия"))
    conn.execute(insert, ("voice_streams", "Show/E01.mkv#2", None, None, None, "Студия"))
    for bad in (("files", "a", None, 1, None, None), ("files", "b", 1, None, None, None),
                ("files", "c", 1, 1, None, "Студия"), ("files", "d", 1, 3, 2, None), ("files", "x", None, None, None, None),
                ("voices", "e", None, None, None, None), ("subtitles", "f", 1, 1, None, "Субтитры"),
                ("other", "g", None, None, None, "Студия")):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, bad)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("files", "Show/E01.mkv", 1, 2, None, None))
    columns = [row[1] for row in conn.execute("PRAGMA table_info(unpack_assignments)")]
    assert "excluded" not in columns


def test_a_track_taken_out_of_its_folder_is_one_per_tab_of_tracks(conn):
    conn.execute("INSERT INTO unpack_removed (url, tab, item) VALUES ('u', 'voice_streams', 'r/E01.mkv#1')")
    for bad in ("('u', 'files', 'r/E01.mkv')", "('u', 'voice_streams', 'r/E01.mkv#1')"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"INSERT INTO unpack_removed (url, tab, item) VALUES {bad}")


def test_a_folder_of_the_editor_is_one_by_its_name_and_of_a_known_type(conn):
    conn.execute("INSERT INTO unpack_folders (url, kind, name, subtitle_type) VALUES ('u', 'subtitles', 'Надписи', 'signs')")
    conn.execute("INSERT INTO unpack_folders (url, kind, name) VALUES ('u', 'voice', 'Надписи')")
    for bad in ("('u', 'subtitles', 'Надписи', '')", "('u', 'voice', '', '')", "('u', 'subtitles', 'x', 'other')",
                "('u', 'subtitles', 'y', 'forced')",
                "('u', 'video', 'x', '')"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"INSERT INTO unpack_folders (url, kind, name, subtitle_type) VALUES {bad}")


def test_a_profile_is_of_a_series_unless_said_to_be_of_the_torrent_editor(conn):
    conn.execute("INSERT INTO parser_profiles (id, name) VALUES (1, 'p')")
    conn.execute("INSERT INTO parser_profiles (id, name, kind) VALUES (2, 'q', 'torrent')")
    assert conn.execute("SELECT kind FROM parser_profiles ORDER BY id").fetchall() == [("series",), ("torrent",)]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO parser_profiles (id, name, kind) VALUES (3, 'r', 'other')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO unpack_sources (url, voices_profile_id) VALUES ('u', 99)")
