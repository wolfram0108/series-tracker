"""Tests of the settings editor of the voices: what a save may write, what the screen is shown."""
from pathlib import Path

import pytest

from core.contracts import VoiceSettingsDraft
from modules.gateway import SSE_MAP
from modules.voices import editor
from modules.voices.settings import DEFAULT_PRIORITY, parse


def draft(**changes) -> VoiceSettingsDraft:
    """A form whose groups are given a language, unless the test says otherwise."""
    groups = changes.get("voice_groups", {})
    languages = {name.strip(): "rus" for name in groups if name.strip()}
    return VoiceSettingsDraft(**{"priority": ["kodik", "cvh"], "voice_groups": {}, "voice_languages": languages, **changes})


def problems(form: VoiceSettingsDraft, player_groups=None) -> list[tuple[str, str]]:
    return [(each.field, each.detail.code) for each in editor.check(form, player_groups or {})]


def test_a_good_form_has_no_problems():
    assert problems(draft(voice_groups={"AniLibria": ["aniliberty", "Anilibria TV"], "Empty": []})) == []


@pytest.mark.parametrize("changes, expected", [
    ({"priority": []}, [("priority", "priority_empty")]),
    ({"priority": [" ", ""]}, [("priority", "priority_empty")]),
    ({"priority": ["Kodik", "kodik"]}, [("priority", "priority_duplicate")]),
    ({"priority": ["kodik", "nowhere"]}, [("priority", "priority_unknown_player")]),
])
def test_each_wrong_order_is_refused_with_its_own_code(changes, expected):
    assert problems(draft(**changes)) == expected


def test_a_player_the_stored_player_groups_mention_may_stand_in_the_order():
    groups = {"anilibria": ["aniliberty"]}
    assert problems(draft(priority=["kodik", "anilibria"]), groups) == []
    assert problems(draft(priority=["kodik", "Aniliberty"]), groups) == []
    assert problems(draft(priority=["kodik", "anilibria"])) == [("priority", "priority_unknown_player")]


def test_a_group_may_be_empty_and_a_row_left_blank_is_not_a_group():
    assert problems(draft(voice_groups={"AniLibria": [], "": [], "  ": ["  "]})) == []


def test_a_group_needs_a_name_when_it_has_triggers():
    assert problems(draft(voice_groups={"": ["x"]})) == [("voice_groups", "group_name_empty")]


def test_two_spellings_of_one_group_name_are_two_groups_too_many():
    assert problems(draft(voice_groups={"AniLibria": [], "Ani Libria": []})) == [("voice_groups", "group_duplicate")]


def test_a_trigger_that_is_only_another_spelling_of_the_group_name_is_refused():
    found = problems(draft(voice_groups={"RedHeadSound": ["Red Head Sound"]}))
    assert found == [("voice_groups", "trigger_is_group")]


def test_a_trigger_cannot_be_the_name_of_another_group():
    found = editor.check(draft(voice_groups={"a1": ["b1"], "b1": []}), {})
    assert [each.detail.code for each in found] == ["trigger_is_other_group"]
    assert found[0].detail.params == {"trigger": "b1", "group": "a1", "other": "b1"}


def test_a_trigger_cannot_belong_to_two_groups():
    found = editor.check(draft(voice_groups={"a1": ["x1"], "b1": ["X1"]}), {})
    assert [each.detail.code for each in found] == ["trigger_in_two_groups"]
    assert found[0].detail.params == {"trigger": "X1", "first": "a1", "second": "b1"}


def test_a_trigger_written_twice_in_one_group_is_kept_once():
    form = draft(voice_groups={"AniLibria": ["aniliberty", "AniLiberty", " aniliberty "]})
    assert problems(form) == [] and editor.clean(form).voice_groups == {"AniLibria": ["aniliberty"]}


def test_a_saved_form_is_stored_trimmed_without_blank_rows_and_only_these_three_values_are_written():
    clean = editor.clean(draft(priority=[" kodik ", "kodik", "cvh"], voice_groups={" AniLibria ": [" aniliberty ", ""], "": []},
                               voice_languages={" AniLibria ": "rus", "Gone": "jpn"}))
    assert clean.priority == ["kodik", "cvh"] and clean.voice_groups == {"AniLibria": ["aniliberty"]}
    assert editor.stored(clean) == {"voices.source_priority": '["kodik", "cvh"]', "voices.groups": '{"AniLibria": ["aniliberty"]}',
                                    "voices.languages": '{"AniLibria": "rus"}'}


def test_a_group_is_kept_without_a_language_until_it_is_given_one_and_only_a_known_one():
    assert problems(draft(voice_groups={"Jisu": []}, voice_languages={})) == []
    assert problems(draft(voice_groups={"Jisu": []}, voice_languages={"Jisu": "chi"})) == []
    for code in ("xx", "ukr"):
        assert problems(draft(voice_groups={"Jisu": []}, voice_languages={"Jisu": code})) == [
            ("voice_languages", "group_language_unknown")]


def test_the_language_of_a_group_is_read_back_and_shown_and_a_wrong_stored_one_is_refused():
    saved = draft(voice_groups={"Jisu": [], "Old": []}, voice_languages={"Jisu": "chi"})
    settings = parse(editor.stored(editor.clean(saved)))
    assert settings.voice_languages == {"Jisu": "chi"} and editor.view(settings).voice_languages == {"Jisu": "chi"}
    broken = parse({"voices.groups": '{"Jisu": []}', "voices.languages": '{"Jisu": "xx"}'})
    assert broken.voice_languages == {} and editor.view(broken).rejected == ["voice_languages"]


def test_what_the_editor_stores_is_read_back_by_the_module_as_it_was_written_even_an_empty_group():
    saved = draft(priority=["vibix", "kodik"], voice_groups={"AniLibria": ["aniliberty"], "Empty": []})
    settings = parse(editor.stored(editor.clean(saved)))
    assert settings.priority == ("vibix", "kodik") and settings.voice_groups == {"AniLibria": ["aniliberty"], "Empty": []}
    assert settings.problems == ()


def test_the_view_gives_the_order_to_return_to_and_the_refused_fields():
    shown = editor.view(parse({"voices.groups": "{broken"}))
    assert shown.rejected == ["voice_groups"] and shown.voice_groups == {}
    assert shown.default_priority == list(DEFAULT_PRIORITY) and shown.priority == list(DEFAULT_PRIORITY)
    assert not hasattr(shown, "players")


def test_the_settings_reach_every_open_screen_by_the_stream_and_the_screen_listens_for_it():
    root = Path(__file__).resolve().parent.parent
    name = SSE_MAP["voices.settings.changed"].name
    assert name == "voice_settings_update"
    assert f"`{name}`" in (root / "contracts" / "sse_contract.md").read_text(encoding="utf-8")
    assert f'"{name}"' in (root / "web" / "src" / "composables" / "useRealtime.ts").read_text(encoding="utf-8")
