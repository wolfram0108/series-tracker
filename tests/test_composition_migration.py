"""Migration 0035 of composition: a row the source's own choice leaves out is kept in the layout, `exclusion='source'`.
Up, the rows are kept as they were and the new word is allowed; down, the rows of that word go, the rest stays."""
import sqlite3
import subprocess
import sys

import pytest

ROW = ("INSERT INTO composition_rows (series_id, source_id, row_key, seq, excluded, exclusion, player, state) "
       "VALUES (?, ?, ?, ?, ?, ?, 'vk', 'playable')")


def alembic(path, *args, check=True):
    return subprocess.run([sys.executable, "-m", "alembic", *args], env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"},
                          cwd=".", check=check, capture_output=True, text=True)


def rows(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT row_key, seq, excluded, exclusion FROM composition_rows ORDER BY seq").fetchall()


@pytest.fixture
def at_0034(tmp_path):
    path = str(tmp_path / "migration.db")
    alembic(path, "upgrade", "0034")
    with sqlite3.connect(path) as conn:
        conn.execute(ROW, (1, 1, "a", 0, 0, None))
        conn.execute(ROW, (1, 1, "b", 1, 1, "rule"))
        conn.execute(ROW, (1, 1, "c", 2, 1, "no_number"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ROW, (1, 1, "d", 3, 1, "source"))
        conn.commit()
    return path


def test_up_keeps_every_row_and_allows_a_row_left_out_by_the_source(at_0034):
    before = rows(at_0034)
    alembic(at_0034, "upgrade", "0035")
    assert rows(at_0034) == before
    with sqlite3.connect(at_0034) as conn:
        conn.execute(ROW, (1, 1, "d", 3, 1, "source"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ROW, (1, 1, "e", 4, 1, "other"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ROW, (1, 1, "a", 5, 0, None))  # the key of a row stays unique
        conn.commit()


def test_down_takes_away_only_the_rows_left_out_by_the_source(at_0034):
    alembic(at_0034, "upgrade", "0035")
    with sqlite3.connect(at_0034) as conn:
        conn.execute(ROW, (1, 1, "d", 3, 1, "source"))
        conn.commit()
    alembic(at_0034, "downgrade", "0034")
    assert rows(at_0034) == [("a", 0, 0, None), ("b", 1, 1, "rule"), ("c", 2, 1, "no_number")]
