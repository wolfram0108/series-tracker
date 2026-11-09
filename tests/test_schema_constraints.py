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


def test_a_reference_of_a_slot_is_one_with_or_without_a_part_and_its_mark_is_one_of_the_five(conn):
    insert = ("INSERT INTO scan_references (series_id, season, episode, part, mark, source_id, row_key, unique_id) "
              "VALUES (5, 1, 1, ?, 'by_level', 1, 'k', 'u1')")
    for part in (None, 1, 2):
        conn.execute(insert, (part,))
    for part in (None, 1):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (part,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO scan_references (series_id, season, episode, mark) VALUES (5, 1, 9, 'maybe')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO scan_references (series_id, season, episode, mark) VALUES (999, 1, 9, 'none')")


def test_a_torrent_is_one_row_per_hash_of_a_series_that_is_there(conn):
    conn.execute("INSERT INTO torrent_progress (qb_hash, series_id, status) VALUES ('hash-a', 5, 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO torrent_progress (qb_hash, series_id, status) VALUES ('hash-a', 5, 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO torrent_progress (qb_hash, series_id, status) VALUES ('hash-c', 99, 'x')")


def test_two_parts_of_one_voice_over_are_two_rows_and_a_part_is_one_row(conn):
    add = ("INSERT INTO voice_dubs (series_id, season, episode, part, voice_key, voice_label, chain, updated_at) "
           "VALUES (5, 1, 1, ?, 'blackcat', 'BlackCat', '[]', 'now')")
    conn.execute(add, (1,))
    conn.execute(add, (2,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(add, (2,))


def test_a_series_has_at_most_one_markup_reference(conn):
    conn.execute("INSERT INTO series_sources (series_id, url, source_type, markup) VALUES (5, 'a', 'web_video', 1)")
    conn.execute("INSERT INTO series_sources (series_id, url, source_type, markup) VALUES (5, 'b', 'web_video', 0)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE series_sources SET markup = 1 WHERE url = 'b'")
