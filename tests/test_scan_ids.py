"""Формулы идентификаторов (Р-10) — дифф-верификация на ВСЕХ реальных
записях фикстуры прод-БД: существующие id обязаны воспроизводиться."""
import sqlite3

import pytest

from modules.scan import ids

FIXTURE = "tests/fixtures/app.fixture.db"


@pytest.fixture(scope="module")
def fixture_conn():
    conn = sqlite3.connect(FIXTURE)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def test_torrent_ids_match_production(fixture_conn):
    rows = fixture_conn.execute(
        "SELECT torrent_id, link, date_time FROM torrents").fetchall()
    assert len(rows) > 100  # фикстура реальная, не пустышка
    mismatches = [r["torrent_id"] for r in rows
                  if ids.torrent_id(r["link"], r["date_time"])
                  != r["torrent_id"]]
    assert mismatches == []


def test_media_unique_ids_match_production(fixture_conn):
    rows = fixture_conn.execute(
        "SELECT unique_id, source_url, publication_date, series_id "
        "FROM media_items").fetchall()
    assert len(rows) > 100
    mismatches = [r["unique_id"] for r in rows
                  if ids.media_unique_id(r["source_url"],
                                         r["publication_date"],
                                         r["series_id"]) != r["unique_id"]]
    assert mismatches == []


def test_media_unique_id_accepts_iso_z():
    # sources отдаёт публикацию ISO-Z — формула обязана дать тот же id,
    # что и naive-UTC datetime из БД
    a = ids.media_unique_id("https://vk.com/video-1_2",
                            "2026-03-17T20:13:16Z", 5)
    b = ids.media_unique_id("https://vk.com/video-1_2",
                            "2026-03-17 20:13:16", 5)
    assert a == b
