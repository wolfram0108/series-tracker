"""Stored voice-over groups reach the matching: from the stored value to the group a name belongs to (law Z-6)."""
from modules.voices.identity import NO_GROUPS, Groups, usable_groups
from modules.voices.settings import parse

STORED = {"voices.groups": '{"Studio One": ["S1 Team", "Studio 1"]}'}
NAMES = ["Studio 1", "Other Crew", "S1 Team", "Studio One", "studio-one"]


def groups_of(values):
    return [parse(values).voices.group_of(name) for name in NAMES]


def test_a_stored_group_holds_every_spelling_the_user_listed_and_its_own_name():
    assert groups_of(STORED) == ["Studio One", None, "Studio One", "Studio One", "Studio One"]


def test_without_the_group_the_same_spellings_belong_to_nothing_and_are_not_joined():
    assert groups_of({}) == [None] * len(NAMES)


def test_a_group_without_triggers_holds_its_own_name_alone():
    assert groups_of({"voices.groups": '{"Studio One": []}'}) == [None, None, None, "Studio One", "Studio One"]


def test_a_credit_that_names_two_studios_is_one_name_and_belongs_to_the_group_that_lists_it_whole():
    values = {"voices.groups": '{"Studio One": ["S1 Team"], "Crew": ["Other Crew"]}'}
    assert parse(values).voices.group_of("S1 Team / Other Crew") is None
    values = {"voices.groups": '{"Studio One": ["S1 Team"], "Pair": ["S1 Team / Other Crew"]}'}
    assert parse(values).voices.group_of("S1 Team / Other Crew") == "Pair"
    assert parse(values).voices.group_of("S1 Team") == "Studio One"


def test_a_name_with_nothing_to_compare_belongs_to_no_group():
    groups = Groups.of({"Studio One": ["S1"]})
    assert groups.group_of("") is None and groups.group_of(" - ") is None
    assert NO_GROUPS.group_of("Studio One") is None


def test_data_that_is_not_a_set_of_named_lists_is_not_trusted():
    assert usable_groups(["not", "a", "mapping"]) == {}
    assert usable_groups({"a": ["b", 5, ""], "": ["x"], "c": "nope", 7: ["y"]}) == {"a": ["b"]}


def test_two_names_are_compared_through_their_groups():
    players = parse({"voices.player_groups": '{"cvh": ["vk"]}'}).players
    assert players.canonical("VK") == players.canonical("cvh") == "cvh"
    assert players.canonical("Kodik") == "kodik"
