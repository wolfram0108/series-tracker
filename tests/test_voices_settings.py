"""Tests of the typed reading of the voices settings."""
from modules.voices.settings import DEFAULT_PRIORITY, parse


def test_each_wrong_value_falls_back_to_its_default_and_is_named():
    settings = parse({"voices.source_priority": '{"a": 1}',
                      "voices.max_temp_episodes": "-2", "voices.groups": "{broken",
                      "voices.player_groups": '["a"]'})
    assert settings.priority == tuple(DEFAULT_PRIORITY)
    assert settings.max_temp_episodes == 0 and settings.voices.index == {} and settings.players.index == {}
    named = " ".join(settings.problems)
    for key in ("source_priority", "max_temp_episodes", "voices.groups", "player_groups"):
        assert key in named
    assert len(settings.problems) == 4
