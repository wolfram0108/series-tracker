"""Output settings: the value of every lever for an episode by inheritance, the refusals, the edits of records, and
the episodes a change touches (invariants I-22 to I-24, combinations C-13 to C-17)."""
import itertools
import random

import pytest

from core.contracts import (EffectiveLever, Level, SettingRecord, SettingsReset, TargetKind, ValueShape, group_target,
                            variant_target)
from core.reasons import parse
from modules.composition import levers
from modules.composition.levers import Lever, _picked_source
from modules.composition.settings import (affected, apply_reset, apply_set, effective, levels, retarget, validate,
                                          validate_reset, without_source, within_episodes)

VK = variant_target("Ruchime", 1, "vk")
KODIK = variant_target("Ruchime", 2, "kodik")
GROUP = group_target("Ruchime")
OTHER_GROUP = group_target("AniStar")
CHOICE_VK = {"group": "Ruchime", "source_id": 1, "player": "vk"}
CHOICE_KODIK = {"group": "Ruchime", "source_id": 2, "player": "kodik"}
CHOICE = "reference.choice"
ALLOW = "variant.allow"
TAKE = "voice.take"
PLAYER = "voice.player"

EPISODES = [(season, episode) for season in (1, 2) for episode in range(1, 41)]


def rec(lever, value, season=None, episode=None, target=None, series_id=1) -> SettingRecord:
    return SettingRecord(series_id=series_id, season=season, episode=episode, lever=lever, target=target, value=value)


def levers_of(records, season, episode):
    return {(each.lever, each.target): (each.value, each.level) for each in effective(records, season, episode)}


@pytest.fixture
def volume():
    """A lever nobody wrote any code for: it comes only by registration and goes with the test."""
    fake = Lever("fake.volume", TargetKind.GROUP, ValueShape.FLAG, False, "voices")
    levers.register(fake)
    yield fake
    levers.unregister(fake.name)


# ---- inheritance (I-22) ------------------------------------------------------------------------------------------


def test_with_no_record_a_lever_without_a_target_has_its_default_and_targets_have_no_entries():
    assert effective([], 1, 5) == [EffectiveLever(lever=CHOICE, target=None, value=None, level=Level.DEFAULT)]


def test_an_episode_takes_its_own_value_else_the_season_then_the_series_then_the_default():
    records = [rec(CHOICE, CHOICE_VK), rec(CHOICE, CHOICE_KODIK, season=1), rec(CHOICE, None, season=1, episode=34)]
    assert levers_of(records, 1, 34)[(CHOICE, None)] == (None, Level.EPISODE)
    assert levers_of(records, 1, 35)[(CHOICE, None)] == (CHOICE_KODIK, Level.SEASON)
    assert levers_of(records, 2, 1)[(CHOICE, None)] == (CHOICE_VK, Level.SERIES)
    assert levers_of(records[:1] + records[2:], 1, 35)[(CHOICE, None)] == (CHOICE_VK, Level.SERIES)
    assert levers_of([], 1, 35)[(CHOICE, None)] == (None, Level.DEFAULT)


def test_a_season_and_the_series_have_their_own_values_by_the_same_rule():
    records = [rec(CHOICE, CHOICE_VK), rec(CHOICE, CHOICE_KODIK, season=1)]
    assert levers_of(records, 1, None)[(CHOICE, None)] == (CHOICE_KODIK, Level.SEASON)
    assert levers_of(records, 2, None)[(CHOICE, None)] == (CHOICE_VK, Level.SERIES)
    assert levers_of(records, None, None)[(CHOICE, None)] == (CHOICE_VK, Level.SERIES)


def test_an_own_value_equal_to_the_default_is_still_an_own_value():
    assert levers_of([rec(CHOICE, None, season=1)], 1, 5)[(CHOICE, None)] == (None, Level.SEASON)


def test_a_lever_with_targets_lists_the_targets_that_have_a_record_on_the_chain_of_the_episode():
    records = [rec(ALLOW, False, target=VK), rec(ALLOW, False, season=1, target=KODIK),
               rec(ALLOW, True, season=2, episode=3, target=KODIK), rec(TAKE, False, season=2, target=GROUP)]
    first = levers_of(records, 1, 3)
    assert {key for key in first if key[0] != CHOICE} == {(ALLOW, VK), (ALLOW, KODIK)}
    assert first[(ALLOW, VK)] == (False, Level.SERIES) and first[(ALLOW, KODIK)] == (False, Level.SEASON)
    second = levers_of(records, 2, 3)
    assert {key for key in second if key[0] != CHOICE} == {(ALLOW, VK), (ALLOW, KODIK), (TAKE, GROUP)}
    assert second[(ALLOW, KODIK)] == (True, Level.EPISODE) and second[(TAKE, GROUP)] == (False, Level.SEASON)


def test_every_lever_and_target_is_given_once_whatever_the_records_and_the_order_they_come_in():
    records = [rec(ALLOW, False, target=VK), rec(ALLOW, True, season=1, target=VK), rec(ALLOW, False, season=1, episode=2, target=VK),
               rec(TAKE, False, target=GROUP), rec(TAKE, True, season=1, target=GROUP), rec(TAKE, False, target=OTHER_GROUP),
               rec(CHOICE, CHOICE_VK), rec(CHOICE, CHOICE_KODIK, season=1), rec(PLAYER, {"source_id": 2, "player": "kodik"}, target=GROUP)]
    expected = {}
    for season, episode in [(1, 2), (1, 3), (2, 1), (1, None), (None, None)]:
        expected[season, episode] = effective(records, season, episode)
        keys = [(each.lever, each.target) for each in expected[season, episode]]
        assert len(keys) == len(set(keys)) and keys == sorted(keys, key=lambda pair: (
            [each.name for each in levers.all()].index(pair[0]), pair[1] or ""))
    shuffler = random.Random(7)
    for _ in range(20):
        shuffled = records[:]
        shuffler.shuffle(shuffled)
        assert {key: effective(shuffled, *key) for key in expected} == expected


def test_a_value_handed_out_is_a_copy_the_caller_may_change():
    records = [rec(CHOICE, dict(CHOICE_VK))]
    effective(records, 1, 1)[0].value["player"] = "changed"
    assert effective(records, 1, 1)[0].value == CHOICE_VK


def test_an_episode_without_a_season_is_refused():
    with pytest.raises(ValueError):
        effective([], None, 5)


def test_a_record_the_registry_no_longer_accepts_is_ignored():
    records = [rec(CHOICE, "garbage"), rec("gone.lever", 1), rec(ALLOW, False, target="not-a-target"),
               rec(ALLOW, "no", target=VK), rec(TAKE, False, target=None)]
    assert effective(records, 1, 1) == [EffectiveLever(lever=CHOICE, target=None, value=None, level=Level.DEFAULT)]


# ---- the episodes a change touches (I-23) ------------------------------------------------------------------------


def test_a_season_value_changes_only_the_episodes_of_the_season_that_follow_it():
    """C-13: the reference of the season is set, episode 34 has its own pick and its source off; then the season's changes."""
    before = [rec(CHOICE, CHOICE_VK, season=1),
              rec(ALLOW, False, season=1, episode=34, target=VK), rec(CHOICE, CHOICE_KODIK, season=1, episode=34)]
    after = apply_set(before, rec(CHOICE, {"group": "Ruchime", "source_id": 2, "player": "kodik"}, season=1))
    assert levers_of(after, 1, 34) == levers_of(before, 1, 34)
    assert levers_of(after, 1, 33)[(CHOICE, None)] == (CHOICE_KODIK, Level.SEASON) != levers_of(before, 1, 33)[(CHOICE, None)]
    assert affected(before, after, EPISODES) == {(1, episode) for episode in range(1, 41)} - {(1, 34)}


def test_a_season_pick_that_changes_leaves_the_own_value_of_episode_34_for_other_levers_to_follow():
    before = [rec(ALLOW, False, season=1, episode=34, target=VK)]
    after = apply_set(before, rec(ALLOW, False, season=1, target=VK))
    assert affected(before, after, EPISODES) == {(1, episode) for episode in range(1, 41)} - {(1, 34)}


def test_resetting_an_episode_back_to_its_season_touches_that_episode_alone():
    """C-14."""
    before = [rec(CHOICE, CHOICE_VK, season=1), rec(CHOICE, CHOICE_KODIK, season=1, episode=34),
              rec(ALLOW, False, season=1, episode=34, target=VK), rec(TAKE, False, season=2, target=GROUP)]
    after = apply_reset(before, SettingsReset(series_id=1, season=1, episode=34))
    assert levers_of(after, 1, 34)[(CHOICE, None)] == (CHOICE_VK, Level.SEASON)
    assert (ALLOW, VK) not in levers_of(after, 1, 34)
    assert affected(before, after, EPISODES) == {(1, 34)}
    one_by_one = apply_reset(before, SettingsReset(series_id=1, season=1, episode=34, lever=CHOICE))
    one_by_one = apply_reset(one_by_one, SettingsReset(series_id=1, season=1, episode=34, lever=ALLOW, target=VK))
    assert after == one_by_one


def test_a_voice_off_on_the_season_and_on_for_one_episode_is_taken_for_that_episode_only():
    """C-15."""
    records = [rec(TAKE, False, season=1, target=GROUP), rec(TAKE, True, season=1, episode=5, target=GROUP)]
    assert levers_of(records, 1, 5)[(TAKE, GROUP)] == (True, Level.EPISODE)
    assert levers_of(records, 1, 6)[(TAKE, GROUP)] == (False, Level.SEASON)
    assert (TAKE, GROUP) not in levers_of(records, 2, 5)
    assert affected([], records[:1], EPISODES) == {(1, episode) for episode in range(1, 41)}
    assert affected(records[:1], records, EPISODES) == {(1, 5)}


def test_numbers_that_came_from_a_new_reading_keep_their_records_and_leave_the_rest_alone():
    """C-17: the layout changes, the records stand by the numbers; what the numbers touch is only the changed records."""
    records = [rec(CHOICE, CHOICE_VK, season=1, episode=34), rec(TAKE, False, season=1, target=GROUP)]
    shrunk = [(season, episode) for season, episode in EPISODES if (season, episode) != (1, 34)]
    assert affected(records, records, shrunk) == set()
    assert levers_of(records, 1, 34)[(CHOICE, None)] == (CHOICE_VK, Level.EPISODE)
    assert levers_of(records, 1, 35)[(TAKE, GROUP)] == (False, Level.SEASON)


def test_a_series_value_touches_every_episode_that_does_not_have_its_own_and_nothing_else():
    before = [rec(CHOICE, CHOICE_VK, season=2), rec(CHOICE, CHOICE_KODIK, season=1, episode=3)]
    after = apply_set(before, rec(CHOICE, CHOICE_KODIK))
    expected = {pair for pair in EPISODES if pair[0] == 1} - {(1, 3)}
    assert affected(before, after, EPISODES) == expected


def test_a_change_that_changes_nothing_touches_nothing():
    records = [rec(CHOICE, CHOICE_VK, season=1)]
    assert affected(records, apply_set(records, rec(CHOICE, CHOICE_VK, season=1)), EPISODES) == set()
    assert affected(records, apply_reset(records, SettingsReset(series_id=1, season=2, lever=CHOICE)),
                    EPISODES) == set()
    assert affected(records, apply_reset(records, SettingsReset(series_id=1, season=2)), EPISODES) == set()
    assert affected(records, apply_set(records, rec(TAKE, True, season=3, target=GROUP)), []) == set()


SEASON_1 = {(1, episode) for episode in range(1, 41)}
SEASON_2 = {(2, episode) for episode in range(1, 41)}


@pytest.mark.parametrize("change, touched", [
    (lambda r: apply_set(r, rec(CHOICE, CHOICE_KODIK, season=2)), SEASON_2),
    (lambda r: apply_set(r, rec(ALLOW, True, season=1, target=VK)), SEASON_1),
    (lambda r: apply_set(r, rec(TAKE, True, season=1, target=GROUP)), SEASON_1 - {(1, 2)}),
    (lambda r: apply_reset(r, SettingsReset(series_id=1, season=1, episode=2, lever=TAKE, target=GROUP)), {(1, 2)}),
    (lambda r: apply_set(r, rec(PLAYER, {"source_id": 2, "player": "kodik"}, season=2, episode=7, target=GROUP)),
     {(2, 7)}),
    (lambda r: apply_reset(r, SettingsReset(series_id=1, season=1)), SEASON_1),
    (lambda r: apply_reset(r, SettingsReset(series_id=1)), set(EPISODES)),
])
def test_every_kind_of_change_touches_the_episodes_that_follow_the_level_it_moved(change, touched):
    base = [rec(CHOICE, CHOICE_VK), rec(ALLOW, False, season=1, target=VK),
            rec(TAKE, False, season=1, episode=2, target=GROUP)]
    assert affected(base, change(base), EPISODES) == touched


def test_resetting_the_series_leaves_the_seasons_and_episodes_their_own_values():
    before = [rec(CHOICE, CHOICE_VK), rec(CHOICE, CHOICE_KODIK, season=1), rec(CHOICE, None, season=2, episode=1)]
    after = apply_reset(before, SettingsReset(series_id=1))
    assert after == before[1:]
    assert affected(before, after, EPISODES) == {(2, episode) for episode in range(2, 41)}


# ---- validate ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("record", [
    rec(CHOICE, None), rec(CHOICE, CHOICE_VK, season=1), rec(CHOICE, None, season=1, episode=2),
    rec(ALLOW, False, target=VK), rec(TAKE, True, season=1, target=GROUP),
    rec(PLAYER, None, target=GROUP), rec(PLAYER, {"source_id": 2, "player": "kodik"}, season=1, episode=1, target=GROUP),
])
def test_a_record_in_the_terms_of_the_registry_is_accepted(record):
    assert validate(record) is None


@pytest.mark.parametrize("record, code", [
    (rec("no.such", True), "unknown_lever"),
    (rec("no.such", None, target=GROUP), "unknown_lever"),
    (rec(CHOICE, None, target=GROUP), "bad_target"),
    (rec(ALLOW, True), "bad_target"),
    (rec(ALLOW, True, target=GROUP), "bad_target"),
    (rec(ALLOW, True, target="source:1"), "bad_target"),
    (rec(TAKE, True, target=VK), "bad_target"),
    (rec(TAKE, True), "bad_target"),
    (rec(TAKE, "as_above", target=GROUP), "bad_value"),
    (rec(PLAYER, None, target="Ruchime"), "bad_target"),
    (rec(CHOICE, "auto"), "bad_value"),
    (rec(CHOICE, {"group": "Ruchime", "source_id": 1}), "bad_value"),
    (rec(ALLOW, None, target=VK), "bad_value"),
    (rec(ALLOW, 1, target=VK), "bad_value"),
    (rec(TAKE, "yes", target=GROUP), "bad_value"),
    (rec(PLAYER, {"player": "kodik"}, target=GROUP), "bad_value"),
])
def test_a_record_the_registry_refuses_is_refused_with_a_coded_reason(record, code):
    why = validate(record)
    assert parse(why)["code"] == code and parse(why)["params"] == {"lever": record.lever}


# ---- apply_set and apply_reset -----------------------------------------------------------------------------------


def test_setting_a_value_adds_a_record_and_setting_it_again_replaces_it():
    first = apply_set([], rec(ALLOW, False, season=1, target=VK))
    second = apply_set(first, rec(ALLOW, True, season=1, target=VK))
    third = apply_set(second, rec(ALLOW, True, season=1, target=KODIK))
    assert first == [rec(ALLOW, False, season=1, target=VK)] and second == [rec(ALLOW, True, season=1, target=VK)]
    assert len(third) == 2


def test_an_own_value_of_none_is_a_value_and_the_way_back_to_the_level_above_is_a_reset_of_the_lever():
    records = [rec(CHOICE, CHOICE_VK), rec(CHOICE, None, season=1), rec(ALLOW, False, season=1, target=VK)]
    assert levers_of(records, 1, 1)[(CHOICE, None)] == (None, Level.SEASON)
    back = apply_reset(records, SettingsReset(series_id=1, season=1, lever=CHOICE))
    assert back == [rec(CHOICE, CHOICE_VK), rec(ALLOW, False, season=1, target=VK)]
    assert levers_of(back, 1, 1)[(CHOICE, None)] == (CHOICE_VK, Level.SERIES)


def test_a_reset_of_a_lever_with_a_target_takes_that_target_only():
    records = [rec(ALLOW, False, season=1, target=VK), rec(ALLOW, False, season=1, target=KODIK),
               rec(ALLOW, True, season=2, target=VK), rec(TAKE, False, season=1, target=GROUP)]
    after = apply_reset(records, SettingsReset(series_id=1, season=1, lever=ALLOW, target=VK))
    assert after == [rec(ALLOW, False, season=1, target=KODIK), rec(TAKE, False, season=1, target=GROUP),
                     rec(ALLOW, True, season=2, target=VK)]
    assert apply_reset(records, SettingsReset(series_id=1, season=3, lever=ALLOW, target=VK)) == apply_set(
        records, rec(TAKE, False, season=1, target=GROUP))


@pytest.mark.parametrize("scope, code", [
    (SettingsReset(series_id=1, season=1, lever="no.such"), "unknown_lever"),
    (SettingsReset(series_id=1, season=1, lever=ALLOW), "bad_target"),
    (SettingsReset(series_id=1, season=1, lever=ALLOW, target=GROUP), "bad_target"),
    (SettingsReset(series_id=1, season=1, lever=CHOICE, target=VK), "bad_target"),
    (SettingsReset(series_id=1, season=1, target=VK), "bad_target"),
])
def test_a_reset_that_names_a_lever_or_a_target_the_registry_does_not_know_is_refused(scope, code):
    assert parse(validate_reset(scope))["code"] == code
    with pytest.raises(ValueError) as caught:
        apply_reset([rec(CHOICE, CHOICE_VK)], scope)
    assert parse(str(caught.value))["code"] == code


@pytest.mark.parametrize("scope", [SettingsReset(series_id=1), SettingsReset(series_id=1, season=1, episode=2),
                                   SettingsReset(series_id=1, lever=CHOICE),
                                   SettingsReset(series_id=1, season=1, lever=TAKE, target=GROUP)])
def test_a_reset_of_a_level_or_of_a_known_lever_is_accepted(scope):
    assert validate_reset(scope) is None


def test_the_records_given_are_not_changed_and_what_is_stored_does_not_share_the_callers_value():
    records = [rec(ALLOW, False, season=1, target=VK)]
    kept = [each.model_copy(deep=True) for each in records]
    value = dict(CHOICE_VK)
    new = apply_set(records, rec(CHOICE, value))
    value["player"] = "changed"
    assert records == kept and new != records
    assert [each.value for each in new if each.lever == CHOICE] == [CHOICE_VK]
    assert apply_reset(records, SettingsReset(series_id=1, season=1)) == [] and records == kept


def test_a_record_that_is_refused_is_not_stored():
    with pytest.raises(ValueError) as caught:
        apply_set([], rec(ALLOW, "no", target=VK))
    assert parse(str(caught.value))["code"] == "bad_value"


def test_records_come_back_in_one_order_so_that_independent_edits_commute():
    edits = [rec(ALLOW, False, season=1, target=VK), rec(CHOICE, CHOICE_VK), rec(TAKE, False, season=2, episode=4, target=GROUP),
             rec(ALLOW, True, target=KODIK), rec(PLAYER, None, season=1, target=OTHER_GROUP)]
    results = set()
    for order in itertools.permutations(edits):
        records = []
        for edit in order:
            records = apply_set(records, edit)
        results.add(tuple(each.model_dump_json() for each in records))
    assert len(results) == 1


def test_a_reset_takes_the_records_of_that_level_only():
    records = [rec(CHOICE, CHOICE_VK), rec(ALLOW, False, season=1, target=VK), rec(TAKE, False, season=1, target=GROUP),
               rec(CHOICE, CHOICE_KODIK, season=1, episode=3), rec(CHOICE, CHOICE_KODIK, season=2)]
    assert apply_reset(records, SettingsReset(series_id=1, season=1)) == apply_set(
        apply_set(apply_set([], rec(CHOICE, CHOICE_VK)), rec(CHOICE, CHOICE_KODIK, season=1, episode=3)),
        rec(CHOICE, CHOICE_KODIK, season=2))
    assert apply_reset(records, SettingsReset(series_id=1, season=1, episode=3)) != records
    assert len(apply_reset(records, SettingsReset(series_id=1, season=1, episode=3))) == 4
    assert apply_reset(records, SettingsReset(series_id=1, season=3)) == apply_set(records, rec(CHOICE, CHOICE_VK))


# ---- records that outlive their sources and episodes (C-16, C-17, I-24) -------------------------------------------


def test_a_source_that_leaves_takes_every_record_that_points_at_it_by_target_or_by_value():
    """C-16."""
    records = [rec(ALLOW, False, target=VK), rec(ALLOW, False, season=1, target=KODIK),
               rec(CHOICE, CHOICE_VK, season=1), rec(CHOICE, CHOICE_KODIK, season=2),
               rec(PLAYER, {"source_id": 1, "player": "vk"}, season=1, target=GROUP),
               rec(PLAYER, {"source_id": 2, "player": "kodik"}, season=1, target=OTHER_GROUP),
               rec(TAKE, False, season=1, target=GROUP), rec(CHOICE, None, season=3)]
    after = without_source(records, 1)
    assert {(each.lever, each.target, each.season) for each in after} == {
        (ALLOW, KODIK, 1), (CHOICE, None, 2), (PLAYER, OTHER_GROUP, 1), (TAKE, GROUP, 1), (CHOICE, None, 3)}
    assert levers_of(after, 1, 5)[(CHOICE, None)] == (None, Level.DEFAULT)
    assert without_source(records, 9) == apply_set(records, rec(CHOICE, None, season=3))


def test_a_source_that_leaves_takes_the_records_of_a_lever_registered_later_that_points_at_it(volume):
    pointing = Lever("fake.pin", TargetKind.NONE, ValueShape.VARIANT_PICK, None, "scan", _picked_source)
    levers.register(pointing)
    try:
        records = [rec("fake.pin", {"source_id": 1, "player": "vk"}), rec("fake.pin", {"source_id": 4, "player": "vk"}, season=1),
                   rec("fake.volume", True, target=GROUP)]
        assert {(each.lever, each.season) for each in without_source(records, 1)} == {
            ("fake.pin", 1), ("fake.volume", None)}
    finally:
        levers.unregister("fake.pin")


def test_an_episode_that_leaves_the_layout_takes_its_own_records_and_the_levels_above_stay():
    """C-17."""
    records = [rec(CHOICE, CHOICE_VK), rec(TAKE, False, season=1, target=GROUP),
               rec(CHOICE, CHOICE_KODIK, season=1, episode=34), rec(CHOICE, CHOICE_KODIK, season=1, episode=35),
               rec(ALLOW, False, season=2, episode=1, target=VK)]
    after = within_episodes(records, {(1, 35), (2, 2)})
    assert {(each.lever, each.season, each.episode) for each in after} == {
        (CHOICE, None, None), (TAKE, 1, None), (CHOICE, 1, 35)}
    assert within_episodes(records, set(EPISODES)) == apply_set(records, rec(CHOICE, CHOICE_VK))


# ---- a lever nobody wrote code for (a part of M-34) --------------------------------------------------------------


def test_a_lever_added_by_registration_is_inherited_validated_set_reset_and_counted_without_other_changes(volume):
    assert validate(rec("fake.volume", True, season=1, target=GROUP)) is None
    assert parse(validate(rec("fake.volume", 101, target=GROUP)))["code"] == "bad_value"
    assert parse(validate(rec("fake.volume", True)))["code"] == "bad_target"

    before = apply_set([], rec("fake.volume", True, season=1, target=GROUP))
    assert levers_of(before, 1, 2)[("fake.volume", GROUP)] == (True, Level.SEASON)
    assert ("fake.volume", GROUP) not in levers_of(before, 2, 2)

    after = apply_set(before, rec("fake.volume", False, season=1, episode=2, target=GROUP))
    assert levers_of(after, 1, 2)[("fake.volume", GROUP)] == (False, Level.EPISODE)
    assert affected(before, after, EPISODES) == {(1, 2)}

    reset = apply_reset(after, SettingsReset(series_id=1, season=1, episode=2))
    assert reset == before and affected(after, reset, EPISODES) == {(1, 2)}


def test_a_lever_without_a_target_registered_later_has_its_default_in_every_episode():
    levers.register(Lever("fake.mode", TargetKind.NONE, ValueShape.FLAG, True, "scan"))
    try:
        assert levers_of([], 1, 1)[("fake.mode", None)] == (True, Level.DEFAULT)
        records = apply_set([], rec("fake.mode", False, season=2))
        assert levers_of(records, 2, 9)[("fake.mode", None)] == (False, Level.SEASON)
        assert affected([], records, EPISODES) == {pair for pair in EPISODES if pair[0] == 2}
    finally:
        levers.unregister("fake.mode")


def test_records_of_a_lever_that_left_the_registry_wait_unseen_and_come_back_when_it_does(volume):
    records = apply_set([], rec("fake.volume", True, target=GROUP))
    levers.unregister("fake.volume")
    assert ("fake.volume", GROUP) not in levers_of(records, 1, 1) and records
    assert parse(validate(records[0]))["code"] == "unknown_lever"
    levers.register(volume)
    assert levers_of(records, 1, 1)[("fake.volume", GROUP)] == (True, Level.SERIES)


# ---- the headers of the levels ------------------------------------------------------------------------------------


def test_the_headers_show_the_series_and_each_season_by_the_value_of_the_level_itself():
    records = [rec(TAKE, False, target=GROUP), rec(TAKE, True, season=1, target=GROUP),
               rec(CHOICE, CHOICE_VK, season=1, episode=3), rec(CHOICE, CHOICE_KODIK, season=2)]
    shown = levels(records, [2, 1, 1])
    assert [each.season for each in shown] == [None, 1, 2]
    by_level = {each.season: {(x.lever, x.target): (x.value, x.level) for x in each.levers} for each in shown}
    assert by_level[None] == {(CHOICE, None): (None, Level.DEFAULT), (TAKE, GROUP): (False, Level.SERIES)}
    assert by_level[1] == {(CHOICE, None): (None, Level.DEFAULT), (TAKE, GROUP): (True, Level.SEASON)}
    assert by_level[2] == {(CHOICE, None): (CHOICE_KODIK, Level.SEASON), (TAKE, GROUP): (False, Level.SERIES)}


def test_the_headers_of_a_series_with_no_season_are_the_series_alone():
    assert [each.season for each in levels([], [])] == [None]


# ---- a voice-over changes its group: what was set for it follows ---------------------------------------------------------


def test_what_was_set_for_a_group_a_variant_of_it_and_the_reference_of_it_is_set_for_the_group_it_became():
    unknown, made = "Неизвестная озвучка 1", "Стар"
    records = [rec(TAKE, False, season=1, target=group_target(unknown)),
               rec(ALLOW, False, season=1, episode=2, target=variant_target(unknown, 1, "vk")),
               rec(CHOICE, {"group": unknown, "source_id": 1, "player": "vk"}, season=1),
               rec(PLAYER, {"source_id": 2, "player": "kodik"}, target=group_target(unknown)),
               rec(TAKE, False, target=group_target("Другая"))]
    after = retarget(records, {unknown: made})
    assert sorted((each.lever, each.target) for each in after) == sorted([
        (TAKE, group_target(made)), (ALLOW, variant_target(made, 1, "vk")), (CHOICE, None),
        (PLAYER, group_target(made)), (TAKE, group_target("Другая"))])
    assert [each.value for each in after if each.lever == CHOICE] == [{"group": made, "source_id": 1, "player": "vk"}]
    assert retarget(after, {made: unknown}) == canonical_of(records)


def canonical_of(records):
    return sorted(records, key=lambda each: (each.series_id, -1 if each.season is None else each.season,
                                              -1 if each.episode is None else each.episode, each.lever, each.target or ""))


def test_a_record_the_new_name_already_has_at_the_same_level_stands_and_the_moved_one_gives_way():
    records = [rec(TAKE, False, season=1, target=group_target("Неизвестная озвучка 1")),
               rec(TAKE, True, season=1, target=group_target("Стар")),
               rec(TAKE, False, season=2, target=group_target("Неизвестная озвучка 1"))]
    after = retarget(records, {"Неизвестная озвучка 1": "Стар"})
    assert [(each.season, each.target, each.value) for each in after] == [
        (1, group_target("Стар"), True), (2, group_target("Стар"), False)]


def test_no_renames_leave_the_records_as_they_are():
    records = [rec(TAKE, False, target=GROUP)]
    assert retarget(records, {}) == records
