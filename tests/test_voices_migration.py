"""Migration 0029 of the voices: `voice_dubs` loses `site_episode` and keeps every other column and row, and the series
that already have voice-overs are entered among those a sync was asked for, so that what was waiting goes on."""
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
    assert columns(at_0028) == names
    assert dubs(at_0028, *names) == before and len(before) == len(ROWS)
    assert before[3]["job_id"] == "job-9" and before[4]["output_path"].endswith("BlackCat.ru.flac")


def test_a_series_with_voice_overs_may_download_and_one_without_waits_for_its_sync(at_0028):
    alembic(at_0028, "upgrade", "0029")
    with sqlite3.connect(at_0028) as conn:
        assert [row[0] for row in conn.execute("SELECT series_id FROM voice_series")] == [7]
        assert conn.execute("SELECT count(*) FROM voice_series WHERE allowed_at IS NULL").fetchone()[0] == 0


def test_the_rows_survive_the_way_back_and_the_unique_key_still_holds(at_0028):
    names = [name for name in columns(at_0028) if name != "site_episode"]
    alembic(at_0028, "upgrade", "0029")
    before = dubs(at_0028, *names)
    alembic(at_0028, "downgrade", "0028")
    assert "site_episode" in columns(at_0028) and dubs(at_0028, *names) == before
    with sqlite3.connect(at_0028) as conn:
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='voice_series'").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO voice_dubs (series_id, season, episode, site_episode, voice_key, voice_label, "
                         "chain, updated_at) VALUES (7, 1, 1, 0, 'blackcat', 'BlackCat', '[]', 'now')")
    alembic(at_0028, "upgrade", "0029")
    assert dubs(at_0028, *names) == before
