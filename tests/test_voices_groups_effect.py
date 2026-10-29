"""Stored voice-over groups reach the matching: from the stored value to the key a voice is kept under."""
from modules.voices import VoicesModule
from modules.voices.identity import NO_RULES, group_key, voice_groups
from modules.voices.settings import parse

STORED = {"voices.groups": '{"Studio One": ["S1 Team", "Studio 1"]}'}
NAMES = ["Studio 1", "Other Crew", "S1 Team", "Studio One"]


def test_a_stored_group_makes_every_spelling_of_a_studio_one_voice():
    rules = parse(STORED).rules
    assert voice_groups(NAMES, rules) == [[0, 2, 3], [1]]
    assert len({group_key([NAMES[index]], rules) for index in (0, 2, 3)}) == 1


def test_without_the_group_the_same_spellings_are_separate_voices():
    assert voice_groups(NAMES, parse({}).rules) == [[0], [1], [2], [3]]


def test_a_group_without_triggers_changes_nothing():
    assert voice_groups(NAMES, parse({"voices.groups": '{"Studio One": []}'}).rules) == [[0], [1], [2], [3]]


def test_a_credit_that_names_two_studios_joins_through_the_group():
    names = ["S1 Team / Other Crew", "Studio One", "Other Crew"]
    assert voice_groups(names, parse(STORED).rules) == [[0, 1, 2]]
    assert voice_groups(names, NO_RULES) == [[0, 2], [1]]


def test_a_voice_keeps_the_key_its_rows_were_recorded_under_when_a_group_joins_its_spellings():
    rules = parse(STORED).rules
    rows = [{"id": 7, "voice_key": group_key(["S1 Team"], NO_RULES), "voice_label": "S1 Team"}]
    dubs = [{"key": group_key(["Studio One"], rules), "label": "Studio One"}]
    assert VoicesModule._adopted_keys(dubs, rows, rules) == {"studioone": "s1team"}
    assert VoicesModule._adopted_keys(dubs, rows, NO_RULES) == {"studioone": "studioone"}
