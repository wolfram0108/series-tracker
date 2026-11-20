"""Имена файлов (Р-15): имя файла — итоговый артефакт системы, поэтому формат закреплён неизменными векторами записи
медиа-элемента. Сверено 05.11.2026 с рабочей БД: из 994 скачанных элементов имя 975 воспроизводится; 19 элементов
сериала 59 хранят озвучку `AniStar`, а их файлы на диске остались `[AniSTAR]` (имя файла отстало от записи — не формат).
Плюс юнит-кейсы формата.
"""

from modules.rules import formatter


# --- юнит-кейсы формата --------------------------------------------------------------

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
    return {"name_en": name, "season": None}
