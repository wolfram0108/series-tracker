"""The rows of the output — the window «Выход», its plan and the planner: one way in, the table, the rules of the
constructor, the voice-over groups (ЭТАП_10:107). A row the groups did not take is in none of it."""
from pydantic import TypeAdapter

from core import native
from core.contracts import CompositionRow, RowState
from core.contracts.availability import lines_of_level
from core.plan.inputs import online_of
from modules.composition.project import UNKNOWN_VOICE_LABEL, season_orders, season_voices

UNKNOWN = UNKNOWN_VOICE_LABEL.format(number=1)


def line(key: str, episode: int | None, group: str | None, **fields) -> CompositionRow:
    return CompositionRow(**{"series_id": 1, "source_id": 1, "row_key": key, "season": 1 if episode else None,
                             "episode_start": episode, "episode_end": episode, "voice_group": group,
                             "voice_label": group or UNKNOWN, "voice_unknown": None if group else 1, "player": "vk",
                             "heights": [2160 if group is None else 720], "state": RowState.PLAYABLE, **fields})


ROWS = [line("ref", 1, "AniStar"), line("raw", 1, None), line("cut", 1, "AniStar", excluded=True, season=None,
                                                                     episode_start=None, episode_end=None)]


def test_a_row_is_of_the_output_when_the_rules_numbered_it_and_a_group_took_it():
    assert [each.in_output for each in ROWS] == [True, False, False]
    assert ROWS[0].model_dump()["in_output"] is True  # the window is told it with the row, it does not work it out


def test_a_row_no_group_took_reaches_neither_the_planner_nor_the_levels_of_the_window_however_high_it_is():
    assert [row.id.row_key for row in online_of(ROWS)] == ["ref"]
    assert [row.row_key for row in lines_of_level(ROWS, 1, 1)] == ["ref"]


def test_the_order_of_the_tracks_of_a_season_has_no_unknown_voice_over_and_keeps_a_group_that_has_no_row_now():
    layout = native.Rows.from_json(TypeAdapter(list[CompositionRow]).dump_json(ROWS))
    orders = season_orders({1: [UNKNOWN, "Ушедшая группа", "AniStar"]}, layout, {})
    assert orders == {1: ["Ушедшая группа", "AniStar"]}
    assert [span.label for each in season_voices(orders, layout) for span in each.voices] == ["Ушедшая группа", "AniStar"]
