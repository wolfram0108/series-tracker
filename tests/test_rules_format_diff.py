"""Имена файлов (Р-15): имя файла — итоговый артефакт системы, поэтому формат закреплён неизменными векторами записи
медиа-элемента. Сверено 05.11.2026 с рабочей БД: из 994 скачанных элементов имя 975 воспроизводится; 19 элементов
сериала 59 хранят озвучку `AniStar`, а их файлы на диске остались `[AniSTAR]` (имя файла отстало от записи — не формат).
Плюс юнит-кейсы формата.
"""
import pytest

from modules.rules import formatter


# --- юнит-кейсы формата --------------------------------------------------------------

def test_format_basic_and_sanitize():
    series = {"name_en": 'Bad: "Name"?', "kind": "video"}
    name = formatter.format_name(series, {"season": 2, "episode": 7,
                                          "voiceover": "AniLibria",
                                          "resolution": 1080})
    assert name == "Bad Name s02e07 [AniLibria] 1080p.mp4"


def test_format_compilation_range_and_subdir():
    series = {"name_en": "Show", "kind": "video"}
    name = formatter.format_name(series, {"season": 1, "start": 1, "end": 3},
                                 original_filename="sub/old.mp4")
    assert name == "sub/Show s01e01-e03.mp4"


def test_series_season_is_default_not_override():
    """Сезон карточки заполняет пропуск, но не спорит с распознанным."""
    series = {"name_en": "Show", "season": "s03", "kind": "torrent"}
    name = formatter.format_name(series, {"episode": 5},
                                 original_filename="x.mkv")
    assert name == "Show s03e05.mkv"
    name = formatter.format_name(series, {"season": 1, "episode": 5},
                                 original_filename="x.mkv")
    assert name == "Show s01e05.mkv"


def test_torrent_season_folder_logic():
    multi = {"name_en": "S", "season": None}
    assert formatter.torrent_season_folder(multi, {"season": 4}, "a.mkv") == 4
    assert formatter.torrent_season_folder(multi, {}, "Specials/a.mkv") == 0
    assert formatter.torrent_season_folder(multi, {}, "a.mkv") == 1
    single = {"name_en": "S", "season": "Season 2"}
    assert formatter.torrent_season_folder(single, {}, "a.mkv") == 2
    # спешел из правил не схлопывается в сезон карточки
    assert formatter.torrent_season_folder(single, {"season": 0},
                                           "a.mkv") == 0
    assert formatter.torrent_season_folder(single, {"season": 9},
                                           "a.mkv") == 9
    unreadable = {"name_en": "S", "season": "Season"}
    assert formatter.torrent_season_folder(unreadable, {}, "a.mkv") is None


# --- the name made from the record of a media item -----------------------------------------------------

def video(name: str) -> dict:
    return {"name_en": name, "kind": "video", "season": None}


@pytest.mark.parametrize("series, item, original, expected", [
    (video("Shrouding the Heavens"), {"season": 1, "episode_start": 175, "episode_end": 175, "resolution": 2160,
                                      "voiceover_tag": "AniStar"}, "x.mp4",
     "Shrouding the Heavens s01e175 [AniStar] 2160p.mp4"),
    (video("Shrouding the Heavens"), {"season": 1, "episode_start": 109, "episode_end": 110, "resolution": 720,
                                      "voiceover_tag": "Animy"}, "old.mp4",
     "Shrouding the Heavens s01e109-e110 [Animy] 720p.mp4"),
    (video("The God of War Dominates"), {"season": 1, "episode_start": 683, "episode_end": None, "resolution": 2160,
                                         "voiceover_tag": "ПВА ШОУ"}, "x.mkv",
     "The God of War Dominates s01e683 [ПВА ШОУ] 2160p.mkv"),
    # Stage 12: a video cut out of a compilation is named by the compilation's voice-over and resolution.
    (video("Show"), {"season": 1, "episode_start": 12, "episode_end": 12, "resolution": 1080, "voiceover_tag": "AniStar",
                     "cut": '{"key": "compilation:x", "height": 2160, "voice_group": "ПВА"}'}, "x.mkv",
     "Show s01e12 [ПВА] 2160p.mkv"),
    (video("Show"), {"season": 1, "episode_start": 12, "episode_end": None, "part": 2, "resolution": 1080,
                     "voiceover_tag": "AniLibria"}, "x.mp4", "Show s01e12 Part 2 [AniLibria] 1080p.mp4"),
    ({"name_en": "Show", "kind": "torrent", "season": "s02"}, {"season": None, "episode_start": 5, "episode_end": None,
                                                               "resolution": None, "voiceover_tag": None},
     "Show.S02E05.1080p.mkv", "Show s02e05.mkv"),
])
def test_the_name_of_a_file_is_made_from_the_record_of_its_item(series, item, original, expected):
    assert formatter.format_name(series, formatter.build_metadata(series, item), original_filename=original) == expected
