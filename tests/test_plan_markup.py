"""The markup reference of a series (stage 14, docs/missions/voice-alignment/stage-14/DESIGN.md) in the world of the
planner (tests/plan_world.py): the events С-1…С-20 of the design one by one, then random histories around the mark. After
every sync the world holds its invariants, the markup ones (Э-2…Э-7) among them."""
import random

import pytest

from core.plan.tables import RowId
from plan_world import Chaos, World

PVA, ORIGINAL = "ПВА", "Оригинал"
PICTURE, MARKUP, DUBS, OTHER = 1, 3, 2, 5  # the sources: the high picture, the markup reference, voice-overs, a second original


def world_of(*, markup: int | None = MARKUP, dubs: int = 2, episodes=(1,), pva: int = 2160, original: int = 1080) -> World:
    """A series of the owner's kind: a high picture (ПВА) and the original in the right structure, a few voice-overs;
    the order of the season puts the original first, so that its place last is the markup's own rule."""
    world = World()
    world.choose(sources=(PICTURE, DUBS, MARKUP, OTHER),
                 groups={1: (ORIGINAL, PVA, *(f"G{number}" for number in range(1, dubs + 1)))})
    for episode in episodes:
        world.put(PICTURE, f"pva{episode}", episode, group=PVA, height=pva)
        world.put(MARKUP, f"orig{episode}", episode, group=ORIGINAL, player="cvh", height=original)
        for number in range(1, dubs + 1):
            world.put(DUBS, f"g{number}e{episode}", episode, group=f"G{number}", player="kodik")
    world.choose(markup=markup)
    return world


def synced(world: World, title: str):
    step = world.sync(title)
    world.check(step)
    return step


def file_of(world: World, episode: int = 1):
    found = [each for each in world.containers.values() if f"e{episode:02d}" in each.name and not each.off]
    assert len(found) == 1, sorted(world.containers)
    return found[0]


def sounds_of(container) -> list[str]:
    sounds = [track.group for track in container.tracks]
    sounds.insert(container.own_at, container.video.snapshot.group)
    return sounds


def op_kinds(step) -> list[str]:
    return sorted(op.kind for op in step.plan.ops)


def row(source: int, key: str) -> RowId:
    return RowId(source, key, key)


# --- С-1, С-2: the mark ------------------------------------------------------------------------------------------

def test_marked_with_a_higher_picture_the_episode_is_the_picture_cut_onto_the_markup_and_the_original_is_last():
    world = world_of()
    step = synced(world, "пометка")
    container = file_of(world)
    assert container.video.cut_of == row(PICTURE, "pva1") and container.video.timeline == row(MARKUP, "orig1")
    assert sounds_of(container) == [PVA, "G1", "G2", ORIGINAL]
    goal = step.plan.goal((1, 1))
    assert {way.voice: way.kind for way in goal.tracks}[ORIGINAL] == "timeline"
    # The original is downloaded once and never aligned; the picture is downloaded once for the cut.
    assert op_kinds(step).count("download_video") == 1 and op_kinds(step).count("download_picture") == 1
    assert not [op for op in step.plan.ops if op.kind == "align" and op.subject[1].voice == ORIGINAL]


def test_marked_when_the_picture_is_already_in_the_library_the_cut_takes_that_file():
    world = world_of(markup=None)
    synced(world, "без эталона")
    assert file_of(world).video.id == row(PICTURE, "pva1")
    world.choose(markup=MARKUP)
    step = synced(world, "пометка")
    assert "download_picture" not in op_kinds(step)
    assert step.plan.goal((1, 1)).video.picture is not None
    container = file_of(world)
    assert container.video.cut_of == row(PICTURE, "pva1") and container.video.timeline == row(MARKUP, "orig1")
    assert len(world.containers) == 1  # the old file went: everything in it lives on


def test_marked_when_the_markup_is_the_best_video_it_is_the_video_and_its_sound_is_last():
    world = world_of(pva=720)
    step = synced(world, "эталон лучший")
    container = file_of(world)
    assert container.video.id == row(MARKUP, "orig1") and container.video.cut_of is None
    assert "cut" not in op_kinds(step)
    assert sounds_of(container)[-1] == ORIGINAL and container.own_at == len(container.tracks)


def test_a_markup_row_of_an_unknown_voice_over_gives_no_timeline():
    world = world_of()
    world.put(MARKUP, "orig1", 1, group=None, player="cvh", height=1080)
    step = synced(world, "озвучка эталона без группы")
    assert step.plan.goal((1, 1)).markup is None
    assert file_of(world).video.id == row(PICTURE, "pva1")


# --- С-3, Э-6: another picture on the same timeline --------------------------------------------------------------

def test_a_higher_picture_on_the_same_timeline_only_repacks():
    world = world_of(pva=1440)
    synced(world, "картинка 1440")
    world.put(PICTURE, "pva1", 1, group=PVA, height=2160)
    step = synced(world, "картинка 2160")
    assert "cut" in op_kinds(step) and "align" not in op_kinds(step) and "download_dub" not in op_kinds(step)
    assert file_of(world).video.snapshot.height == 2160


# --- С-4…С-7: failures -------------------------------------------------------------------------------------------

def test_a_markup_that_fails_to_download_leaves_the_episode_by_the_rule_without_it():
    world = world_of()
    world.download_fails.add(row(MARKUP, "orig1"))
    step = synced(world, "эталон не скачался")
    assert step.plan.goal((1, 1)).markup is None
    assert file_of(world).video.id == row(PICTURE, "pva1")


def test_a_picture_that_fails_to_download_takes_the_next_one_then_the_markup_itself():
    world = world_of()
    world.put(OTHER, "alt1", 1, group=PVA, player="vk", height=1440)
    world.download_fails.add(row(PICTURE, "pva1"))
    synced(world, "картинка не скачалась")
    assert file_of(world).video.cut_of == row(OTHER, "alt1")
    both = world_of()
    both.put(OTHER, "alt1", 1, group=PVA, player="vk", height=1440)
    both.download_fails |= {row(PICTURE, "pva1"), row(OTHER, "alt1")}
    synced(both, "обе не скачались")
    assert file_of(both).video.id == row(MARKUP, "orig1")


def test_a_cut_the_service_turns_down_falls_back_to_the_markup_downloaded_once():
    world = world_of()
    world.cut_answers[(1, 1)] = "critical"
    step = synced(world, "разбор отклонён")
    assert step.replanned[0][0] == "cut"
    assert file_of(world).video.id == row(MARKUP, "orig1")  # the world asserts the markup was downloaded once (Э-7)


def test_a_cut_whose_answer_never_came_changes_nothing_and_comes_next_time():
    world = world_of(markup=None)
    synced(world, "без эталона")
    before = dict(world.containers)
    world.choose(markup=MARKUP)
    world.cut_answers[(1, 1)] = "lost"
    step = synced(world, "ответа нет")
    assert not step.applied and world.containers == before
    world.cut_answers.clear()
    synced(world, "снова")
    assert file_of(world).video.timeline == row(MARKUP, "orig1")


# --- С-9…С-13: the mark taken off, moved, the row gone or changed -------------------------------------------------

def test_the_mark_taken_off_keeps_the_cut_and_only_puts_the_original_in_the_order_of_the_season():
    world = world_of()
    synced(world, "пометка")
    world.choose(markup=None)
    step = synced(world, "пометка снята")
    assert op_kinds(step) == ["build"]
    container = file_of(world)
    assert container.video.cut_of == row(PICTURE, "pva1")
    assert sounds_of(container) == [ORIGINAL, PVA, "G1", "G2"]


def test_the_mark_moved_to_another_source_lays_the_episode_onto_its_timeline():
    world = world_of()
    world.put(OTHER, "dub1", 1, group=ORIGINAL, player="cvh", height=720)
    synced(world, "эталон 3")
    world.choose(markup=OTHER)
    synced(world, "эталон 5")
    container = file_of(world)
    assert container.video.timeline == row(OTHER, "dub1") and container.video.cut_of == row(PICTURE, "pva1")
    assert sounds_of(container)[-1] == ORIGINAL


def test_a_markup_row_the_source_forgot_keeps_what_is_held():
    world = world_of()
    synced(world, "пометка")
    world.drop(MARKUP, "orig1")
    step = synced(world, "эталон пропал")
    assert "cut" not in op_kinds(step) and "align" not in op_kinds(step)
    assert file_of(world).video.timeline == row(MARKUP, "orig1")


def test_another_video_behind_the_markup_row_is_another_timeline():
    world = world_of()
    synced(world, "пометка")
    world.put(MARKUP, "orig1", 1, group=ORIGINAL, player="cvh", height=1080, video="v2")
    step = synced(world, "площадка перезалила")
    assert "cut" in op_kinds(step) and "align" in op_kinds(step)
    assert file_of(world).video.timeline == RowId(MARKUP, "orig1", "v2")


# --- С-14, С-15: the picture gone, «Удалить» ---------------------------------------------------------------------

def test_a_picture_the_source_forgot_keeps_the_cut():
    world = world_of()
    synced(world, "пометка")
    world.drop(PICTURE, "pva1")
    step = synced(world, "картинка пропала")
    assert not [op for op in step.plan.ops if op.kind != "build"]
    assert file_of(world).video.cut_of == row(PICTURE, "pva1")


def test_delete_of_the_cut_makes_it_anew():
    world = world_of()
    synced(world, "пометка")
    world.delete("video", file_of(world).name)
    step = synced(world, "удалить разбор")
    assert "cut" in op_kinds(step) and "align" not in op_kinds(step)
    assert file_of(world).video.cut_of == row(PICTURE, "pva1")


# --- С-18, С-19: compilations and parts ----------------------------------------------------------------------------

def test_a_compilation_over_the_episodes_is_cut_onto_the_markup_and_none_leaves_it_out():
    world = world_of(episodes=(1, 2), pva=720)
    world.put(4, "comp", 1, end=2, group=PVA, height=2160)
    synced(world, "компиляция и эталон")
    for episode in (1, 2):
        container = file_of(world, episode)
        assert container.video.cut_of == row(4, "comp") and container.video.timeline == row(MARKUP, f"orig{episode}")
    world.choose(member={(1, 1): None})
    synced(world, "серия 1 ни в одной")
    assert file_of(world, 1).video.id == row(MARKUP, "orig1")


def test_an_episode_in_parts_is_not_cut_and_its_markup_part_is_the_video():
    world = world_of(episodes=())
    world.put(PICTURE, "pva1", 1, group=PVA, height=2160)
    for part in (1, 2):
        world.put(MARKUP, f"orig1p{part}", 1, group=ORIGINAL, player="cvh", height=1080, part=part)
    step = synced(world, "части")
    assert "cut" not in op_kinds(step)
    parts = [each for each in world.containers.values() if " pt" in each.name and not each.off]
    assert {each.video.id.row_key for each in parts} == {"orig1p1", "orig1p2"}


# --- the class fixed on the way: the sound of the timeline of a compilation cut -----------------------------------

def test_the_sound_of_the_single_video_a_compilation_is_cut_by_is_not_downloaded_again_nor_aligned():
    world = World()
    world.put(1, "single3", 3, group="AniStar", player="cvh", height=720)
    world.put(4, "comp", 1, end=5, group="ПВА", height=2160)
    step = synced(world, "разбор компиляции")
    goal = step.plan.goal((1, 3))
    assert {way.voice: way.kind for way in goal.tracks} == {"AniStar": "timeline"}
    assert op_kinds(step) == ["build", "cut", "download_picture", "download_video"]


# --- random histories around the mark ------------------------------------------------------------------------------

ACTIONS = ("markup", "markup", "put_high", "put_high", "put_ref", "put_dub", "put_comp", "drop", "higher", "swap_video",
           "delete_video", "off", "on", "member", "reference_deny", "download_fail", "transient", "cut_answer", "answer",
           "lose_file", "reset", "shuffle_voices")


@pytest.mark.parametrize("seed", range(80))
def test_random_histories_around_the_mark_keep_the_invariants_and_come_to_rest(seed):
    world = World()
    world.choose(markup=3)
    Chaos(world, random.Random(seed), actions=ACTIONS).history(14)
