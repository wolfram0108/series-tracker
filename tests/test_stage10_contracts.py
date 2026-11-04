"""The contracts of the multi-source series: the line the constructor gets, the levels of a lever, the migration chain."""
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError

from core.contracts import (CompositionRow, Level, ReleaseTable, RowState, RowVoice, SettingRecord, TableRow,
                            group_target, line_of, parse_variant_target, variant_target)


def table(*names: str) -> ReleaseTable:
    return ReleaseTable(url="https://example.org/page", site="ruchime", names=list(names), complete=True)


def row(**fields) -> TableRow:
    return TableRow(**{"key": "k", "player": "vk", "state": RowState.PLAYABLE, **fields})


def test_a_line_has_the_fields_in_the_fixed_order_each_with_its_code_and_the_name_and_text_without_one():
    line = line_of(table("Асура: Безумный король 2"),
                   row(player="vibix", player_label="Vibix", voice=RowVoice(name="Ruchime"), group="Сезон 2",
                       heights=[480, 720, 1080], text="Серия 1"))
    assert line == "PID:example.org | P:Vibix | Асура: Безумный король 2 | Серия 1 | G:Сезон 2 | V:Ruchime | R:1080p"


def test_a_missing_field_is_left_out_with_its_separator():
    line = line_of(table(), row(player_label="VK", heights=[2160], text="Непревзойденный царь небес - 525 серия 3 сезон (653 эпизод)"))
    assert line == "PID:example.org | P:VK | Непревзойденный царь небес - 525 серия 3 сезон (653 эпизод) | R:2160p"


def test_the_type_of_the_voice_is_the_one_the_player_names():
    line = line_of(table("Практикуя ци сто тысяч лет"),
                   row(player="kodik", player_label="Kodik", voice=RowVoice(name="Студия Константина Исаева", kind="voice"),
                       group="1 сезон", heights=[720], text="1-3 серия"))
    assert line == "PID:example.org | P:Kodik | Практикуя ци сто тысяч лет | 1-3 серия | G:1 сезон | V:Студия Константина Исаева | VT:voice | R:720p"


def test_the_text_of_a_cvh_row_is_given_by_the_player_and_taken_as_it_is():
    line = line_of(table("Практикуя ци сто тысяч лет"),
                   row(player="cvh", player_label="CVH", voice=RowVoice(name="AniStar", kind="Многоголосый"),
                       group="1 сезон", heights=[480, 1080], text="5 серия"))
    assert line == "PID:example.org | P:CVH | Практикуя ци сто тысяч лет | 5 серия | G:1 сезон | V:AniStar | VT:Многоголосый | R:1080p"


@pytest.mark.parametrize("state", [RowState.ABSENT, RowState.NOT_OBTAINED])
def test_a_row_that_does_not_play_has_no_resolution_in_its_line(state):
    assert line_of(table(), row(player_label="VK", state=state, heights=[720], text="Серия 3")) == "PID:example.org | P:VK | Серия 3"


def test_the_player_stands_in_for_a_missing_label():
    assert line_of(table(), row(player="allvideo", text="Серия 3")) == "PID:example.org | P:allvideo | Серия 3"


def test_a_row_takes_no_field_the_contract_does_not_know():
    with pytest.raises(ValidationError):
        row(episode=3)


def test_a_compilation_is_a_range_that_spans_more_than_one_episode():
    base = dict(series_id=1, source_id=1, row_key="k", player="kodik", state=RowState.PLAYABLE, season=1)
    assert CompositionRow(**base, episode_start=1, episode_end=3).is_compilation
    assert not CompositionRow(**base, episode_start=2, episode_end=2).is_compilation
    assert not CompositionRow(**base, episode_start=2).is_compilation


def test_the_level_of_a_record_follows_the_numbers_it_names():
    assert SettingRecord(series_id=1, lever="reference.choice").level == Level.SERIES
    assert SettingRecord(series_id=1, season=2, lever="reference.choice").level == Level.SEASON
    assert SettingRecord(series_id=1, season=2, episode=34, lever="reference.choice").level == Level.EPISODE


def test_an_episode_is_set_within_a_season():
    with pytest.raises(ValidationError):
        SettingRecord(series_id=1, episode=34, lever="reference.choice")


def test_targets_name_domain_things_not_row_ids():
    assert variant_target("Ruchime", 3, "vibix") == 'variant:["Ruchime",3,"vibix"]'
    assert group_target("Ruchime") == "group:Ruchime"
    # No name, however odd, is taken for a part of another: the group may hold the separators of any other form.
    for group, source_id, player in [("A / B", 3, "a/b"), ('Q"uote', 0, "p"), ("group:x", 1, "source:1/player:z"), ("Ё", 2, "я")]:
        assert parse_variant_target(variant_target(group, source_id, player)) == (group, source_id, player)


def test_the_migrations_of_the_stage_keep_one_head_in_order():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert len(script.get_heads()) == 1
    chain = [script.get_revision(f"{number:04d}").down_revision for number in range(25, 30)]
    assert chain == ["0024", "0025", "0026", "0027", "0028"]
