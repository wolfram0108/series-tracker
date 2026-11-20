"""Tests of the settings editor of the voices: what a save may write, what the screen is shown."""


from core.contracts import VoiceSettingsDraft
from modules.voices import editor


def draft(**changes) -> VoiceSettingsDraft:
    """A form whose groups are given a language, unless the test says otherwise."""
    groups = changes.get("voice_groups", {})
    languages = {name.strip(): "rus" for name in groups if name.strip()}
    return VoiceSettingsDraft(**{"priority": ["kodik", "cvh"], "voice_groups": {}, "voice_languages": languages, **changes})


def problems(form: VoiceSettingsDraft, player_groups=None, kept=()) -> list[tuple[str, str]]:
    return [(each.field, each.detail.code) for each in editor.check(form, player_groups or {}, kept=kept)]


def test_a_trigger_cannot_belong_to_two_groups():
    found = editor.check(draft(voice_groups={"a1": ["x1"], "b1": ["X1"]}), {})
    assert [each.detail.code for each in found] == ["trigger_in_two_groups"]
    assert found[0].detail.params == {"trigger": "X1", "first": "a1", "second": "b1"}
