"""Tests of the settings editor of the voices: what a save may write, what a draft of the rules groups."""
import pytest

from core.contracts import GroupsDraft, VoiceSettingsDraft
from modules.voices import editor
from modules.voices.layout import DEFAULT_PRIORITY
from modules.voices.settings import DEFAULT_TEMP_ROOT, parse


def draft(**changes) -> VoiceSettingsDraft:
    fields = {"priority": ["kodik", "cvh"], "voice_aliases": {}, "player_aliases": {},
              "temp_root": "/data/tmp", "max_temp_episodes": 0}
    return VoiceSettingsDraft(**{**fields, **changes})


def codes(found) -> list[tuple[str, str]]:
    return [(problem.field, problem.detail.code) for problem in found]


def test_a_good_form_has_no_problems():
    assert editor.check(draft(voice_aliases={"Redhead": "Red Head Studio"}, player_aliases={"aniliberty": "anilibria"})) == []


def test_an_empty_form_value_that_means_the_default_is_allowed():
    assert editor.check(draft(temp_root="")) == []


@pytest.mark.parametrize("changes, expected", [
    ({"priority": []}, [("priority", "priority_empty")]),
    ({"priority": [" ", ""]}, [("priority", "priority_empty")]),
    ({"priority": ["Kodik", "kodik"]}, [("priority", "priority_duplicate")]),
    ({"priority": ["kodik", "nowhere"]}, [("priority", "priority_unknown_player")]),
    ({"temp_root": "relative/path"}, [("temp_root", "temp_root_not_absolute")]),
    ({"max_temp_episodes": -1}, [("max_temp_episodes", "max_temp_negative")]),
])
def test_each_wrong_value_is_refused_with_its_own_code(changes, expected):
    assert codes(editor.check(draft(**changes))) == expected


def test_two_spellings_of_one_name_need_no_rule():
    assert codes(editor.check(draft(voice_aliases={"RedHeadSound": "Red Head Sound"}))) == [("voice_aliases", "alias_self")]


def test_a_pair_with_one_name_missing_is_refused_and_a_blank_row_is_not():
    assert codes(editor.check(draft(voice_aliases={"Redhead": "", "": "", "  ": " "}))) == [("voice_aliases", "alias_incomplete")]


def test_a_name_given_two_meanings_is_refused():
    found = editor.check(draft(voice_aliases={"Redhead": "Studio A", "REDHEAD": "Studio B"}))
    assert codes(found) == [("voice_aliases", "alias_conflict")]


def test_a_chain_of_pairs_is_refused_because_a_pair_is_resolved_in_one_step():
    found = editor.check(draft(player_aliases={"a1": "b1", "b1": "c1"}))
    assert codes(found) == [("player_aliases", "alias_chain")]
    assert found[0].detail.params == {"alias": "a1", "canonical": "b1", "target": "c1"}


def test_a_saved_form_is_stored_trimmed_without_blank_rows_and_with_a_plain_path():
    clean = editor.clean(draft(priority=[" kodik ", "kodik", "cvh"], voice_aliases={" Redhead ": " Studio ", "": ""},
                               temp_root=" /data//tmp/ "))
    assert clean.priority == ["kodik", "cvh"] and clean.voice_aliases == {"Redhead": "Studio"}
    assert clean.temp_root == "/data/tmp"
    assert editor.stored(clean) == {
        "voices.source_priority": '["kodik", "cvh"]', "voices.aliases": '{"Redhead": "Studio"}',
        "voices.player_aliases": "{}", "voices.temp_root": "/data/tmp", "voices.max_temp_episodes": "0"}


def test_what_the_editor_stores_is_read_back_by_the_module_as_it_was_written():
    saved = draft(priority=["vibix", "kodik"], voice_aliases={"Redhead": "Studio"}, temp_root="/data/x", max_temp_episodes=4)
    settings = parse(editor.stored(editor.clean(saved)))
    assert settings.priority == ("vibix", "kodik") and settings.voice_aliases == {"Redhead": "Studio"}
    assert settings.temp_root == "/data/x" and settings.max_temp_episodes == 4 and settings.problems == ()


def test_an_empty_path_is_stored_and_read_as_the_default():
    assert parse(editor.stored(editor.clean(draft(temp_root="")))).temp_root == DEFAULT_TEMP_ROOT


def test_the_view_gives_the_defaults_the_refused_fields_and_the_known_names():
    view = editor.view(parse({"voices.aliases": "{broken", "voices.temp_root": "relative"}), ["BlackCat"])
    assert sorted(view.rejected) == ["temp_root", "voice_aliases"] and view.known_voices == ["BlackCat"]
    assert view.defaults.priority == list(DEFAULT_PRIORITY) and view.defaults.temp_root == DEFAULT_TEMP_ROOT
    assert view.priority == list(DEFAULT_PRIORITY) and view.voice_aliases == {}


def test_a_draft_of_the_rules_groups_names_before_it_is_saved():
    names = ["RedHeadSound", "Red Head Sound", "Redhead", "BlackCat"]
    without = editor.preview(GroupsDraft(names=names, voice_aliases={}, player_aliases={}))
    assert [group.members for group in without.groups] == [["RedHeadSound", "Red Head Sound"], ["Redhead"], ["BlackCat"]]
    with_rule = editor.preview(GroupsDraft(names=names, voice_aliases={"Redhead": "Red Head Sound"}, player_aliases={}))
    assert [group.members for group in with_rule.groups] == [["RedHeadSound", "Red Head Sound", "Redhead"], ["BlackCat"]]
    assert with_rule.problems == []


def test_the_preview_names_the_faults_of_the_draft_and_ignores_blank_and_repeated_names():
    found = editor.preview(GroupsDraft(names=["A", " ", "A", "B"], voice_aliases={"x": "x"}, player_aliases={}))
    assert [group.members for group in found.groups] == [["A"], ["B"]]
    assert codes(found.problems) == [("voice_aliases", "alias_self")]


def test_a_player_the_rules_mention_may_stand_in_the_order_even_if_it_is_not_built_in():
    assert editor.check(draft(priority=["kodik", "anilibria"], player_aliases={"aniliberty": "anilibria"})) == []
    assert editor.check(draft(priority=["kodik", "Aniliberty"], player_aliases={"aniliberty": "anilibria"})) == []


def test_the_settings_reach_every_open_screen_by_the_stream_and_the_screen_listens_for_it():
    from pathlib import Path
    from modules.gateway import SSE_MAP
    root = Path(__file__).resolve().parent.parent
    name, _ = SSE_MAP["voices.settings.changed"]
    assert name == "voice_settings_update"
    assert f"`{name}`" in (root / "contracts" / "sse_contract.md").read_text(encoding="utf-8")
    assert f'"{name}"' in (root / "web" / "src" / "composables" / "useRealtime.ts").read_text(encoding="utf-8")
