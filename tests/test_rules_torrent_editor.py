"""The rules of the torrent editor (stage 15): the line of each tab as the editor writes it, and the actions that set a
folder of a voice-over or of subtitles, a language and a type of subtitles; the season and the episode of a file are the
constructor's own extractions over the line of the tab «Файлы». The layouts are those of the corpus of the owner's
releases (DESIGN.md): every file in one folder with suffixes, folders by studio, numbers only in the names of the sounds,
seasons and OVA in folders of their own."""
import json

import pytest

from core.contracts.torrent import episode_word, files_line, kind_of, stream_line, track_file_line
from modules.rules import compile_profile, process_title


def rule(rule_id, name, actions, conditions=()):
    return {"id": rule_id, "name": name, "priority": rule_id, "continue_after_match": True,
            "action_pattern": json.dumps(actions, ensure_ascii=False), "conditions": list(conditions)}


def contains(*blocks, ctype="contains"):
    return {"condition_type": ctype, "logical_operator": "AND", "pattern": json.dumps(list(blocks), ensure_ascii=False)}


def text(value):
    return {"type": "text", "value": value}


NUMBER, ANY = {"type": "number"}, {"type": "any_text"}


def assign(action, value):
    return {"action_type": action, "action_pattern": value}  # the value of an assignment is stored as its text


def extract(action, *blocks):
    return {"action_type": action, "action_pattern": json.dumps(list(blocks), ensure_ascii=False)}


def test_the_lines_of_the_tabs_are_written_as_the_mockup_shows_them():
    assert files_line("Show/Rus Audio/JAM/Show - 05.mka") == "D:Show/Rus Audio/JAM | Show - 05.mka | K:звук | X:mka"
    assert files_line("Show - 05.mkv") == "Show - 05.mkv | K:видео | X:mkv"
    assert files_line("Show/Fonts/arial.ttf") == "D:Show/Fonts | arial.ttf | K:шрифт | X:ttf"
    assert track_file_line("Show/sub.rus/[Shift]/05.ass", episode_word(1, 5)) == \
        "D:Show/sub.rus/[Shift] | 05.ass | X:ass | S:S01E05"
    assert episode_word(2, 1, 3) == "S02E01-E03" and episode_word(None, None) == ""
    assert stream_line("audio", 2, "jpn", "Оригинал", "aac") == "T:звук | A:2 | L:jpn | N:Оригинал | C:aac"
    assert stream_line("subtitle", 3) == "T:субтитры | A:3"
    assert kind_of("x.EAC3") == kind_of("x.mka") and kind_of("x.nfo").value == "other"


FILES = compile_profile(1, [
    rule(1, "не видео и не звук", [{"action_type": "exclude"}], [contains(text("K:другое"))]),
    rule(2, "сезон из каталога", [extract("extract_season", text("Season "), NUMBER)], [contains(text("D:"))]),
    rule(3, "серия после тире", [extract("extract_single", text(" - "), NUMBER)]),
    rule(4, "серия SxxEyy", [extract("extract_season", text("S"), NUMBER, text("E")),
                              extract("extract_single", text("E"), NUMBER)]),
])


@pytest.mark.parametrize("path, numbers", [
    ("Show/Show - 05 [1080p].mkv", {"episode": 5}),                              # every file in one folder
    ("Show/Rus Audio/JAM/Show - 05.mka", {"episode": 5}),                        # folders by studio
    ("Show/Season 2/Show - 03.mkv", {"season": 2, "episode": 3}),                 # seasons in folders of their own
    ("Show/Show.S01E07.1080p.mkv", {"season": 1, "episode": 7}),
])
def test_the_season_and_the_episode_of_a_file_come_from_the_rules_over_its_line(path, numbers):
    got = process_title(FILES, files_line(path))
    assert not got["excluded"] and got["extracted"] == numbers


def test_a_file_that_is_neither_video_nor_sound_nor_subtitles_is_excluded_by_its_kind():
    assert process_title(FILES, files_line("Show/readme.nfo"))["excluded"]


VOICES = compile_profile(2, [
    rule(1, "студии", [assign("assign_voice_folder", "JAM"), assign("assign_language", "rus")],
         [contains(text("D:Show/Rus Audio/JAM"))]),
    rule(2, "надписи", [assign("assign_subtitle_folder", "Надписи"), assign("assign_subtitle_type", "signs")],
         [contains(text("D:"), ANY, text("Signs"))]),
])


def test_a_rule_of_the_editor_puts_a_track_in_a_folder_with_a_language_and_a_type():
    sound = process_title(VOICES, track_file_line("Show/Rus Audio/JAM/Show - 05.mka", "S01E05"))
    signs = process_title(VOICES, track_file_line("Show/Subs/Signs/05.ass", "S01E05"))
    assert sound["extracted"] == {"voice_folder": "JAM", "language": "rus"}
    assert signs["extracted"] == {"subtitle_folder": "Надписи", "subtitle_type": "signs"}


def test_a_type_of_subtitles_the_editor_does_not_know_or_an_empty_folder_is_an_error_not_a_value():
    profile = compile_profile(3, [rule(1, "плохо", [assign("assign_subtitle_type", "half"), assign("assign_voice_folder", " ")])])
    got = process_title(profile, "S:S01E01")
    assert got["extracted"] == {} and len(got["errors"]) == 2
    assert all('"rule_value_bad"' in error for error in got["errors"])


async def test_the_profiles_of_the_editor_are_not_listed_among_the_profiles_of_a_series(tmp_path):
    from core.db import Database
    from modules.rules.repository import RulesRepository
    from schema_db import migrated
    repo = RulesRepository(Database(str(migrated(tmp_path / "r.db"))))
    series = await repo.create_profile("Сериал")
    editor = await repo.create_profile("Торрент: файлы", "torrent")
    assert [p["id"] for p in await repo.list_profiles()] == [series]
    assert [p["id"] for p in await repo.list_profiles("torrent")] == [editor]
