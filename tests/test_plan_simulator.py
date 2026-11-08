"""The planner of a series in a world of tables (tests/plan_world.py): every situation is only other inputs, and after
every sync the invariants of the model hold (docs/missions/voice-alignment/stage-12/episode-table/MODEL.md)."""
import random

import pytest

from core.plan.tables import RowId
from plan_world import World

REF = "AniStar"


def episode_with_dubs(world: World, episode: int, count: int, *, ref_source: int = 1, dub_source: int = 2) -> None:
    """An episode with its reference and `count` voice-overs, synced: the asset holds one container."""
    world.put(ref_source, f"ref{episode}", episode, group=REF)
    for number in range(1, count + 1):
        world.put(dub_source, f"g{number}e{episode}", episode, group=f"G{number}", player="kodik")
    step = world.sync("исходно")
    world.check(step)
    assert not step.failed


def container_of(world: World, episode: int):
    found = [each for each in world.containers.values() if f"e{episode:02d} " in each.name and not each.off]
    assert len(found) == 1, [each.name for each in world.containers.values()]
    return found[0]


def kinds(step, slot):
    goal = step.plan.goal(slot)
    return goal.video.kind, {way.voice: way.kind for way in goal.tracks}


def test_the_example_of_the_owner_renumbered_reference_and_three_voices_go_to_episode_6_whole():
    """Episode 5 with 20 voice-overs. A rule numbers the reference and three voice-overs as episode 6 (the video is the
    same: they are in sync); 17 stay with episode 5, which gets another reference; one of the 17 has no row online any
    more. Episode 6 takes the video and the three tracks as they are; episode 5 downloads 16 voice-overs again and aligns
    the offline one out of the old container; only then the old file goes."""
    world = World()
    episode_with_dubs(world, 5, 20)
    world.put(3, "other5", 5, group=REF, player="cvh")  # another video of episode 5
    for key in ("ref5",):
        world.renumber(1, key, 6)
    for number in (1, 2, 3):
        world.renumber(2, f"g{number}e5", 6)
    world.drop(2, "g20e5")
    step = world.sync("перенумерация")
    world.check(step)
    assert not step.failed and len(step.applied) == len(step.plan.groups)
    video6, tracks6 = kinds(step, (1, 6))
    assert video6 == "asset" and tracks6 == {"G1": "reuse", "G2": "reuse", "G3": "reuse"}
    video5, tracks5 = kinds(step, (1, 5))
    assert video5 == "download"
    assert [voice for voice, kind in tracks5.items() if kind == "realign"] == ["G20"]
    assert sum(kind == "download" for kind in tracks5.values()) == 16
    assert len(container_of(world, 6).tracks) == 3 and len(container_of(world, 5).tracks) == 17
    assert len(world.containers) == 2  # the old file went: everything in it lives on


def test_the_example_with_voices_of_episode_6_that_cannot_match_the_video_blocks_both_episodes():
    """The same, but episode 6 has its own voice-over online: the video moved there is truly episode 5, so it fails
    alignment. Episodes 5 and 6 share the old file, so neither is applied: nothing is lost, the window shows the failure."""
    world = World()
    episode_with_dubs(world, 5, 4)
    world.put(3, "other5", 5, group=REF, player="cvh")
    world.renumber(1, "ref5", 6)
    world.put(2, "x6", 6, group="X", player="kodik")  # truly episode 6
    before = dict(world.containers)
    step = world.sync("перенумерация с чужой озвучкой")
    world.check(step)
    assert any(op.startswith("align:(1, 6)") for op in step.failed)
    assert world.containers == before


def test_a_source_that_forgets_the_reference_changes_nothing():
    world = World()
    episode_with_dubs(world, 1, 2)
    world.drop(1, "ref1")
    step = world.sync("источник забыл референс")
    world.check(step)
    assert not step.plan.ops and kinds(step, (1, 1))[0] == "asset"


def test_a_better_reference_downloads_online_voices_again_and_realigns_the_offline_one():
    world = World()
    episode_with_dubs(world, 1, 3)
    world.drop(2, "g3e1")
    world.put(1, "ref1hd", 1, group=REF, height=1080, video="ref1hd")
    step = world.sync("лучший референс")
    world.check(step)
    video, tracks = kinds(step, (1, 1))
    assert video == "download" and tracks == {"G1": "download", "G2": "download", "G3": "realign"}
    assert len(world.containers) == 1 and container_of(world, 1).video.snapshot.height == 1080


def test_the_same_row_in_a_higher_resolution_keeps_the_tracks():
    world = World()
    episode_with_dubs(world, 1, 2)
    world.put(1, "ref1", 1, group=REF, height=1080)  # the same row and video, higher now
    step = world.sync("то же видео выше")
    world.check(step)
    video, tracks = kinds(step, (1, 1))
    assert video == "download" and set(tracks.values()) == {"reuse"}


def test_delete_of_an_online_reference_downloads_it_again_and_keeps_its_tracks():
    """Н-3: «Удалить» does not switch off: the same video comes back, and the tracks are on its timeline."""
    world = World()
    episode_with_dubs(world, 1, 2)
    world.delete("video", container_of(world, 1).name)
    step = world.sync("удалить онлайн-референс")
    world.check(step)
    assert kinds(step, (1, 1)) == ("download", {"G1": "reuse", "G2": "reuse"})


def test_delete_of_an_offline_reference_takes_the_next_video_and_keeps_the_voices():
    """В-5: the offline voice-over is aligned out of the old container, the online one is downloaded again."""
    world = World()
    episode_with_dubs(world, 1, 2)
    world.put(3, "other1", 1, group=REF, player="cvh")
    world.drop(1, "ref1")
    world.drop(2, "g2e1")
    world.delete("video", container_of(world, 1).name)
    step = world.sync("удалить референс")
    world.check(step)
    video, tracks = kinds(step, (1, 1))
    assert video == "download" and tracks == {"G1": "download", "G2": "realign"}
    assert not any(group.lost for group in step.plan.groups)


def test_delete_of_the_reference_with_no_other_video_says_which_voices_go_with_it():
    """В-9: nothing to align the voices to — the plan names the tracks that go with the video. «No other video» means no
    row of the episode online at all: the video of a voice-over's row is a video of the episode too."""
    world = World()
    episode_with_dubs(world, 1, 2)
    for key in ("ref1", "g1e1", "g2e1"):
        world.drop(1 if key == "ref1" else 2, key)
    world.delete("video", container_of(world, 1).name)
    step = world.sync("удалить единственный референс")
    world.check(step)
    assert step.plan.goal((1, 1)) is None
    assert [dub.row_key for group in step.plan.groups for _, dub in group.lost] == ["g1e1", "g2e1"]
    assert world.containers == {}


def test_an_episode_switched_off_is_hidden_and_switched_on_again_with_no_work():
    world = World()
    episode_with_dubs(world, 1, 2)
    world.choose(off_episodes=frozenset({(1, 1)}))
    step = world.sync("отключить серию")
    world.check(step)
    assert not step.plan.ops and [each.name for each in world.containers.values()] == ["s01e01 [AniStar] 720p.mkv.off"]
    world.choose(off_episodes=frozenset())
    step = world.sync("включить серию")
    world.check(step)
    assert not step.plan.ops and not container_of(world, 1).off


def test_a_group_switched_off_keeps_its_tracks_and_realigns_them_when_the_reference_changes():
    """В-10: switching a group off stops new work; a track already in the container stays and follows the video."""
    world = World()
    episode_with_dubs(world, 1, 2)
    world.choose(off_groups=frozenset({"G1"}))
    step = world.sync("отключить группу")
    world.check(step)
    assert not step.plan.ops
    world.put(1, "ref1hd", 1, group=REF, height=1080, video="ref1hd")
    step = world.sync("лучший референс при отключённой группе")
    world.check(step)
    assert kinds(step, (1, 1))[1] == {"G1": "realign", "G2": "download"}


def test_another_video_behind_the_same_row_is_another_row():
    """В-6: the host uploaded another video under the row: a new candidate; the old file stays the reference on a tie."""
    world = World()
    episode_with_dubs(world, 1, 1)
    world.put(1, "ref1", 1, group=REF, video="ref1-v2")
    step = world.sync("подмена видео той же высоты")
    world.check(step)
    assert not step.plan.ops
    world.put(1, "ref1", 1, group=REF, video="ref1-v3", height=1080)
    step = world.sync("подмена видео выше")
    world.check(step)
    assert kinds(step, (1, 1)) == ("download", {"G1": "download"})


def test_a_failed_alignment_applies_nothing_and_the_next_sync_finishes():
    world = World()
    episode_with_dubs(world, 1, 2)
    world.put(1, "ref1hd", 1, group=REF, height=1080, video="ref1hd")
    world.align_fails.add(((1, 1), "G2"))
    before = dict(world.containers)
    step = world.sync("сбой выравнивания")
    world.check(step)
    assert step.failed and world.containers == before
    world.align_fails.clear()
    step = world.sync("повтор")
    world.check(step)
    assert not step.failed and container_of(world, 1).video.snapshot.height == 1080


def test_a_read_that_failed_changes_nothing():
    world = World()
    episode_with_dubs(world, 1, 2)
    world.read_fails.add(1)
    world.drop(1, "ref1")
    world.put(1, "ref1hd", 1, group=REF, height=1080, video="ref1hd")
    step = world.sync("сбой чтения")
    world.check(step)
    assert not step.plan.ops


def test_a_cut_out_of_a_compilation_survives_a_new_group_of_the_compilation():
    """П-4: the cut video is an asset; the rule calling the compilation's voice-over otherwise cuts nothing again."""
    world = World()
    world.put(1, "single3", 3, group=REF, player="cvh", height=720)
    world.put(4, "comp", 1, end=5, group=REF, height=2160, player="vk")
    world.put(2, "g1e3", 3, group="G1", player="kodik")
    step = world.sync("разбор")
    world.check(step)
    assert kinds(step, (1, 3)) == ("cut", {"G1": "download"})
    world.put(4, "comp", 1, end=5, group="Другая", height=2160, player="vk")
    step = world.sync("у компиляции сменилась группа")
    world.check(step)
    assert kinds(step, (1, 3))[0] == "asset"
    assert not any(op.startswith(("cut:", "compilation:")) for op in (each.id for each in step.plan.ops))


def test_delete_of_a_track_takes_it_away_and_an_online_one_comes_back():
    """Н-3: «Удалить» does not switch off: the row is online and comes back by download."""
    world = World()
    episode_with_dubs(world, 1, 2)
    name = container_of(world, 1).name
    world.delete("track", name, RowId(2, "g1e1", "g1e1"))
    step = world.sync("удалить дорожку")
    world.check(step)
    assert kinds(step, (1, 1))[1]["G1"] == "download"


ACTIONS = ("put_ref", "put_dub", "drop", "renumber", "higher", "delete_video", "delete_track", "off", "on",
           "off_group", "read_fail", "align_fail", "put_comp", "drop_comp")


@pytest.mark.parametrize("seed", range(120))
def test_random_histories_keep_the_invariants(seed):
    """Short random histories of a small series: whatever happens, after every sync nothing goes that the goal does not
    hold anew or that the user did not delete, failed work changes no file, and a sync that did everything leaves no work."""
    rng = random.Random(seed)
    world = World()
    keys: list[tuple[int, str]] = []
    for step_no in range(8):
        action = rng.choice(ACTIONS)
        episode = rng.choice((1, 2))
        world.read_fails.clear()
        world.align_fails.clear()
        if action == "put_ref":
            source = rng.choice((1, 3))
            key = f"r{source}e{rng.choice((1, 2))}"
            world.put(source, key, episode, group=REF, player="vk" if source == 1 else "cvh",
                      height=rng.choice((480, 720, 1080)))
            keys.append((source, key))
        elif action == "put_dub":
            group = rng.choice(("A", "B", "C"))
            key = f"{group}e{episode}"
            world.put(2, key, episode, group=group, player="kodik")
            keys.append((2, key))
        elif action == "drop" and keys:
            world.drop(*rng.choice(keys))
        elif action == "renumber" and keys:
            source, key = rng.choice(keys)
            world.renumber(source, key, episode)
        elif action == "higher" and keys:
            source, key = rng.choice(keys)
            for each in world.site.get(source, []):
                if each.row.id.row_key == key:
                    world.put(source, key, each.row.start, group=each.row.group, player=each.row.player,
                              height=each.row.height + 360, video=each.row.id.video, content=each.content)
        elif action == "delete_video" and world.containers:
            world.delete("video", rng.choice(sorted(world.containers)))
        elif action == "delete_track" and world.containers:
            container = world.containers[rng.choice(sorted(world.containers))]
            if container.tracks:
                world.delete("track", container.name, rng.choice(container.tracks).dub)
        elif action == "off":
            world.choose(off_episodes=world.choices.off_episodes | {(1, episode)})
        elif action == "on":
            world.choose(off_episodes=world.choices.off_episodes - {(1, episode)})
        elif action == "off_group":
            world.choose(off_groups=world.choices.off_groups ^ {rng.choice(("A", "B", "C"))})
        elif action == "read_fail":
            world.read_fails.add(rng.choice((1, 2, 3)))
        elif action == "align_fail":
            world.align_fails.add(((1, episode), rng.choice(("A", "B", "C"))))
        elif action == "put_comp":
            world.put(4, "comp", 1, end=2, group=rng.choice((REF, "A")), height=rng.choice((1080, 2160)))
        elif action == "drop_comp":
            world.drop(4, "comp")
        step = world.sync(f"{step_no}: {action}")
        world.check(step)
