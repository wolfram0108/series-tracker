"""Дифф-верификация имён файлов (Р-15): для КАЖДОГО скачанного
медиа-элемента фикстуры прод-БД пересчитанное имя обязано совпасть с
хранящимся в final_filename — имя файла и есть итоговый артефакт
системы. Плюс юнит-кейсы формата.
"""
import sqlite3

import pytest

from modules.rules import formatter
from modules.rules.engine import compile_profile, process_title

FIXTURE = "tests/fixtures/app.fixture.db"


# --- юнит-кейсы формата --------------------------------------------------------------

def test_format_basic_and_sanitize():
    series = {"name_en": 'Bad: "Name"?', "source_type": "vk_video"}
    name = formatter.format_name(series, {"season": 2, "episode": 7,
                                          "voiceover": "AniLibria",
                                          "resolution": 1080})
    assert name == "Bad Name s02e07 [AniLibria] 1080p.mp4"


def test_format_compilation_range_and_subdir():
    series = {"name_en": "Show", "source_type": "vk_video"}
    name = formatter.format_name(series, {"season": 1, "start": 1, "end": 3},
                                 original_filename="sub/old.mp4")
    assert name == "sub/Show s01e01-e03.mp4"


def test_series_season_overrides_all():
    series = {"name_en": "Show", "season": "s03", "source_type": "torrent"}
    name = formatter.format_name(series, {"season": 1, "episode": 5},
                                 original_filename="x.mkv")
    assert name == "Show s03e05.mkv"


def test_torrent_season_folder_logic():
    multi = {"name_en": "S", "season": None}
    assert formatter.torrent_season_folder(multi, {"season": 4}, "a.mkv") == 4
    assert formatter.torrent_season_folder(multi, {}, "Specials/a.mkv") == 0
    assert formatter.torrent_season_folder(multi, {}, "a.mkv") == 1
    single = {"name_en": "S", "season": "Season 2"}
    assert formatter.torrent_season_folder(single, {"season": 9},
                                           "a.mkv") == 2


# --- дифф со всеми реальными именами фикстуры ------------------------------------------

def test_all_production_filenames_reproduced():
    conn = sqlite3.connect(FIXTURE)
    conn.row_factory = sqlite3.Row

    def load_rules(profile_id):
        rules = []
        for r in conn.execute("SELECT * FROM parser_rules WHERE "
                              "profile_id=? ORDER BY priority",
                              (profile_id,)):
            conds = [dict(c) for c in conn.execute(
                "SELECT * FROM parser_rule_conditions WHERE rule_id=? "
                "ORDER BY id", (r["id"],))]
            rules.append({**dict(r), "conditions": conds})
        return rules

    series_map = {r["id"]: dict(r)
                  for r in conn.execute("SELECT * FROM series")}
    items = [dict(r) for r in conn.execute(
        "SELECT * FROM media_items WHERE status='completed' AND "
        "final_filename IS NOT NULL")]
    assert len(items) > 300  # фикстура реальная

    profiles: dict = {}
    mismatches = []
    for item in items:
        series = series_map[item["series_id"]]
        pid = series["parser_profile_id"]
        if pid not in profiles:
            profiles[pid] = compile_profile(pid, load_rules(pid))
        extracted = process_title(
            profiles[pid], item["source_title"] or "").get("extracted") or {}
        metadata = formatter.build_metadata(series, item, extracted)
        name = formatter.format_name(
            series, metadata, original_filename=item["final_filename"])
        if name != item["final_filename"]:
            mismatches.append((item["unique_id"], item["final_filename"],
                               name))
    assert mismatches == []
    conn.close()
