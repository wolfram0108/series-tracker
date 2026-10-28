"""Tests of the typed reading of the voices settings."""
from modules.voices.layout import DEFAULT_PRIORITY
from modules.voices.settings import DEFAULT_TEMP_ROOT, parse


def test_nothing_set_means_the_defaults_and_no_problems():
    settings = parse({})
    assert settings.priority == tuple(DEFAULT_PRIORITY) and settings.temp_root == DEFAULT_TEMP_ROOT
    assert settings.max_temp_episodes == 0 and settings.problems == ()


def test_good_values_are_taken_as_they_are():
    settings = parse({"voices.source_priority": '["kodik", "cvh"]', "voices.temp_root": "/data/tmp",
                      "voices.max_temp_episodes": "3", "voices.aliases": '{"Red": "Redhead"}'})
    assert settings.priority == ("kodik", "cvh") and settings.temp_root == "/data/tmp"
    assert settings.max_temp_episodes == 3 and settings.rules.voices == {"red": "redhead"}
    assert settings.problems == ()


def test_each_wrong_value_falls_back_to_its_default_and_is_named():
    settings = parse({"voices.source_priority": '{"a": 1}', "voices.temp_root": "relative/path",
                      "voices.max_temp_episodes": "-2", "voices.aliases": "{broken",
                      "voices.player_aliases": '["a"]'})
    assert settings.priority == tuple(DEFAULT_PRIORITY) and settings.temp_root == DEFAULT_TEMP_ROOT
    assert settings.max_temp_episodes == 0 and settings.rules.voices == {} and settings.rules.players == {}
    named = " ".join(settings.problems)
    for key in ("source_priority", "temp_root", "max_temp_episodes", "voices.aliases", "player_aliases"):
        assert key in named
    assert len(settings.problems) == 5


def test_a_number_that_is_not_a_number_is_named_not_swallowed():
    assert "max_temp_episodes" in parse({"voices.max_temp_episodes": "many"}).problems[0]


def test_alias_entries_that_are_not_pairs_of_names_are_dropped_and_counted():
    settings = parse({"voices.aliases": '{"a": "b", "c": 5, "": "d"}'})
    assert settings.rules.voices == {"a": "b"} and "отброшено записей: 2" in settings.problems[0]
