"""Формулы идентификаторов (Р-10): id уже записанных строк обязаны воспроизводиться, поэтому каждая формула закреплена
неизменным вектором. Значения сверены 05.11.2026 со всеми строками рабочей БД: 44 торрента по `torrent_id`; 1902 строки
`media_items` — 1752 по `media_unique_id`, 1 по `upgrade_unique_id`, 147 по `row_unique_id`, 2 по `upgrade_row_unique_id`."""
import pytest

from core import ids

VIDEO = "https://vkvideo.ru/video_ext.php?oid=-1&id=2&hash=ab"
ROW_VIDEO = "https://vkvideo.ru/video_ext.php?oid=-223748566&id=456240501&hash=ab"


@pytest.mark.parametrize("made, expected", [
    (lambda: ids.torrent_id("https://kinozal.tv/details.php?id=2034617", "05.05.2026 10:15:00"), "f9d0f14f9e9a13e0"),
    (lambda: ids.torrent_id("https://rutracker.org/forum/viewtopic.php?t=6612345", None), "a1d47e801dbc43ab"),
    (lambda: ids.media_unique_id(VIDEO, "2026-03-17 20:13:16", 5), "d9830b904b0c4bda"),
    (lambda: ids.upgrade_unique_id(VIDEO, "2026-03-17 20:13:16", 5, 2160), "909c0ef343489d6a"),
    (lambda: ids.row_unique_id(57, 10, "-223748566_456240501"), "ebe4bcc6390e6d79"),
    (lambda: ids.version_row_unique_id(57, 10, "-223748566_456240501", ROW_VIDEO), "536993a7f07437d4"),
    (lambda: ids.upgrade_row_unique_id(57, 10, "-223748566_456240501", 2160, ROW_VIDEO), "2665f08054d77c2a"),
])
def test_every_formula_gives_the_id_it_gave_to_the_rows_already_written(made, expected):
    assert made() == expected


def test_media_unique_id_accepts_iso_z():
    # sources отдаёт публикацию ISO-Z — формула обязана дать тот же id,
    # что и naive-UTC datetime из БД
    a = ids.media_unique_id("https://vk.com/video-1_2",
                            "2026-03-17T20:13:16Z", 5)
    b = ids.media_unique_id("https://vk.com/video-1_2",
                            "2026-03-17 20:13:16", 5)
    assert a == b


def test_upgrade_unique_id_is_stable_and_resolution_specific():
    url, pub = "https://1.ruchime.org/release/x/episode-1", "2026-10-20 12:00:00"
    base = ids.media_unique_id(url, pub, 3)
    hd = ids.upgrade_unique_id(url, pub, 3, 1080)
    assert hd == ids.upgrade_unique_id(url, pub, 3, 1080)
    assert len({base, hd, ids.upgrade_unique_id(url, pub, 3, 2160)}) == 3
