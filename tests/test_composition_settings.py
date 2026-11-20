"""Output settings: the value of every lever for an episode by inheritance, the refusals, the edits of records, and
the episodes a change touches (invariants I-22 to I-24, combinations C-13 to C-17)."""

import pytest

from core.contracts import (Queue, SettingRecord, TargetKind, ValueShape, group_target, variant_target)
from modules.composition import levers
from modules.composition.levers import Lever, _order_sources
from modules.composition.settings import (retarget, without_source)

VK = variant_target("Ruchime", 1, "vk")
KODIK = variant_target("Ruchime", 2, "kodik")
GROUP = group_target("Ruchime")
ALLOW = "reference.allow"
PLAYER = "voice.order"

EPISODES = [(season, episode) for season in (1, 2) for episode in range(1, 41)]


def rec(lever, value, season=None, episode=None, target=None, series_id=1) -> SettingRecord:
    return SettingRecord(series_id=series_id, season=season, episode=episode, lever=lever, target=target, value=value)


# The levers of compilations (stage 12) have no target: every episode lists them with their defaults.
@pytest.fixture
def volume():
    """A lever nobody wrote any code for: it comes only by registration and goes with the test."""
    fake = Lever("fake.volume", TargetKind.GROUP, ValueShape.FLAG, False, "voices", Queue.VOICE)
    levers.register(fake)
    yield fake
    levers.unregister(fake.name)


# ---- inheritance (I-22) ------------------------------------------------------------------------------------------


# ---- the episodes a change touches (I-23) ------------------------------------------------------------------------


SEASON_1 = {(1, episode) for episode in range(1, 41)}
SEASON_2 = {(2, episode) for episode in range(1, 41)}


# ---- validate ----------------------------------------------------------------------------------------------------


# ---- apply_set and apply_reset -----------------------------------------------------------------------------------


# ---- records that outlive their sources and episodes (C-16, C-17, I-24) -------------------------------------------


def test_a_source_that_leaves_takes_the_records_of_a_lever_registered_later_that_points_at_it(volume):
    pointing = Lever("fake.pin", TargetKind.GROUP, ValueShape.VARIANT_ORDER, [], "scan", Queue.REFERENCE, _order_sources)
    levers.register(pointing)
    try:
        records = [rec("fake.pin", [{"source_id": 1, "player": "vk"}], target=GROUP),
                   rec("fake.pin", [{"source_id": 4, "player": "vk"}], season=1, target=GROUP),
                   rec("fake.volume", True, target=GROUP)]
        assert {(each.lever, each.season) for each in without_source(records, 1)} == {
            ("fake.pin", 1), ("fake.volume", None)}
    finally:
        levers.unregister("fake.pin")


# ---- a lever nobody wrote code for (a part of M-34) --------------------------------------------------------------


# ---- the headers of the levels ------------------------------------------------------------------------------------


# ---- a voice-over changes its group: what was set for it follows ---------------------------------------------------------


def canonical_of(records):
    return sorted(records, key=lambda each: (each.series_id, -1 if each.season is None else each.season,
                                              -1 if each.episode is None else each.episode, each.lever, each.target or ""))


# ---- two groups became one: what was set for each is one setting of the group they became --------------------------------


def test_a_pick_of_a_group_that_takes_the_name_of_an_existing_group_goes_back_to_the_level_above_when_the_picks_differ():
    told = []
    different = [rec(PLAYER, [{"source_id": 1, "player": "vk"}], target=group_target("A")),
                 rec(PLAYER, [{"source_id": 2, "player": "kodik"}], target=group_target("B"))]
    assert retarget(different, {"A": "B"}, told.append) == [] and len(told) == 1
    equal = [rec(PLAYER, [{"source_id": 1, "player": "vk"}], target=group_target("A")),
             rec(PLAYER, [{"source_id": 1, "player": "vk"}], target=group_target("B"))]
    assert [(each.target, each.value) for each in retarget(equal, {"A": "B"})] == [
        (group_target("B"), [{"source_id": 1, "player": "vk"}])]


# --- Р-99: groups of equal variants ---------------------------------------------------------------------------------

SAME = "reference.same"


def variant(group, source, player):
    return {"group": group, "source_id": source, "player": player}


def test_a_source_that_leaves_takes_its_variants_out_of_the_groups_and_a_group_left_with_one_is_gone():
    records = [rec(SAME, [[variant("A", 1, "vk"), variant("A", 2, "vk"), variant("A", 3, "vk")],
                          [variant("A", 1, "cvh"), variant("A", 2, "cvh")]], season=1),
               rec(SAME, [[variant("A", 1, "vk"), variant("A", 2, "vk")]], season=2)]
    after = without_source(records, 1)
    assert [(each.season, each.value) for each in after] == [(1, [[variant("A", 2, "vk"), variant("A", 3, "vk")]])]
    assert without_source(records, 9) == canonical_of(records)


def test_voice_over_groups_that_became_one_make_the_groups_that_share_a_variant_one_group():
    records = [rec(SAME, [[variant("Неизвестная озвучка 1", 1, "vk"), variant("A", 2, "vk")],
                          [variant("A", 2, "vk"), variant("B", 3, "vk")]], season=1)]
    after = retarget(records, {"Неизвестная озвучка 1": "A"})
    assert [each.value for each in after] == [[[variant("A", 1, "vk"), variant("A", 2, "vk"), variant("B", 3, "vk")]]]
    alone = [rec(SAME, [[variant("Неизвестная озвучка 1", 1, "vk"), variant("A", 1, "vk")]], season=1)]
    assert retarget(alone, {"Неизвестная озвучка 1": "A"}) == []  # one variant is no group: the level above speaks again
