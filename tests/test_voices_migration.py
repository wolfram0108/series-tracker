"""Migration 0029 of the voices: `voice_dubs` loses `site_episode` and keeps every other column and row; the rows that
exist are admitted to download; the files of source rows that cover several episodes get their table."""
import json
import sqlite3
import subprocess
import sys

import pytest

from series_factory import insert_series

ROWS = [
    # (series, season, episode, key, label, status, columns the step fills)
    (7, 1, 1, "BlackCat", "BlackCat", "pending",
     {"attempt": 1, "error": '{"code":"download_failed"}'}),
    (7, 1, 1, "RedHeadSound", "RedHeadSound", "downloading", {"temp_path": "/w/downloads/7/s01e01/7_s01e01_RedHeadSound.mp4"}),
    (7, 1, 2, "BlackCat", "BlackCat", "ready", {"temp_path": "/w/downloads/7/s01e02/7_s01e02_BlackCat.mp4"}),
    (7, 1, 2, "RedHeadSound", "RedHeadSound", "aligning",
     {"temp_path": "/w/downloads/7/s01e02/x.mp4", "job_id": "job-9", "submit_key": "abc123", "out_dir": "/w/out/abc123",
      "ref_uid": "u2", "ref_path": "/lib/Series s01e02.mp4"}),
    (7, 1, 3, "BlackCat", "BlackCat", "placed",
     {"output_path": "/lib/Series s01e03.BlackCat.ru.flac", "ref_uid": "u3", "ref_path": "/lib/Series s01e03.mp4",
      "job_id": "job-3", "out_dir": "/w/out/def456"}),
    (7, 1, 3, "RedHeadSound", "RedHeadSound", "rejected", {"outcome": '{"code":"suspect_residual"}'}),
    (7, 2, 1, "BlackCat", "BlackCat", "failed", {"error": '{"code":"not_in_any_source"}'}),
]
CHAIN = [{"player": "cvh", "studio": "BlackCat", "url": "https://site/#cvh-1"},
         {"player": "kodik", "studio": "BlackCat", "url": "https://site/#kodik-1"}]


def alembic(path, *args):
    subprocess.run([sys.executable, "-m", "alembic", *args],
                   env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"}, cwd=".", check=True,
                   capture_output=True)


def columns(path):
    with sqlite3.connect(path) as conn:
        return [row[1] for row in conn.execute("PRAGMA table_info(voice_dubs)")]


def dubs(path, *names):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT {', '.join(names)} FROM voice_dubs ORDER BY id")]


@pytest.fixture
def at_0028(tmp_path):
    path = str(tmp_path / "migration.db")
    alembic(path, "upgrade", "0028")
    with sqlite3.connect(path) as conn:
        insert_series(conn, id=7, name="s", name_en="s", site="dunhuatv", url="https://site/title.html",
                      source_type="web_video")
        insert_series(conn, id=8, name="t", name_en="t", site="dunhuatv", url="https://site/other.html",
                      source_type="web_video")
        for number, (series, season, episode, key, label, status, fields) in enumerate(ROWS, start=1):
            values = {"series_id": series, "season": season, "episode": episode, "site_episode": 40 + number,
                      "voice_key": key.lower(), "voice_label": label, "chain": json.dumps(CHAIN), "status": status,
                      "updated_at": "2026-10-30 10:00:00.000000", **fields}
            conn.execute(f"INSERT INTO voice_dubs ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                         tuple(values.values()))
        conn.commit()
    return path


def test_the_column_goes_and_every_row_keeps_every_other_value(at_0028):
    names = [name for name in columns(at_0028) if name != "site_episode"]
    before = dubs(at_0028, *names)
    alembic(at_0028, "upgrade", "0029")
    assert columns(at_0028) == [*names, "admitted", "file_id"]
    assert dubs(at_0028, *names) == before and len(before) == len(ROWS)
    assert before[3]["job_id"] == "job-9" and before[4]["output_path"].endswith("BlackCat.ru.flac")


def test_the_rows_that_exist_are_admitted_and_have_no_file(at_0028):
    alembic(at_0028, "upgrade", "0029")
    rows = dubs(at_0028, "admitted", "file_id")
    assert rows and {(row["admitted"], row["file_id"]) for row in rows} == {(1, None)}
    with sqlite3.connect(at_0028) as conn:
        assert conn.execute("SELECT count(*) FROM voice_files").fetchone()[0] == 0


def test_a_file_is_one_row_per_source_row_and_address(at_0028):
    alembic(at_0028, "upgrade", "0029")
    with sqlite3.connect(at_0028) as conn:
        add = ("INSERT INTO voice_files (series_id, source_id, row_key, url, updated_at) VALUES (7, ?, ?, ?, 'now')")
        conn.execute(add, (1, "k", "https://site/a"))
        conn.execute(add, (1, "k", "https://site/b"))  # the row changed its video: a file of its own
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(add, (1, "k", "https://site/a"))
        assert conn.execute("SELECT status, attempt FROM voice_files").fetchall() == [("pending", 0)] * 2


def test_the_rows_survive_the_way_back_the_files_are_journaled_and_the_unique_key_still_holds(at_0028):
    names = [name for name in columns(at_0028) if name != "site_episode"]
    alembic(at_0028, "upgrade", "0029")
    with sqlite3.connect(at_0028) as conn:
        conn.execute("INSERT INTO voice_files (series_id, source_id, row_key, url, status, temp_path, updated_at) "
                     "VALUES (7, 1, 'k', 'https://site/a', 'ready', '/w/downloads/7/f1/x.mp4', 'now')")
        conn.execute("UPDATE voice_dubs SET file_id=1 WHERE id=2")
        conn.commit()
    before = dubs(at_0028, *[name for name in names if name not in ("file_id", "admitted")])
    alembic(at_0028, "downgrade", "0028")
    after = dubs(at_0028, *[name for name in names if name not in ("file_id", "admitted", "status", "attempt")])
    assert after == [{key: value for key, value in row.items() if key not in ("status", "attempt")} for row in before]
    assert "site_episode" in columns(at_0028) and "file_id" not in columns(at_0028)
    with sqlite3.connect(at_0028) as conn:
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='voice_files'").fetchone()[0] == 0
        assert conn.execute("SELECT path, kind FROM voice_cleanup").fetchall() == [("/w/downloads/7/f1/x.mp4", "path")]
        assert conn.execute("SELECT status, attempt FROM voice_dubs WHERE id=2").fetchone() == ("pending", 0)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO voice_dubs (series_id, season, episode, site_episode, voice_key, voice_label, "
                         "chain, updated_at) VALUES (7, 1, 1, 0, 'blackcat', 'BlackCat', '[]', 'now')")
    alembic(at_0028, "upgrade", "0029")
    assert dubs(at_0028, *[name for name in names if name not in ("file_id", "admitted")]) == [
        {**row, **({"status": "pending", "attempt": 0} if row["id"] == 2 else {})} for row in before]


# ---- 0032: the part of an episode is a part of the key ---------------------------------------------------------------

FULL = ["id", "series_id", "season", "episode", "voice_key", "voice_label", "chain", "attempt", "status", "temp_path",
        "error", "updated_at", "job_id", "aligned_path", "output_path", "outcome", "ref_uid", "ref_path", "move_from",
        "submit_key", "out_dir", "admitted", "file_id"]


@pytest.fixture
def at_0031(at_0028):
    alembic(at_0028, "upgrade", "0031")
    return at_0028


def test_every_row_keeps_every_value_and_has_no_part_and_the_ids_are_kept(at_0031):
    before = dubs(at_0031, *FULL)
    alembic(at_0031, "upgrade", "0032")
    assert dubs(at_0031, *FULL) == before and len(before) == len(ROWS)
    assert {row["part"] for row in dubs(at_0031, "part")} == {0}
    assert "part" in columns(at_0031)


def test_two_parts_of_one_voice_over_are_two_rows_and_one_part_is_one_row(at_0031):
    alembic(at_0031, "upgrade", "0032")
    add = ("INSERT INTO voice_dubs (series_id, season, episode, part, voice_key, voice_label, chain, updated_at) "
           "VALUES (7, 1, 1, ?, 'blackcat', 'BlackCat', '[]', 'now')")
    with sqlite3.connect(at_0031) as conn:
        conn.execute(add, (1,))
        conn.execute(add, (2,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(add, (2,))
        with pytest.raises(sqlite3.IntegrityError):  # the rows that exist are part 0 and stay one row of their key
            conn.execute(add, (0,))


def test_the_way_back_keeps_the_first_row_of_a_key_and_journals_what_the_other_parts_own(at_0031):
    alembic(at_0031, "upgrade", "0032")
    with sqlite3.connect(at_0031) as conn:
        conn.execute(
            "INSERT INTO voice_dubs (series_id, season, episode, part, voice_key, voice_label, chain, updated_at, "
            "temp_path, output_path) VALUES (7, 1, 1, 2, 'blackcat', 'BlackCat', '[]', 'now', '/w/p2.mp4', '/lib/p2.flac')")
        conn.commit()
    alembic(at_0031, "downgrade", "0031")
    assert "part" not in columns(at_0031)
    assert len(dubs(at_0031, "id")) == len(ROWS)
    with sqlite3.connect(at_0031) as conn:
        assert sorted(conn.execute("SELECT path, kind FROM voice_cleanup").fetchall()) == [
            ("/lib/p2.flac", "artifact"), ("/w/p2.mp4", "path")]
    alembic(at_0031, "upgrade", "0032")
    assert len(dubs(at_0031, "id")) == len(ROWS)


def test_the_way_back_keeps_the_row_of_the_whole_episode_whatever_the_order_the_parts_were_made_in_and_journals_all_the_rest(at_0031):
    alembic(at_0031, "upgrade", "0032")
    add = ("INSERT INTO voice_dubs (series_id, season, episode, part, voice_key, voice_label, chain, updated_at, status, "
           "output_path, temp_path, job_id, out_dir, submit_key) VALUES (7, 1, 1, ?, 'cat', 'Cat', '[]', 'now', ?, ?, ?, ?, ?, ?)")
    with sqlite3.connect(at_0031) as conn:
        conn.execute(add, (1, "placed", "/lib/p1.flac", "/w/p1.mp4", "job-p1", "/w/out/p1", None))
        conn.execute(add, (0, "placed", "/lib/whole.flac", "/w/whole.mp4", "job-whole", "/w/out/whole", None))
        conn.execute(add, (2, "aligning", "/lib/p2.flac", "/w/p2.mp4", None, "/w/out/p2", "send-p2"))
        conn.commit()
    alembic(at_0031, "downgrade", "0031")
    with sqlite3.connect(at_0031) as conn:
        kept = conn.execute("SELECT output_path FROM voice_dubs WHERE voice_key='cat'").fetchall()
        journal = {(path, kind) for path, kind in conn.execute("SELECT path, kind FROM voice_cleanup")}
    assert kept == [("/lib/whole.flac",)]
    assert {("/lib/p1.flac", "artifact"), ("/w/p1.mp4", "path"), ("job-p1", "job"),
            ("/lib/p2.flac", "artifact"), ("/w/p2.mp4", "path"), ("send-p2", "submission")} <= journal
    assert not any(path in ("/lib/whole.flac", "/w/whole.mp4", "job-whole") for path, _ in journal)


def test_the_way_back_hands_over_a_job_only_when_no_row_that_stays_holds_it(at_0031):
    """A job at the service is the work of a batch: the part that goes does not take with it a job another voice-over keeps."""
    alembic(at_0031, "upgrade", "0032")
    add = ("INSERT INTO voice_dubs (series_id, season, episode, part, voice_key, voice_label, chain, updated_at, status, "
           "job_id, out_dir, submit_key) VALUES (7, 1, 1, ?, ?, ?, '[]', 'now', ?, ?, ?, ?)")
    with sqlite3.connect(at_0031) as conn:
        conn.execute(add, (0, "cat", "Cat", "aligning", "job-whole", "/w/out/whole", None))
        conn.execute(add, (1, "cat", "Cat", "aligning", "job-shared", "/w/out/shared", None))
        conn.execute(add, (0, "dog", "Dog", "aligning", "job-shared", "/w/out/shared", None))
        conn.execute(add, (2, "cat", "Cat", "aligning", None, "/w/out/send", "send-shared"))
        conn.execute(add, (0, "emu", "Emu", "aligning", None, "/w/out/send", "send-shared"))
        conn.execute(add, (3, "cat", "Cat", "aligning", "job-own", "/w/out/own", None))
        conn.commit()
    alembic(at_0031, "downgrade", "0031")
    with sqlite3.connect(at_0031) as conn:
        journal = {(path, kind) for path, kind in conn.execute("SELECT path, kind FROM voice_cleanup")}
    assert ("job-own", "job") in journal  # nobody who stays holds it
    assert not any(path in ("job-shared", "send-shared", "job-whole") for path, _ in journal)
