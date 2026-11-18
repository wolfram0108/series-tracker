"""The names and groups of voice-overs and players (core/voice_names.py): one form to compare, one order of players."""
import pytest

from core.voice_names import NO_GROUPS, Groups, fold_name, rank, usable_groups


@pytest.mark.parametrize("name, folded", [("Red Head Sound", "redheadsound"), ("  AniStar ", "anistar"),
                                          ("Студия Константина Исаева", "студияконстантинаисаева"), ("A / B", "ab")])
def test_a_name_folds_without_case_spaces_and_punctuation(name, folded):
    assert fold_name(name) == folded


def test_a_name_belongs_to_the_group_whose_name_or_trigger_it_equals():
    groups = Groups.of({"RuChiMe": ["Animy", "AniMy / RuChiMe"], "AniStar": ["ani star"]})
    assert groups.group_of("ruchime") == "RuChiMe"
    assert groups.group_of("ANIMY") == "RuChiMe"
    assert groups.group_of("Ani Star") == "AniStar"
    assert groups.group_of("Unknown") is None
    assert groups.group_of("") is None


def test_a_combined_credit_joins_the_group_of_neither_of_its_parts():
    groups = Groups.of({"A": ["a"], "B": ["b"]})
    assert groups.group_of("A / B") is None


def test_a_group_name_outranks_the_trigger_of_another_group():
    groups = Groups.of({"first": ["second"], "second": []})
    assert groups.group_of("second") == "second"


def test_data_that_is_not_a_group_is_not_trusted():
    assert usable_groups("x") == {}
    assert usable_groups({"": ["a"], "ok": ["b", 3, ""], 5: ["c"]}) == {"ok": ["b"]}


def test_a_player_takes_the_place_of_its_name_or_of_its_group_and_an_unknown_one_comes_last():
    groups = Groups.of({"mirror": ["cvh", "kodik"]})
    order = ["vk", "mirror", "allvideo"]
    assert [rank(p, order, groups) for p in ("vk", "cvh", "kodik", "allvideo", "other")] == [0, 1, 1, 2, 3]
    assert [rank(p, ["vk", "cvh"], NO_GROUPS) for p in ("vk", "cvh", "kodik")] == [0, 1, 2]


def test_every_release_of_every_page_takes_the_one_place_of_the_torrent_and_its_group():
    """A release is a line of one page (`torrent:<release>`): the order of the settings names the torrent once."""
    order = ["vk", "torrent", "kodik"]
    assert [rank(p, order) for p in ("torrent:page", "torrent:1080p • AVC", "torrent:page#DIR", "kodik")] == [1, 1, 1, 2]
    assert rank("torrent:page", ["vk", "kodik"]) == 2  # not in the order: last, as any player
    assert rank("torrent:page", ["vk", "files", "kodik"], Groups.of({"files": ["torrent"]})) == 1
    assert rank("line:NBY", ["line", "line:NBY"]) == 1  # a line of the provider is its own place
