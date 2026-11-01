"""The classifications of source types in core: the type of a source, and the profile of the torrent source of a series."""
from core import is_video_source, torrent_profile_id


def _source(source_type, profile_id=None, source_id=1):
    return {"id": source_id, "series_id": 1, "position": 0, "url": "u", "source_type": source_type,
            "parser_profile_id": profile_id}


def test_vk_and_web_sources_are_video_ones_and_a_torrent_is_not():
    assert is_video_source("vk_video") and is_video_source("web_video")
    assert not is_video_source("torrent")
    assert not is_video_source(None)


def test_the_profile_is_the_one_of_the_torrent_source():
    assert torrent_profile_id({"sources": [_source("torrent", 5)]}) == 5
    assert torrent_profile_id({"sources": [_source("web_video", 7, 1), _source("torrent", 5, 2)]}) == 5


def test_no_torrent_source_or_no_profile_is_no_profile():
    assert torrent_profile_id({"sources": [_source("torrent")]}) is None
    assert torrent_profile_id({"sources": [_source("vk_video", 3)]}) is None
    assert torrent_profile_id({"sources": []}) is None
    assert torrent_profile_id({}) is None
