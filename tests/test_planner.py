"""The plan of a series (modules/scan/planner.py): a pure function that gives the status of every row from the winner of
each slot. The winner is the reference the choice made (tests/test_scan_reference.py); the planner only follows it.
"""
from modules.scan.planner import plan_with_supersession


def _item(unique_id, start, season=1, part=None, end=None, **fields):
    return {"unique_id": unique_id, "episode_start": start, "episode_end": end, "season": season, "part": part,
            **fields}


def test_the_winner_of_a_slot_is_in_the_plan_and_the_rest_of_the_slot_is_displaced_by_it():
    rows = [_item("lo", 1), _item("hi", 1), _item("other", 2)]
    statuses, superseded = plan_with_supersession(rows, {(1, 1, None): "hi", (1, 2, None): "other"})
    assert statuses == {"hi": "in_plan_single", "lo": "replaced", "other": "in_plan_single"}
    assert superseded == {"lo": "hi"}


def test_the_planner_does_not_choose_the_winner_by_itself():
    """A row of a lower resolution that the choice named wins: the resolution is not looked at here."""
    rows = [_item("lo", 1, resolution=480), _item("hi", 1, resolution=2160)]
    statuses, _ = plan_with_supersession(rows, {(1, 1, None): "lo"})
    assert statuses == {"lo": "in_plan_single", "hi": "replaced"}


def test_seasons_are_separate_slots():
    rows = [_item("s1", 1, season=1), _item("s2", 1, season=2)]
    statuses, superseded = plan_with_supersession(rows, {(1, 1, None): "s1", (2, 1, None): "s2"})
    assert statuses == {"s1": "in_plan_single", "s2": "in_plan_single"} and superseded == {}


def test_parts_of_an_episode_are_slots_of_their_own_and_both_stay_in_the_plan():
    rows = [_item("p1", 8, part=1), _item("p2", 8, part=2), _item("p1b", 8, part=1)]
    statuses, superseded = plan_with_supersession(rows, {(1, 8, 1): "p1", (1, 8, 2): "p2"})
    assert statuses == {"p1": "in_plan_single", "p2": "in_plan_single", "p1b": "replaced"}
    assert superseded == {"p1b": "p1"}


def test_a_part_given_as_text_is_the_same_slot_as_the_number():
    rows = [_item("a", 8, part="2"), _item("b", 8, part=2)]
    statuses, _ = plan_with_supersession(rows, {(1, 8, 2): "b"})
    assert statuses == {"a": "replaced", "b": "in_plan_single"}


def test_a_row_in_a_slot_nobody_holds_is_not_needed():
    statuses, superseded = plan_with_supersession([_item("left", 3)], {})
    assert statuses == {"left": "redundant"} and superseded == {}


def test_a_compilation_is_never_in_the_plan_and_displaces_nothing():
    rows = [_item("comp", 1, end=3), _item("single", 1)]
    statuses, superseded = plan_with_supersession(rows, {(1, 1, None): "single"})
    assert statuses == {"comp": "redundant", "single": "in_plan_single"} and superseded == {}


def test_a_row_the_user_switched_off_gets_no_status_and_holds_nothing():
    off = _item("off", 1, is_ignored_by_user=1)
    statuses, superseded = plan_with_supersession([off, _item("old", 1)], {(1, 1, None): "off"})
    assert statuses == {"old": "redundant"} and superseded == {}


def test_a_switched_off_season_gets_no_status():
    rows = [_item("a", 1, season=2), _item("b", 1, season=1)]
    statuses, _ = plan_with_supersession(rows, {(1, 1, None): "b", (2, 1, None): "a"}, ignored_seasons={2})
    assert statuses == {"b": "in_plan_single"}


def test_no_winners_no_rows():
    assert plan_with_supersession([], {}) == ({}, {})
