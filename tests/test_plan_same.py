"""Variants the user declared equal (Р-99) in the world of the planner (tests/plan_world.py): the variants of a group are one
place in the queue of the reference, so the video the file holds stays while it is as good as the queue's first. The case of
the owner: the file holds the video of a source that is gone, nine other sources give the same picture."""
import random

import pytest

from core.contracts import ReferenceMark
from core.plan.inputs import choices_of, online_of
from core.plan.marks import references_of
from core.plan.planner import plan
from core.plan.tables import Inputs, RowId
from core.variant_queue import NO_SAME, Same
from core.voice_names import NO_GROUPS
from plan_world import Chaos, World
from scan_support import row

CN = "Китайский"
EPISODE = (1, 1)


def variant(source: int, player: str = "vk") -> tuple[str, int, str]:
    return (CN, source, player)


def equal(*sources: int) -> dict:
    return {EPISODE: Same((frozenset(variant(source) for source in sources),))}


def world_of(*sources: int, same: bool = True, height: int = 2160) -> World:
    world = World()
    world.choose(sources=(1, 2, 3, 4), **({"same": equal(*sources)} if same else {}))
    return world


def file_of(world: World):
    found = [each for each in world.containers.values() if "e01" in each.name and not each.off]
    assert len(found) == 1, sorted(world.containers)
    return found[0]


def synced(world: World, title: str):
    step = world.sync(title)
    world.check(step)
    assert not step.failed, step.failed
    return step


def held_by_the_third_source(world: World, *, same: bool) -> None:
    """The file holds the video of source 3 (the only one that gave the episode); then sources 1 and 2 give it as well."""
    world.put(3, "dj", 1, group=CN, height=2160)
    synced(world, "исходно")
    assert file_of(world).video.id.source_id == 3
    world.put(1, "a", 1, group=CN, height=2160)
    world.put(2, "b", 1, group=CN, height=2160)


def test_the_file_stays_with_the_video_it_holds_while_the_first_of_the_queue_is_one_of_its_group():
    world = world_of(1, 2, 3)
    held_by_the_third_source(world, same=True)
    step = synced(world, "равные появились")
    assert file_of(world).video.id.source_id == 3 and not step.plan.ops
    # The same story with no group: the source that stands first by the rule takes the episode, the file is made anew.
    other = world_of(same=False)
    held_by_the_third_source(other, same=False)
    synced(other, "без группы")
    assert file_of(other).video.id.source_id == 1


def test_the_file_stays_when_its_source_forgets_the_row_and_when_the_downloads_of_the_others_fail():
    world = world_of(1, 2, 3)
    held_by_the_third_source(world, same=True)
    synced(world, "равные появились")
    world.drop(3, "dj")
    world.download_fails.add(RowId(1, "a", "a"))
    assert not synced(world, "источник забыл строку").plan.ops
    assert file_of(world).video.id.source_id == 3


def test_a_higher_video_of_the_group_still_takes_the_place_of_the_file_resolution_decides_first():
    world = world_of(1, 3)
    world.put(3, "dj", 1, group=CN, height=1080)
    synced(world, "исходно")
    world.put(1, "a", 1, group=CN, height=2160)
    synced(world, "выше")
    assert file_of(world).video.id.source_id == 1 and file_of(world).video.snapshot.height == 2160


def test_with_nothing_held_the_group_gives_its_first_by_the_rule_and_the_next_member_when_that_one_fails():
    world = world_of(1, 2, 3)
    for source, key in ((1, "a"), (2, "b"), (3, "c")):
        world.put(source, key, 1, group=CN)
    step = synced(world, "первая серия")
    assert file_of(world).video.id.source_id == 1 and step.plan.goal(EPISODE).video.kind == "download"
    world = world_of(1, 2, 3)
    for source, key in ((1, "a"), (2, "b"), (3, "c")):
        world.put(source, key, 1, group=CN)
    world.download_fails.add(RowId(1, "a", "a"))
    synced(world, "первая не скачалась")
    assert file_of(world).video.id.source_id == 2


def test_a_group_stands_where_its_best_member_does_even_when_that_member_has_no_row_for_the_episode():
    """Source 4 stands before source 3 in the order of the sources, source 1 before both. The file holds source 3's video,
    which is of the group of source 1: with that member absent the group still stands at its place, before source 4."""
    world = World()
    world.choose(sources=(1, 4, 3), same=equal(1, 3))
    world.put(3, "c", 1, group=CN)
    synced(world, "исходно")
    world.put(4, "d", 1, group=CN)
    step = synced(world, "посторонний появился")
    assert file_of(world).video.id.source_id == 3 and not step.plan.ops
    plain = World()
    plain.choose(sources=(1, 4, 3))
    plain.put(3, "c", 1, group=CN)
    synced(plain, "исходно")
    plain.put(4, "d", 1, group=CN)
    synced(plain, "посторонний появился")
    assert file_of(plain).video.id.source_id == 4


def test_the_order_of_the_user_names_a_group_by_its_first_member_the_others_stand_at_that_place():
    """The user puts source 1 first, and 2 is in its group: when 1 fails to download, 2 is taken before an outsider that
    would otherwise be first by the order of the sources."""
    world = World()
    world.choose(sources=(4, 1, 2), reference={EPISODE: (variant(1),)}, same=equal(1, 2))
    for source, key in ((1, "a"), (2, "b"), (4, "d")):
        world.put(source, key, 1, group=CN)
    world.download_fails.add(RowId(1, "a", "a"))
    synced(world, "первая не скачалась")
    assert file_of(world).video.id.source_id == 2


# --- the mark of the reference: an equal is no other player, a failed equal is no loss ------------------------------

PRIORITY = ("vk", "cvh", "kodik")


def told(rows, *, wanted=(), same=NO_SAME, failed=frozenset()):
    online = online_of(rows)
    choices = choices_of(rows, online, [], [], players=PRIORITY, player_groups=NO_GROUPS, sources=(1, 2, 3), voice_order={},
                         off_episodes=())
    from dataclasses import replace
    choices = replace(choices, reference={(1, 1): tuple(wanted)} if wanted else {}, same={(1, 1): same} if same.groups else {})
    inputs = Inputs(online, (), choices, frozenset(failed))
    return {(each.season, each.episode): each for each in references_of(inputs, plan(inputs))}[(1, 1)]


def test_the_variant_the_user_put_first_is_missing_and_one_equal_to_it_is_taken_the_reference_is_not_another_player():
    rows = [row(1, 1, "a", 1, 1, player="vk", height=1080, voice="A")]
    first = ("B", 2, "cvh")
    assert told(rows, wanted=[first]).mark is ReferenceMark.OTHER_PLAYER
    assert told(rows, wanted=[first], same=Same((frozenset({first, ("A", 1, "vk")}),))).mark is not ReferenceMark.OTHER_PLAYER


def test_a_download_that_failed_is_no_loss_when_what_was_taken_is_equal_to_it():
    rows = [row(1, 1, "a", 1, 1, player="vk", height=1080, voice="A"), row(1, 2, "b", 1, 1, player="cvh", height=1080, voice="A")]
    lost = {("download", RowId(1, "a", ""))}
    assert told(rows, failed=lost).mark is ReferenceMark.OTHER_PLAYER
    equals = Same((frozenset({("A", 1, "vk"), ("A", 2, "cvh")}),))
    assert told(rows, failed=lost, same=equals).mark is not ReferenceMark.OTHER_PLAYER


# --- random histories ------------------------------------------------------------------------------------------------

# The groups are the matter here: the references come and go, are given a better source, fail to download, and the owner
# declares some of them equal and takes the groups back; the rest of the simulator's actions run in the other test.
FOCUSED = ("put_ref", "put_ref", "put_ref", "put_dub", "drop", "drop", "download_fail", "transient", "reference_order",
           "reference_deny", "swap_video", "higher", "same", "same", "same", "same_off", "reset", "read_fail")
EVERY = Chaos.ACTIONS + ("same", "same", "same_off")


@pytest.mark.parametrize("seed", range(200))
def test_random_histories_of_the_references_with_groups_of_equal_variants_never_give_up_a_held_video_for_an_equal(seed):
    """After every sync the file's video is not given up for a video of its group that is no higher (Э-8, checked by the
    world), and the series comes to rest."""
    Chaos(World(), random.Random(seed), actions=FOCUSED, episodes=(1, 2)).history(18)


@pytest.mark.parametrize("seed", range(60))
def test_random_histories_of_every_action_with_groups_of_equal_variants_keep_the_invariants_and_come_to_rest(seed):
    Chaos(World(), random.Random(seed), actions=EVERY).history(14)
