"""Tests of the typed reading of the voices settings."""
from modules.voices.layout import DEFAULT_PRIORITY
from modules.voices.settings import parse


def test_nothing_set_means_the_defaults_and_no_problems():
    settings = parse({})
    assert settings.priority == tuple(DEFAULT_PRIORITY)
    assert settings.max_temp_episodes == 0 and settings.problems == ()


def test_good_values_are_taken_as_they_are():
    settings = parse({"voices.source_priority": '["kodik", "cvh"]',
                      "voices.max_temp_episodes": "3", "voices.groups": '{"Redhead": ["Red"]}'})
    assert settings.priority == ("kodik", "cvh")
    assert settings.max_temp_episodes == 3 and settings.rules.voices == {"red": "redhead"}
    assert settings.problems == ()


def test_each_wrong_value_falls_back_to_its_default_and_is_named():
    settings = parse({"voices.source_priority": '{"a": 1}',
                      "voices.max_temp_episodes": "-2", "voices.groups": "{broken",
                      "voices.player_groups": '["a"]'})
    assert settings.priority == tuple(DEFAULT_PRIORITY)
    assert settings.max_temp_episodes == 0 and settings.rules.voices == {} and settings.rules.players == {}
    named = " ".join(settings.problems)
    for key in ("source_priority", "max_temp_episodes", "voices.groups", "player_groups"):
        assert key in named
    assert len(settings.problems) == 4


def test_a_number_that_is_not_a_number_is_named_not_swallowed():
    assert "max_temp_episodes" in parse({"voices.max_temp_episodes": "many"}).problems[0]


def test_groups_without_a_name_or_a_list_and_names_that_are_not_text_are_dropped_and_counted():
    settings = parse({"voices.groups": '{"a": ["b", 5, ""], "": ["x"], "c": "nope"}'})
    assert settings.voice_groups == {"a": ["b"]} and settings.rules.voices == {"b": "a"}
    assert "отброшено записей: 5" in settings.problems[0]


def test_the_groups_are_kept_as_spelled_and_every_trigger_points_to_its_group():
    settings = parse({"voices.groups": '{"AniLibria": ["aniliberty", "Anilibria TV"]}',
                      "voices.player_groups": '{"cvh": ["vk"]}'})
    assert settings.voice_groups == {"AniLibria": ["aniliberty", "Anilibria TV"]}
    assert settings.rules.voices == {"aniliberty": "anilibria", "anilibriatv": "anilibria"}
    assert settings.player_groups == {"cvh": ["vk"]} and settings.rules.player("VK") == "cvh"
