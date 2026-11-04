"""The choice of the reference (modules/scan/reference.py): a pure function, tested on rows of a layout with known answers.

M-18 (resolution, then the order of players, then the order of sources), M-19 (a compilation is never the reference),
M-21 (a pick on the series, a season or an episode, inheritance, `other_player`), M-22 (no candidate, nothing swapped
for the picked voice-over), the lever `variant.allow`, the strict order of the keys and the determinism of the answer.
"""
import random

import pytest

from core.contracts import (REFERENCE_CHOICE, VARIANT_ALLOW, Exclusion, Level, Reference, ReferenceChoice,
                            ReferenceMark, RowState, variant_target)
from core.reasons import parse
from core.voice_names import Groups
from modules.scan import reference
from modules.scan.reference import Candidate, Order
from scan_support import episode_settings, lever, row

S = 1  # the series
PRIORITY = ["vk", "allvideo", "vibix", "kodik", "cvh"]


def choose(rows, settings=(), priority=PRIORITY, sources=(1, 2, 3), **kwargs):
    return reference.choose(rows, settings, priority, sources, **kwargs)


def only(chosen):
    assert len(chosen) == 1
    return chosen[0]


def pick(group="Ruchime", source_id=1, player="vk", level=Level.SERIES):
    return lever(REFERENCE_CHOICE, ReferenceChoice(group=group, source_id=source_id, player=player).model_dump(),
                 level)


def deny(source_id, player, level=Level.SERIES, value=False, group="Ruchime"):
    return lever(VARIANT_ALLOW, value, level, variant_target(group, source_id, player))


# --- M-18: the best single row ----------------------------------------------------------------

def test_the_highest_resolution_wins():
    chosen = only(choose([row(S, 1, "a", 1, 1, player="vk", height=720),
                          row(S, 1, "b", 1, 1, player="vibix", height=1080),
                          row(S, 2, "c", 1, 1, player="kodik", height=480)]))
    assert chosen.row.row_key == "b"
    assert chosen.reference.mark == ReferenceMark.BY_LEVEL
    assert (chosen.reference.source_id, chosen.reference.row_key, chosen.reference.player,
            chosen.reference.height) == (1, "b", "vibix", 1080)


def test_at_an_equal_resolution_the_player_higher_in_the_order_wins():
    rows = [row(S, 1, "a", 1, 1, player="kodik", height=1080), row(S, 1, "b", 1, 1, player="vk", height=1080)]
    assert only(choose(rows, priority=["vk", "kodik"])).row.row_key == "b"
    assert only(choose(rows, priority=["kodik", "vk"])).row.row_key == "a"


def test_at_an_equal_resolution_and_player_the_source_earlier_in_the_series_wins():
    rows = [row(S, 2, "a", 1, 1, height=1080), row(S, 1, "b", 1, 1, height=1080)]
    assert only(choose(rows, sources=(1, 2))).row.row_key == "b"
    assert only(choose(rows, sources=(2, 1))).row.row_key == "a"


def test_a_player_the_settings_do_not_name_comes_after_every_one_they_name():
    rows = [row(S, 1, "a", 1, 1, player="newplayer", height=1080), row(S, 1, "b", 1, 1, player="cvh", height=1080)]
    assert only(choose(rows, priority=["vk", "cvh"])).row.row_key == "b"


def test_the_names_of_players_in_the_settings_are_read_without_case_and_spaces():
    rows = [row(S, 1, "a", 1, 1, player="vk", height=1080), row(S, 1, "b", 1, 1, player="kodik", height=1080)]
    assert only(choose(rows, priority=[" Kodik ", "VK"])).row.row_key == "b"


def test_players_the_user_put_into_one_group_take_one_place_in_the_order():
    rows = [row(S, 1, "a", 1, 1, player="cvh", height=1080), row(S, 1, "b", 1, 1, player="kodik", height=1080)]
    groups = Groups.of({"mirror": ["cvh", "kodik"]})
    # Alone, the order of the settings puts kodik first; with the two in one group they share a place and the
    # source decides, which is the same row either way, so the tie is broken by the place in the layout.
    assert only(choose(rows, priority=["kodik", "cvh"])).row.row_key == "b"
    assert only(choose(rows, priority=["mirror"], player_groups=groups)).row.row_key == "a"


def test_a_row_the_player_cannot_play_is_no_candidate():
    rows = [row(S, 1, "gone", 1, 1, height=2160, state=RowState.ABSENT),
            row(S, 1, "unsure", 1, 1, height=1440, state=RowState.NOT_OBTAINED),
            row(S, 1, "ok", 1, 1, height=720)]
    assert only(choose(rows)).row.row_key == "ok"


def test_a_row_with_no_height_ranks_below_a_row_with_one():
    rows = [row(S, 1, "blind", 1, 1, height=None), row(S, 1, "seen", 1, 1, height=360, player="cvh")]
    assert only(choose(rows)).row.row_key == "seen"


def test_every_episode_has_its_own_reference_in_the_order_of_season_and_episode():
    chosen = choose([row(S, 1, "e2", 1, 2), row(S, 1, "s2e1", 2, 1), row(S, 1, "e1", 1, 1)])
    assert [(each.reference.season, each.reference.episode, each.row.row_key) for each in chosen] == [
        (1, 1, "e1"), (1, 2, "e2"), (2, 1, "s2e1")]


def test_an_episode_with_no_row_in_the_layout_has_no_reference():
    assert choose([]) == []
    assert choose([row(S, 1, "x", 1, 1, excluded=True)]) == []


def test_a_reversed_range_is_neither_an_episode_nor_a_compilation():
    assert choose([row(S, 1, "garbage", 1, 201, 110)]) == []


# --- parts: a slot is (season, episode, part) --------------------------------------------------

def test_the_parts_of_an_episode_are_slots_of_their_own():
    chosen = choose([row(S, 1, "p2", 1, 8, part=2, height=1080), row(S, 1, "p1", 1, 8, part=1, height=720),
                     row(S, 2, "p1b", 1, 8, part=1, height=1080)])
    assert [(each.slot, each.row.row_key) for each in chosen] == [((1, 8, 1), "p1b"), ((1, 8, 2), "p2")]


def test_a_slot_without_a_part_comes_before_the_numbered_parts():
    chosen = choose([row(S, 1, "p1", 1, 8, part=1), row(S, 2, "whole", 1, 8)])
    assert [each.slot for each in chosen] == [(1, 8, None), (1, 8, 1)]


def test_a_part_that_only_has_rows_nobody_can_play_has_no_reference():
    chosen = choose([row(S, 1, "p1", 1, 8, part=1), row(S, 1, "p2", 1, 8, part=2, state=RowState.ABSENT)])
    assert [(each.slot, each.reference.mark) for each in chosen] == [
        ((1, 8, 1), ReferenceMark.BY_LEVEL), ((1, 8, 2), ReferenceMark.NONE)]


# --- M-19: a compilation is never the reference -------------------------------------------------

def test_an_episode_held_by_a_compilation_alone_is_unfit_and_has_no_row():
    chosen = choose([row(S, 1, "c", 1, 1, 3, height=2160)])
    assert [(each.slot, each.reference.mark, each.row) for each in chosen] == [
        ((1, 1, None), ReferenceMark.UNFIT, None), ((1, 2, None), ReferenceMark.UNFIT, None),
        ((1, 3, None), ReferenceMark.UNFIT, None)]
    assert parse(chosen[0].reference.reason)["code"] == "reference_unfit"
    assert chosen[0].reference.source_id is None and chosen[0].reference.row_key is None


def test_a_compilation_never_displaces_a_single_row_whatever_its_resolution():
    chosen = choose([row(S, 1, "c", 1, 1, 3, height=2160), row(S, 2, "e2", 1, 2, height=480)])
    assert [(each.reference.episode, each.reference.mark) for each in chosen] == [
        (1, ReferenceMark.UNFIT), (2, ReferenceMark.BY_LEVEL), (3, ReferenceMark.UNFIT)]
    assert chosen[1].row.row_key == "e2"


def test_a_compilation_nobody_can_play_does_not_make_an_episode_unfit():
    chosen = choose([row(S, 1, "c", 1, 1, 2, state=RowState.ABSENT)])
    assert [each.reference.mark for each in chosen] == [ReferenceMark.NONE, ReferenceMark.NONE]
    assert parse(chosen[0].reference.reason)["code"] == "reference_none"


def test_a_compilation_of_a_player_left_out_of_the_choice_does_not_make_an_episode_unfit():
    settings = [episode_settings(1, 1, deny(1, "vk")), episode_settings(1, 2, deny(1, "vk"))]
    chosen = choose([row(S, 1, "c", 1, 1, 2, height=1080, player="vk")], settings)
    assert [each.reference.mark for each in chosen] == [ReferenceMark.NONE, ReferenceMark.NONE]


@pytest.mark.parametrize("seed", range(20))
def test_whatever_the_layout_the_reference_is_a_playable_single_row_of_its_own_slot(seed):
    rng = random.Random(seed)
    rows = []
    for index in range(40):
        start = rng.randint(1, 6)
        end = start + rng.choice([0, 0, 0, 1, 2])
        rows.append(row(S, rng.randint(1, 3), f"r{index}", rng.choice([1, 2]), start, end,
                        part=rng.choice([None, None, 1, 2]), player=rng.choice(PRIORITY),
                        height=rng.choice([None, 360, 720, 1080, 2160]),
                        state=rng.choice([RowState.PLAYABLE, RowState.PLAYABLE, RowState.ABSENT])))
    for each in choose(rows):
        if each.row is None:
            assert each.reference.mark in (ReferenceMark.NONE, ReferenceMark.UNFIT)
            continue
        assert not each.row.is_compilation and each.row.state == RowState.PLAYABLE
        assert (each.row.season, each.row.episode_start, each.row.part) == each.slot


# --- the keys are strictly in order ---------------------------------------------------------------

@pytest.mark.parametrize("stronger, weaker", [
    (dict(height=1080, player="cvh"), dict(height=720, player="vk")),
    (dict(height=1080, player="cvh", source=3), dict(height=720, player="vk", source=1)),
    (dict(height=720, player="vk", source=3), dict(height=720, player="cvh", source=1)),
])
def test_a_key_is_never_overruled_by_a_key_after_it(stronger, weaker):
    rows = [row(S, weaker.get("source", 1), "weak", 1, 1, player=weaker["player"], height=weaker["height"]),
            row(S, stronger.get("source", 1), "strong", 1, 1, player=stronger["player"], height=stronger["height"])]
    assert only(choose(rows)).row.row_key == "strong"
    assert only(choose(list(reversed(rows)))).row.row_key == "strong"


def test_a_criterion_is_one_more_entry_of_the_list_of_keys():
    """M-34: the keys are one list; a new criterion joins it without a change to the choice."""
    def by_voice(candidate: Candidate, order: Order) -> int:
        return 0 if candidate.row.voice_label == "Wanted" else 1

    rows = [row(S, 1, "loud", 1, 1, height=1080, voice="Other"), row(S, 1, "wanted", 1, 1, height=1080, voice="Wanted")]
    assert only(choose(rows)).row.row_key == "loud"
    assert only(choose(rows, ranking=(reference.by_resolution, by_voice, reference.by_player))).row.row_key == "wanted"


# --- determinism ------------------------------------------------------------------------------------

def test_the_same_inputs_give_the_same_answer_whatever_the_order_the_rows_come_in():
    rows = [row(S, 1, "a", 1, 1, player="vk", height=720), row(S, 2, "b", 1, 1, player="kodik", height=1080),
            row(S, 1, "c", 1, 2, player="vibix", height=1080), row(S, 1, "d", 1, 2, player="vk", height=1080)]
    first = choose(rows)
    assert choose(rows) == first
    assert [(each.slot, each.row.row_key) for each in choose(list(reversed(rows)))] == [
        (each.slot, each.row.row_key) for each in first]


def test_rows_that_rank_the_same_are_told_apart_by_their_place_in_the_layout():
    rows = [row(S, 1, "first", 1, 1, height=1080), row(S, 1, "second", 1, 1, height=1080)]
    assert only(choose(rows)).row.row_key == "first"
    assert only(choose(list(reversed(rows)))).row.row_key == "second"


# --- M-21: the pick of the user, on a series, a season or an episode ------------------------------------

def test_a_pick_of_the_series_is_honoured_and_marked_by_the_level():
    rows = [row(S, 1, "vk", 1, 1, player="vk", height=1080, voice="Ruchime"),
            row(S, 2, "kodik", 1, 1, player="kodik", height=720, voice="AniLibria")]
    chosen = only(choose(rows, [episode_settings(1, 1, pick("AniLibria", 2, "kodik", Level.SERIES))]))
    assert chosen.row.row_key == "kodik" and chosen.reference.mark == ReferenceMark.BY_LEVEL


def test_a_pick_of_a_season_reaches_its_episodes_and_is_marked_by_the_level():
    rows = [row(S, 1, "a1", 1, 1, voice="Ruchime"), row(S, 2, "b1", 1, 1, voice="AniLibria", player="kodik")]
    chosen = only(choose(rows, [episode_settings(1, 1, pick("AniLibria", 2, "kodik", Level.SEASON))]))
    assert chosen.row.row_key == "b1" and chosen.reference.mark == ReferenceMark.BY_LEVEL


def test_a_pick_of_the_episode_itself_is_marked_own():
    rows = [row(S, 1, "a1", 1, 1, voice="Ruchime"), row(S, 2, "b1", 1, 1, voice="AniLibria", player="kodik")]
    chosen = only(choose(rows, [episode_settings(1, 1, pick("AniLibria", 2, "kodik", Level.EPISODE))]))
    assert chosen.row.row_key == "b1" and chosen.reference.mark == ReferenceMark.OWN


def test_an_episode_that_sets_the_auto_choice_itself_is_marked_own_too():
    rows = [row(S, 1, "a1", 1, 1)]
    chosen = only(choose(rows, [episode_settings(1, 1, lever(REFERENCE_CHOICE, None, Level.EPISODE))]))
    assert chosen.reference.mark == ReferenceMark.OWN


def test_an_episode_keeps_its_own_pick_when_the_season_picks_another():
    rows = [row(S, 1, "ruchime", 1, 34, voice="Ruchime"), row(S, 2, "ani", 1, 34, voice="AniLibria", player="kodik"),
            row(S, 1, "ruchime35", 1, 35, voice="Ruchime"), row(S, 2, "ani35", 1, 35, voice="AniLibria", player="kodik")]
    settings = [episode_settings(1, 34, pick("Ruchime", 1, "vk", Level.EPISODE)),
                episode_settings(1, 35, pick("AniLibria", 2, "kodik", Level.SEASON))]
    assert {each.reference.episode: each.row.row_key for each in choose(rows, settings)} == {
        34: "ruchime", 35: "ani35"}


def test_when_the_picked_player_has_no_row_the_next_player_of_the_same_voice_is_taken_and_marked():
    rows = [row(S, 1, "vk", 1, 1, player="vk", voice="AniLibria", height=720),
            row(S, 1, "vibix", 1, 1, player="vibix", voice="AniLibria", height=1080),
            row(S, 2, "other", 1, 1, player="allvideo", voice="Ruchime", height=2160)]
    chosen = only(choose(rows, [episode_settings(1, 1, pick("AniLibria", 2, "kodik", Level.EPISODE))]))
    assert chosen.row.row_key == "vibix"
    assert chosen.reference.mark == ReferenceMark.OTHER_PLAYER
    assert parse(chosen.reference.reason) == {"code": "reference_other_player", "params": {
        "taken": "vibix", "missing": "kodik", "taken_source": 1, "missing_source": 2}}


def test_when_the_picked_player_has_a_row_nobody_can_play_the_next_player_of_the_voice_is_taken():
    rows = [row(S, 2, "kodik", 1, 1, player="kodik", voice="AniLibria", state=RowState.NOT_OBTAINED),
            row(S, 1, "vk", 1, 1, player="vk", voice="AniLibria")]
    chosen = only(choose(rows, [episode_settings(1, 1, pick("AniLibria", 2, "kodik"))]))
    assert (chosen.row.row_key, chosen.reference.mark) == ("vk", ReferenceMark.OTHER_PLAYER)


def test_a_picked_voice_is_never_swapped_for_a_better_row_of_another_voice():
    rows = [row(S, 1, "loud", 1, 1, voice="Ruchime", height=2160),
            row(S, 2, "quiet", 1, 1, voice="AniLibria", player="kodik", height=360)]
    chosen = only(choose(rows, [episode_settings(1, 1, pick("AniLibria", 2, "kodik"))]))
    assert chosen.row.row_key == "quiet"


# --- M-22: nothing to be the reference -------------------------------------------------------------------

def test_an_episode_with_only_rows_nobody_can_play_has_no_reference_and_says_why():
    chosen = only(choose([row(S, 1, "gone", 1, 1, state=RowState.ABSENT)]))
    assert chosen.row is None and chosen.reference.mark == ReferenceMark.NONE
    assert parse(chosen.reference.reason) == {"code": "reference_none", "params": {}}


def test_a_voice_that_has_no_single_row_of_the_episode_leaves_it_without_a_reference_and_nothing_replaces_it():
    rows = [row(S, 1, "other", 1, 5, voice="Ruchime", height=2160)]
    chosen = only(choose(rows, [episode_settings(1, 5, pick("AniLibria", 2, "kodik", Level.EPISODE))]))
    assert chosen.row is None and chosen.reference.mark == ReferenceMark.NONE
    assert parse(chosen.reference.reason) == {"code": "reference_voice_missing", "params": {
        "voice": "AniLibria", "season": 1, "episode": 5}}


def test_a_picked_voice_that_only_a_compilation_holds_makes_the_episode_unfit():
    rows = [row(S, 1, "single", 1, 2, voice="Ruchime"), row(S, 2, "c", 1, 1, 2, voice="AniLibria", player="kodik")]
    chosen = choose(rows, [episode_settings(1, 2, pick("AniLibria", 2, "kodik"))])
    assert [(each.reference.episode, each.reference.mark) for each in chosen] == [
        (1, ReferenceMark.UNFIT), (2, ReferenceMark.UNFIT)]


def test_a_pick_of_a_voice_that_has_vanished_from_the_layout_is_not_hidden_by_the_best_row():
    """S-7: the pick of a row that is gone leaves the episode without a reference; the auto choice does not stand in."""
    rows = [row(S, 1, "left", 1, 1, voice="Ruchime")]
    chosen = only(choose(rows, [episode_settings(1, 1, pick("Gone", 3, "kodik"))]))
    assert chosen.reference.mark == ReferenceMark.NONE


# --- the lever variant.allow -----------------------------------------------------------------------------

def test_a_player_left_out_of_the_choice_gives_no_candidate():
    rows = [row(S, 1, "vk", 1, 1, player="vk", height=2160), row(S, 2, "kodik", 1, 1, player="kodik", height=720)]
    chosen = only(choose(rows, [episode_settings(1, 1, deny(1, "vk"))]))
    assert chosen.row.row_key == "kodik"


def test_a_player_is_left_out_for_its_own_source_only():
    rows = [row(S, 1, "one", 1, 1, player="vk", height=720), row(S, 2, "two", 1, 1, player="vk", height=2160)]
    assert only(choose(rows, [episode_settings(1, 1, deny(2, "vk"))])).row.row_key == "one"


def test_a_player_left_out_by_the_series_comes_back_when_the_episode_takes_it_in_again():
    rows = [row(S, 1, "vk", 1, 1, player="vk", height=2160), row(S, 2, "kodik", 1, 1, player="kodik", height=720)]
    # What the levers of an episode give is one value per target, from the nearest level that set it.
    chosen = only(choose(rows, [episode_settings(1, 1, deny(1, "vk", Level.EPISODE, value=True))]))
    assert chosen.row.row_key == "vk" and chosen.reference.mark == ReferenceMark.OWN


def test_a_setting_of_the_episode_about_participation_is_the_own_setting_of_the_episode():
    rows = [row(S, 1, "vk", 1, 1, player="vk"), row(S, 2, "kodik", 1, 1, player="kodik", height=2160)]
    assert only(choose(rows, [episode_settings(1, 1, deny(2, "kodik", Level.EPISODE))])).reference.mark \
        == ReferenceMark.OWN
    assert only(choose(rows, [episode_settings(1, 1, deny(2, "kodik", Level.SEASON))])).reference.mark \
        == ReferenceMark.BY_LEVEL


def test_a_picked_player_that_is_left_out_is_replaced_by_the_next_one_of_its_voice():
    rows = [row(S, 1, "vk", 1, 1, player="vk", voice="AniLibria"),
            row(S, 2, "kodik", 1, 1, player="kodik", voice="AniLibria")]
    chosen = only(choose(rows, [episode_settings(1, 1, pick("AniLibria", 2, "kodik"), deny(2, "kodik", group="AniLibria"))]))
    assert (chosen.row.row_key, chosen.reference.mark) == ("vk", ReferenceMark.OTHER_PLAYER)


def test_a_value_of_the_pick_that_is_not_one_is_the_auto_choice():
    rows = [row(S, 1, "vk", 1, 1)]
    chosen = only(choose(rows, [episode_settings(1, 1, lever(REFERENCE_CHOICE, {"group": "x"}, Level.SERIES))]))
    assert chosen.reference.mark == ReferenceMark.BY_LEVEL and chosen.row.row_key == "vk"


# --- the hold: an unsure answer is not a "no" (T-5) ------------------------------------------------------------------

def held(row_key="a", *, source_id=1, part=None, mark=ReferenceMark.BY_LEVEL, height=1080):
    return Reference(season=1, episode=1, part=part, mark=mark, source_id=source_id, row_key=row_key, player="vk",
                     height=height)


def unsure(key="a", **fields):
    return row(S, 1, key, 1, 1, state=RowState.NOT_OBTAINED, height=None, **fields)


def test_a_reference_whose_row_gave_no_answer_this_time_is_held():
    rows = [unsure("a"), row(S, 1, "b", 1, 1, player="kodik", height=720)]
    chosen = only(choose(rows, previous=[held()]))
    assert (chosen.row.row_key, chosen.reference.mark, chosen.reference.height) == ("a", ReferenceMark.BY_LEVEL, 1080)
    assert only(choose(rows)).row.row_key == "b"  # without the previous reference the other row is taken


def test_a_row_that_gave_no_answer_is_held_even_when_nothing_else_can_be_the_reference():
    chosen = only(choose([unsure("a")], previous=[held()]))
    assert chosen.row.row_key == "a" and chosen.reference.mark == ReferenceMark.BY_LEVEL


def test_the_hold_is_let_go_when_the_row_plays_again():
    rows = [row(S, 1, "a", 1, 1, player="vk", height=720), row(S, 1, "b", 1, 1, player="kodik", height=1080)]
    assert only(choose(rows, previous=[held()])).row.row_key == "b"


@pytest.mark.parametrize("gone", ["absent", "vanished"])
def test_the_hold_is_let_go_when_the_row_is_absent_or_has_left_the_layout(gone):
    others = [row(S, 1, "b", 1, 1, player="kodik", height=720)]
    rows = others + ([row(S, 1, "a", 1, 1, state=RowState.ABSENT)] if gone == "absent" else [])
    assert only(choose(rows, previous=[held()])).row.row_key == "b"


def test_the_hold_belongs_to_the_slot_of_the_row_and_to_no_other():
    rows = [unsure("a", part=2), row(S, 1, "b", 1, 1, player="kodik", height=720)]
    chosen = choose(rows, previous=[held()])  # the reference was in the slot without a part
    assert [(each.slot, each.row.row_key if each.row else None) for each in chosen] == [
        ((1, 1, None), "b"), ((1, 1, 2), None)]


def test_a_player_left_out_of_the_choice_is_not_held():
    rows = [unsure("a"), row(S, 2, "b", 1, 1, player="kodik", height=720)]
    chosen = only(choose(rows, [episode_settings(1, 1, deny(1, "vk"))], previous=[held()]))
    assert chosen.row.row_key == "b"


def test_a_pick_of_the_user_is_stronger_than_the_hold():
    rows = [unsure("a", voice="Ruchime"), row(S, 2, "b", 1, 1, player="kodik", voice="AniLibria", height=720)]
    chosen = only(choose(rows, [episode_settings(1, 1, pick("AniLibria", 2, "kodik"))], previous=[held()]))
    assert (chosen.row.row_key, chosen.reference.mark) == ("b", ReferenceMark.BY_LEVEL)


def test_the_held_reference_carries_the_mark_the_levers_of_the_episode_give_now():
    rows = [unsure("a")]
    assert only(choose(rows, [episode_settings(1, 1, lever(REFERENCE_CHOICE, None, Level.EPISODE))],
                       previous=[held(mark=ReferenceMark.BY_LEVEL)])).reference.mark == ReferenceMark.OWN
    assert only(choose(rows, previous=[held(mark=ReferenceMark.OWN)])).reference.mark == ReferenceMark.BY_LEVEL


def test_a_reference_held_once_is_held_again_and_stays_the_same():
    rows = [unsure("a"), row(S, 1, "b", 1, 1, player="kodik", height=720)]
    first = choose(rows, previous=[held()])
    again = choose(rows, previous=[each.reference for each in first])
    assert [each.reference for each in again] == [each.reference for each in first]


# --- the hold of a file on disk: a row a read did not bring is no "no"; a row the configuration took out is ------------

# Where the row of the previous reference "a" (vk, 720p, source 1) stands in the layout now.
PLACES = ("playable", "not_obtained", "absent", "rule", "source", "no_number", "elsewhere", "missing")
OTHERS = ("none", "worse", "better")  # the other candidate "b": none, kodik 720p (ranks below a), kodik 1080p
LEVERS = ("none", "pick_b", "deny_a")


def _a_row(place):
    if place == "missing":
        return []
    if place == "elsewhere":
        return [row(S, 1, "a", 1, 2, height=720)]  # the configuration numbers it as another episode
    if place in ("rule", "source", "no_number"):
        excluded = row(S, 1, "a", None, None, height=720, excluded=True)
        return [excluded.model_copy(update={"exclusion": Exclusion(place)})]
    state = {"playable": RowState.PLAYABLE, "not_obtained": RowState.NOT_OBTAINED, "absent": RowState.ABSENT}[place]
    return [row(S, 1, "a", 1, 1, height=720, state=state)]


def _b_row(other):
    height = {"worse": 720, "better": 1080}.get(other)
    return [] if height is None else [row(S, 2, "b", 1, 1, player="kodik", voice="AniLibria", height=height)]


def _levers(name):
    if name == "pick_b":
        return [episode_settings(1, 1, pick("AniLibria", 2, "kodik"))]
    if name == "deny_a":
        return [episode_settings(1, 1, deny(1, "vk"))]
    return []


def _expected(place, other, levers, disk, standing):
    """The law told on its own, not by the code: what stands for episode 1."""
    if levers == "pick_b":
        return "b" if other != "none" else None
    if levers == "none" and place == "not_obtained":
        return "a"
    if levers == "none" and place in ("missing", "no_number") and disk and standing:
        return "held"
    candidates = []
    if place == "playable" and levers != "deny_a":
        candidates.append(("a", 720, 0))
    if other != "none":
        candidates.append(("b", 1080 if other == "better" else 720, 1))
    return min(candidates, key=lambda each: (-each[1], each[2]))[0] if candidates else None


@pytest.mark.parametrize("standing", [True, False])
@pytest.mark.parametrize("disk", [True, False])
@pytest.mark.parametrize("levers", LEVERS)
@pytest.mark.parametrize("other", OTHERS)
@pytest.mark.parametrize("place", PLACES)
def test_a_downloaded_reference_is_held_only_while_the_layout_has_no_word_on_its_row(place, other, levers, disk,
                                                                                   standing):
    rows = _a_row(place) + _b_row(other)
    sources = (1, 2) if standing else (2,)
    previous = held(height=720).model_copy(update={"voice_group": "Ruchime"})
    chosen = choose(rows, _levers(levers), sources=sources, previous=[previous], on_disk={(1, "a")} if disk else set())
    first = next(each for each in chosen if each.slot == (1, 1, None)) if any(
        each.slot == (1, 1, None) for each in chosen) else None
    expected = _expected(place, other, levers, disk, standing)
    if expected == "held":
        assert first is not None and first.row is None
        assert (first.reference.source_id, first.reference.row_key, first.reference.height) == (1, "a", 720)
        assert first.reference.mark == ReferenceMark.BY_LEVEL and first.reference.reason is None
    elif expected is None:
        assert first is None or first.row is None and first.reference.source_id is None
    else:
        assert first is not None and first.row is not None and first.row.row_key == expected


def test_a_held_reference_is_held_again_on_the_next_pass_and_keeps_its_file():
    first = choose([], previous=[held(height=720)], on_disk={(1, "a")})
    again = choose([], previous=[each.reference for each in first], on_disk={(1, "a")})
    assert [each.reference for each in again] == [each.reference for each in first]
    assert [(each.reference.row_key, each.row) for each in again] == [("a", None)]


def test_a_held_reference_stays_in_its_own_part():
    chosen = choose([row(S, 2, "b", 1, 1, player="kodik", height=1080)], previous=[held(part=2)], on_disk={(1, "a")})
    assert [(each.slot, each.reference.row_key) for each in chosen] == [((1, 1, None), "b"), ((1, 1, 2), "a")]


def test_a_previous_reference_that_had_no_row_holds_nothing():
    nothing = Reference(season=1, episode=1, mark=ReferenceMark.NONE)
    rows = [unsure("a"), row(S, 1, "b", 1, 1, player="kodik", height=720)]
    assert only(choose(rows, previous=[nothing])).row.row_key == "b"
