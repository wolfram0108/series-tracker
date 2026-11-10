"""The planner of a series in a world of tables (tests/plan_world.py): every situation is only other inputs, and after
every sync the invariants of the model hold (docs/missions/voice-alignment/stage-12/episode-table/MODEL.md)."""
import random

import pytest

from core.plan.tables import RowId
from plan_world import Chaos, World

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


def test_the_example_with_a_voice_of_episode_6_that_cannot_match_the_video_leaves_that_voice_out():
    """The same, but episode 6 has its own voice-over online: the video moved there is truly episode 5, so it fails
    alignment. The failure is recorded for that voice-over and that video; the plan is counted again without it, and the
    rest is applied: nothing that was on disk is lost."""
    world = World()
    episode_with_dubs(world, 5, 4)
    world.put(3, "other5", 5, group=REF, player="cvh")
    world.renumber(1, "ref5", 6)
    world.put(2, "x6", 6, group="X", player="kodik")  # truly episode 6
    step = world.sync("перенумерация с чужой озвучкой")
    world.check(step)
    assert ("align", RowId(2, "x6", "x6"), RowId(1, "ref5", "ref5")) in step.failed_rows
    assert not step.failed and "X" not in {track.group for track in container_of(world, 6).tracks}


def test_the_reference_was_another_episode_and_the_rule_is_fixed():
    """The owner's case 1. The reference of episode 6 is truly episode 8: of its ten voice-overs nine fail alignment, one
    (also truly 8) passes. The rule is fixed: the row is episode 8. Episode 8 takes that video (VK before CVH) and aligns
    its own voice-overs to it; its old reference goes once the new container is built; episode 6 chooses another video
    and its voice-overs are aligned anew — the failures against the wrong video do not hold for the new one."""
    world = World()
    world.put(1, "ref6", 6, group=REF, content=(1008,))
    world.put(3, "alt6", 6, group=REF, player="cvh")
    for number in range(1, 11):
        world.put(2, f"g{number}e6", 6, group=f"G{number}", player="kodik", content=(1008,) if number == 1 else (1006,))
    world.put(3, "ref8", 8, group=REF, player="cvh")
    for number in (1, 2):
        world.put(2, f"g{number}e8", 8, group=f"G{number}", player="kodik")
    step = world.sync("референс серии 6 — на деле серия 8")
    world.check(step)
    assert len([key for key in step.failed_rows if key[0] == "align"]) == 9
    assert [track.group for track in container_of(world, 6).tracks] == ["G1"]
    world.renumber(1, "ref6", 8)
    step = world.sync("конструктор исправлен")
    world.check(step)
    assert container_of(world, 8).video.id.row_key == "ref6"
    assert {track.group for track in container_of(world, 8).tracks} == {"G1", "G2"}
    assert container_of(world, 6).video.id.row_key == "alt6"
    assert {track.group for track in container_of(world, 6).tracks} == {f"G{number}" for number in range(2, 11)}
    assert not any(each.video.id.row_key == "ref8" for each in world.containers.values())


def test_an_episode_renumbered_on_purpose_moves_with_its_voices():
    """The owner's case 2: episode 6 is to be episode 8, its reference and voice-overs with it. The file moves under the
    new name with its tracks as they are; a voice-over episode 8 has besides is aligned to that video, nothing else."""
    world = World()
    episode_with_dubs(world, 6, 2)
    for source, key in ((1, "ref6"), (2, "g1e6"), (2, "g2e6")):
        world.renumber(source, key, 8)
    world.put(2, "g3e8", 8, group="G3", player="kodik", content=(1006,))
    step = world.sync("серия 6 стала 8")
    world.check(step)
    assert kinds(step, (1, 8)) == ("asset", {"G1": "reuse", "G2": "reuse", "G3": "download"})
    assert list(world.containers) == [container_of(world, 8).name]


def test_a_download_that_fails_takes_the_next_source_in_the_same_sync():
    world = World()
    world.put(1, "ref1", 1, group=REF)
    world.put(3, "alt1", 1, group=REF, player="cvh")
    world.download_fails.add(RowId(1, "ref1", "ref1"))
    step = world.sync("загрузка не удалась")
    world.check(step)
    assert step.replanned == (("download", RowId(1, "ref1", "ref1")),)
    assert container_of(world, 1).video.id.row_key == "alt1"


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


def test_delete_of_a_reference_whose_sound_no_source_gives_aligns_that_sound_out_of_the_old_file():
    """«Удалить» names the video, not its sound: the own sound of the deleted video, which no source gives any more, is
    aligned out of the old file onto the new video; only then the old file goes."""
    world = World()
    episode_with_dubs(world, 1, 1)
    world.put(3, "other1", 1, group="Other", player="cvh")
    world.drop(1, "ref1")
    old = container_of(world, 1).name
    world.delete("video", old)
    step = world.sync("удалить референс, звук которого есть только в файле")
    world.check(step)
    video, tracks = kinds(step, (1, 1))
    assert video == "download" and tracks == {REF: "realign", "G1": "download"}
    (container,) = world.containers.values()
    assert container.video.id.row_key == "other1" and sorted(track.group for track in container.tracks) == [REF, "G1"]


def test_delete_of_a_track_no_source_gives_takes_it_out_of_the_file_with_no_alignment():
    """Д-1: «Удалить» at an offline track is its removal: the container is built without it, nothing is aligned."""
    world = World()
    episode_with_dubs(world, 1, 1)
    world.drop(2, "g1e1")
    world.delete("track", container_of(world, 1).name, RowId(2, "g1e1", "g1e1"))
    step = world.sync("удалить офлайн-дорожку")
    world.check(step)
    assert kinds(step, (1, 1))[1] == {}
    assert not any(op.kind == "align" for op in step.plan.ops)
    assert container_of(world, 1).tracks == () and len(world.containers) == 1  # the old file goes, it is not hidden
    step = world.sync("следующая синхронизация")
    world.check(step)
    assert not step.plan.ops and container_of(world, 1).tracks == ()


def test_a_sound_that_could_not_be_aligned_out_of_a_deleted_file_keeps_the_file():
    """The file of a deleted video is needed while a sound in it has no other way: the alignment out of it failed, so the
    new video comes in and the old file stays, hidden, holding the sound until the failures are reset."""
    world = World()
    episode_with_dubs(world, 1, 1)
    world.put(3, "other1", 1, group="Other", player="cvh")
    world.drop(1, "ref1")
    world.delete("video", container_of(world, 1).name)
    world.answers[((1, 1), REF)] = "mismatch"
    step = world.sync("выравнивание из удаляемого файла не удалось")
    world.check(step)
    step = world.sync("следующая синхронизация")
    world.check(step)
    assert any(REF in (container.video.snapshot.group, *(t.group for t in container.tracks))
               for container in world.containers.values())
    world.reset_failures()
    world.answers.clear()
    step = world.settle()
    world.check(step)
    assert len(world.containers) == 1
    (container,) = world.containers.values()
    assert sorted(track.group for track in container.tracks) == [REF, "G1"]


def test_delete_of_the_reference_with_no_other_video_changes_nothing():
    """В-9: the sources give no other video of the episode — nothing to put in its place, so nothing is done; the
    command waits and is carried out once another video comes. «No other video» means no row of the episode online at
    all: the video of a voice-over's row is a video of the episode too."""
    world = World()
    episode_with_dubs(world, 1, 2)
    before = dict(world.containers)
    for key in ("ref1", "g1e1", "g2e1"):
        world.drop(1 if key == "ref1" else 2, key)
    world.delete("video", container_of(world, 1).name)
    step = world.sync("удалить единственный референс")
    world.check(step)
    assert step.plan.goal((1, 1)).video.kind == "asset" and not step.plan.ops
    assert not any(group.remove for group in step.plan.groups)
    assert world.containers == before and world.choices.delete  # the command waits
    world.put(3, "other1", 1, group=REF, player="cvh")
    step = world.sync("появилось другое видео")
    world.check(step)
    video, tracks = kinds(step, (1, 1))
    assert video == "download" and tracks == {"G1": "realign", "G2": "realign"}
    assert container_of(world, 1).video.id.row_key == "other1"


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


@pytest.mark.parametrize("lever", ["series", "episode", "variant"])
def test_a_voice_switched_off_is_removed_from_every_container(lever):
    """В-10: a track cannot be hidden in its container — the voice-over switched off (its group for the series or for an
    episode, or its variant) is removed from the containers at the next sync; switched on again, it is downloaded anew."""
    world = World()
    for number in (1, 2):
        episode_with_dubs(world, number, 2)
    variant = ("G1", 2, "kodik")
    taken = {"series": dict(off_groups=frozenset({"G1"})),
             "episode": dict(voice_off={(1, n): frozenset({"G1"}) for n in (1, 2)}),
             "variant": dict(voice_deny={(1, n): frozenset({variant}) for n in (1, 2)})}[lever]
    world.choose(**taken)
    step = world.sync("озвучка отключена")
    world.check(step)
    assert {op.kind for op in step.plan.ops} == {"build"}
    assert all([track.group for track in container_of(world, n).tracks] == ["G2"] for n in (1, 2))
    world.choose(off_groups=frozenset(), voice_off={}, voice_deny={})
    step = world.sync("озвучка включена")
    world.check(step)
    assert kinds(step, (1, 1))[1] == {"G1": "download", "G2": "reuse"}


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


def test_a_voice_that_cannot_be_aligned_to_the_new_video_keeps_its_old_file_until_it_can():
    """A better reference comes; G2 fails alignment to it both ways (fresh download and out of the old container). The
    new container is built without G2; the old one, which alone holds G2, stays hidden. Reset, the next sync aligns G2,
    and the old file goes."""
    world = World()
    episode_with_dubs(world, 1, 2)
    world.put(1, "ref1hd", 1, group=REF, height=1080, video="ref1hd")
    world.align_fails.add(((1, 1), "G2"))
    step = world.sync("сбой выравнивания")
    world.check(step)
    assert not step.failed and [track.group for track in container_of(world, 1).tracks] == ["G1"]
    hidden = [each for each in world.containers.values() if each.off]
    assert len(hidden) == 1 and [track.group for track in hidden[0].tracks] == ["G1", "G2"]
    world.align_fails.clear()
    world.reset_failures()
    step = world.sync("сброс неудачных попыток")
    world.check(step)
    assert [track.group for track in container_of(world, 1).tracks] == ["G1", "G2"] and len(world.containers) == 1


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
    assert not any(op.startswith(("cut:", "picture:")) for op in (each.id for each in step.plan.ops))


def test_a_download_that_failed_for_the_moment_holds_nothing_back_and_comes_next_time():
    """3.7: a row not downloaded blocks nothing. A new voice-over whose download fails for this sync only (the player does
    not answer) is passed over: the episode is built without it — the deleted track goes all the same — the window is told
    the failure, nothing is recorded, and the next sync downloads it."""
    world = World()
    episode_with_dubs(world, 1, 1)
    world.drop(2, "g1e1")
    world.delete("track", container_of(world, 1).name, RowId(2, "g1e1", "g1e1"))
    world.put(3, "g2e1", 1, group="G2", player="cvh")
    world.transient = {RowId(3, "g2e1", "g2e1")}
    step = world.sync("новая озвучка не скачалась на этот раз")
    world.check(step)
    assert step.applied and container_of(world, 1).tracks == () and len(world.containers) == 1
    assert not world.failed and ("download", RowId(3, "g2e1", "g2e1")) in step.failed_rows
    world.transient = set()
    step = world.sync("следующая синхронизация")
    world.check(step)
    assert [track.group for track in container_of(world, 1).tracks] == ["G2"]


def test_a_download_that_failed_for_the_moment_and_was_made_by_the_next_source_is_no_problem():
    """3.7: the first row of a voice-over does not answer this time and the next source gives it: the episode is applied
    with that voice-over, and the outcome owes nothing — the series shows no error. With no next source the voice-over is
    left out and the outcome says so."""
    world = World()
    episode_with_dubs(world, 1, 0)
    world.put(3, "g1cvh", 1, group="G1", player="cvh")
    world.put(2, "g1kodik", 1, group="G1", player="kodik")
    world.transient = {RowId(3, "g1cvh", "g1cvh")}
    step = world.sync("первая строка не ответила, следующая есть")
    world.check(step)
    assert [track.dub.row_key for track in container_of(world, 1).tracks] == ["g1kodik"]
    assert world.history[-1].problems == ()
    world.put(3, "g2cvh", 1, group="G2", player="cvh")
    world.transient = {RowId(3, "g2cvh", "g2cvh")}
    step = world.sync("строка не ответила, другой нет")
    world.check(step)
    assert [(problem[1], problem[2]) for problem in step.problems] == [("download_dub", "G2")]


def test_delete_of_a_track_takes_it_away_and_an_online_one_comes_back():
    """Н-3: «Удалить» does not switch off: the row is online and comes back by download."""
    world = World()
    episode_with_dubs(world, 1, 2)
    name = container_of(world, 1).name
    world.delete("track", name, RowId(2, "g1e1", "g1e1"))
    step = world.sync("удалить дорожку")
    world.check(step)
    assert kinds(step, (1, 1))[1]["G1"] == "download"


@pytest.mark.parametrize("seed", range(150))
def test_random_histories_keep_the_invariants_and_come_to_rest(seed):
    """Random histories of a small series of two seasons (tests/plan_world.py, Chaos): after every sync nothing goes that
    the goal does not hold anew or the user did not delete, every file holds one episode, one visible file per episode;
    when the faults stop the series comes to rest."""
    Chaos(World(), random.Random(seed)).history(12)


# --- faults, one kind at a time --------------------------------------------------------------------------------------

def test_two_videos_numbered_as_one_episode_the_wrong_one_wins_and_the_failures_show_it():
    """Overlapping numbers: a row truly episode 2 is numbered 1 and stands first by priority. Every voice-over of episode
    1 fails alignment to it: the file holds the video alone, the failures are recorded, nothing else happens."""
    world = World()
    world.put(3, "true1", 1, group=REF, player="cvh")
    world.put(1, "wrong1", 1, group=REF, content=(1002,))
    for number in (1, 2):
        world.put(2, f"g{number}e1", 1, group=f"G{number}", player="kodik")
    step = world.sync("пересечение номеров")
    world.check(step)
    assert container_of(world, 1).video.id.row_key == "wrong1" and not container_of(world, 1).tracks
    assert len([key for key in step.failed_rows if key[0] == "align"]) == 2


def test_a_season_change_moves_the_file_with_its_tracks():
    world = World()
    episode_with_dubs(world, 3, 2)
    for source, key in ((1, "ref3"), (2, "g1e3"), (2, "g2e3")):
        world.move(source, key, 2, 3)
    step = world.sync("серия ушла во второй сезон")
    world.check(step)
    assert kinds(step, (2, 3)) == ("asset", {"G1": "reuse", "G2": "reuse"})
    assert [each.name for each in world.containers.values()] == ["s02e03 [AniStar] 720p.mkv"]


def test_a_whole_source_renumbered_by_one_comes_to_rest():
    """Mass renumbering: every row of the reference's source is one episode further; the voice-overs stay. The videos
    move, the voice-overs of each episode fail against the video of the episode before and pass against their own."""
    world = World()
    for episode in (1, 2, 3):
        episode_with_dubs(world, episode, 1)
    for episode in (1, 2, 3):
        world.renumber(1, f"ref{episode}", episode + 1)
    step = world.sync("источник сдвинут на одну серию")
    world.check(step)
    world.calm()
    world.settle()


def test_a_file_lost_from_disk_is_made_again_and_an_offline_voice_in_it_is_gone():
    world = World()
    episode_with_dubs(world, 1, 2)
    world.drop(2, "g2e1")  # G2 is offline now: its only copy is the track in the file
    world.lose_file(container_of(world, 1).name)
    step = world.sync("файл пропал с диска")
    world.check(step)
    assert kinds(step, (1, 1)) == ("download", {"G1": "download"})
    assert [track.group for track in container_of(world, 1).tracks] == ["G1"]


@pytest.mark.parametrize("answer", ["suspect", "critical", "mismatch", "crash", "lost"])
def test_every_answer_of_the_alignment(answer):
    """suspect and critical are taken (marks of the track: it is in step); mismatch, the service's verdict, is recorded and
    the voice-over is left out (the old file keeps its track); a crash of the service and a lost answer judged nothing and
    are tried again at the next sync."""
    world = World()
    episode_with_dubs(world, 1, 2)
    world.put(1, "ref1hd", 1, group=REF, height=1080, video="ref1hd")
    world.answers[((1, 1), "G2")] = answer
    step = world.sync(f"ответ сервиса: {answer}")
    world.check(step)
    shown = [track.group for track in container_of(world, 1).tracks]
    if answer in ("suspect", "critical"):
        assert shown == ["G1", "G2"] and len(world.containers) == 1
    elif answer in ("lost", "crash"):
        assert container_of(world, 1).video.snapshot.height == 720  # nothing applied: the group waits for the answer
        assert not step.replanned
        world.answers.clear()
        world.check(world.sync("ответ пришёл"))
        assert [track.group for track in container_of(world, 1).tracks] == ["G1", "G2"]
    else:
        assert shown == ["G1"] and any(each.off and len(each.tracks) == 2 for each in world.containers.values())


def test_the_order_of_voices_changed_again_and_again_only_repacks():
    world = World()
    episode_with_dubs(world, 1, 3)
    for order in (("G3", "G2", "G1"), ("G2", "G3", "G1"), ("G1", "G2", "G3"), ("G3", "G1", "G2")):
        world.choose(groups={1: order})
        step = world.sync(f"порядок озвучек {order}")
        world.check(step)
        assert {op.kind for op in step.plan.ops} == {"build"}
        assert [track.group for track in container_of(world, 1).tracks] == list(order)


def test_the_name_of_a_track_given_and_taken_back_only_repacks_and_a_file_switched_off_keeps_its_names():
    world = World()
    episode_with_dubs(world, 1, 2)
    episode_with_dubs(world, 2, 2)
    world.choose(off_episodes=frozenset({(1, 2)}))
    world.check(world.sync("серия 2 выключена"))
    for titles in ({"G1": "ПМ_G1"}, {"G1": "ПМ_G1", REF: "Оригинал"}, {}):
        world.choose(titles=titles)
        step = world.sync(f"имена дорожек {titles}")
        world.check(step)
        assert {op.kind for op in step.plan.ops} == {"build"}  # the tracks are copied: nothing downloaded, nothing aligned
        assert dict(container_of(world, 1).titles) == titles
        hidden = next(each for each in world.containers.values() if each.off)
        assert hidden.titles == ()  # a file switched off takes no new work
    world.choose(titles={"G2": "ПМ_G2"})
    world.check(world.sync("имя G2"))
    world.choose(off_episodes=frozenset())
    step = world.sync("серия 2 включена")
    world.check(step)
    assert dict(container_of(world, 2).titles) == {"G2": "ПМ_G2"}


def test_the_language_of_a_voice_over_is_the_language_of_its_tracks_and_a_change_of_it_only_repacks():
    world = World()
    episode_with_dubs(world, 1, 2)
    world.check(world.sync("без языков"))
    assert container_of(world, 1).languages == ()  # a group the user gave no language to: none is claimed
    for languages in ({"G1": "rus", REF: "chi"}, {"G1": "jpn", REF: "chi"}):
        world.choose(languages=languages)
        step = world.sync(f"языки {languages}")
        world.check(step)
        assert {op.kind for op in step.plan.ops} == {"build"}  # the tracks are copied: nothing downloaded, nothing aligned
        assert dict(container_of(world, 1).languages) == languages
    step = world.sync("ничего не изменилось")
    assert not step.plan.ops


def test_every_season_orders_its_tracks_by_its_own_order():
    world = World()
    for season in (1, 2):
        world.put(1, f"ref{season}", 1, season=season, group=REF)
        for group in ("G1", "G2"):
            world.put(2, f"{group}s{season}", 1, season=season, group=group, player="kodik")
    world.choose(groups={1: ("G2", "G1"), 2: ("G1", "G2")})
    step = world.sync("порядки сезонов")
    world.check(step)
    tracks = {each.video.episode[0]: [track.group for track in each.tracks] for each in world.containers.values()}
    assert tracks == {1: ["G2", "G1"], 2: ["G1", "G2"]}


def test_switching_on_and_off_back_and_forth_does_no_work():
    world = World()
    episode_with_dubs(world, 1, 2)
    for _ in range(3):
        for off in (frozenset({(1, 1)}), frozenset()):
            world.choose(off_episodes=off)
            step = world.sync("вкл/выкл серии")
            world.check(step)
            assert not step.plan.ops
        for groups in (frozenset({"G1"}), frozenset()):  # a voice-over switched off goes and comes back (В-10)
            world.choose(off_groups=groups)
            step = world.sync("вкл/выкл группы")
            world.check(step)
            assert not world.sync("ещё раз").plan.ops


def test_a_cut_whose_service_finds_something_critical_takes_the_single_video():
    world = World()
    world.put(1, "single3", 3, group=REF, player="cvh")
    world.put(4, "comp", 1, end=5, group=REF, height=2160)
    world.cut_answers[(1, 3)] = "critical"
    step = world.sync("разбор: критичная находка")
    world.check(step)
    assert container_of(world, 3).video.cut_of is None and step.replanned[0][0] == "cut"


# --- the service's own rule, the levers, compilations as voice-overs, parts ------------------------------------------

def test_of_two_equal_videos_the_earlier_row_of_the_layout_is_the_reference():
    """The last tie-break of the service's rule (core/variant_queue) is the place of the row in the layout."""
    world = World()
    world.put(1, "early", 1, group=REF)
    world.put(1, "late", 1, group=REF)
    step = world.sync("ничья")
    world.check(step)
    assert container_of(world, 1).video.id.row_key == "early"


def test_the_players_the_user_called_the_same_take_one_place():
    from core.voice_names import Groups
    world = World()
    world.choose(players=("vk", "cvh", "kodik"), player_groups=Groups.of({"vk": ["cvh"]}))
    world.put(1, "cvh1", 1, group=REF, player="cvh")
    world.put(1, "vk1", 1, group=REF, player="vk")
    step = world.sync("группа плееров")
    world.check(step)
    assert container_of(world, 1).video.id.row_key == "cvh1"  # one place, one source: the earlier row of the layout wins


def test_the_levers_of_the_reference_order_and_leave_out_variants():
    world = World()
    world.put(1, "vk1", 1, group=REF)
    world.put(3, "cvh1", 1, group=REF, player="cvh")
    world.choose(reference={(1, 1): ((REF, 3, "cvh"),)})
    world.check(world.sync("порядок РЕФ"))
    assert container_of(world, 1).video.id.row_key == "cvh1"
    world.choose(reference={}, reference_deny={(1, 1): frozenset({(REF, 1, "vk")})})
    world.check(world.sync("запрет варианта"))
    assert container_of(world, 1).video.id.row_key == "cvh1"


def test_the_levers_of_a_voice_order_leave_out_and_take_off():
    world = World()
    world.put(1, "ref1", 1, group=REF)
    world.put(2, "g1kodik", 1, group="G1", player="kodik")
    world.put(3, "g1cvh", 1, group="G1", player="cvh")
    world.choose(voice_order={((1, 1), "G1"): ((2, "kodik"),)})
    step = world.sync("порядок озвучки")
    world.check(step)
    assert container_of(world, 1).tracks[0].dub.row_key == "g1kodik"
    world.choose(voice_order={}, voice_off={(1, 1): frozenset({"G1"})})
    step = world.sync("озвучка снята")
    world.check(step)
    assert not container_of(world, 1).tracks  # a voice-over taken off is removed from the container (В-10)


def test_the_users_word_on_a_compilation_holds_while_it_fits():
    world = World()
    world.put(1, "single2", 2, group=REF, player="cvh")
    world.put(4, "short", 2, end=3, group=REF, height=2160)
    world.put(4, "long", 1, end=5, group=REF, height=2160)
    step = world.sync("правило: самая короткая")
    world.check(step)
    assert container_of(world, 2).video.cut_of.row_key == "short"
    world.choose(member={(1, 2): RowId(4, "long", "long")})
    step = world.sync("слово пользователя: длинная")
    world.check(step)
    assert container_of(world, 2).video.cut_of.row_key == "long"
    world.choose(member={(1, 2): None})
    step = world.sync("ни в одной")
    world.check(step)
    assert container_of(world, 2).video.cut_of is None


def test_a_compilation_is_a_voice_over_of_an_episode_it_does_not_cut():
    """Д-19: a compilation of another group that covers the episode gives it that voice-over."""
    world = World()
    world.put(1, "single2", 2, group=REF, height=1080)
    world.put(4, "comp", 1, end=3, group="A", height=720, player="kodik")
    step = world.sync("склейка-озвучка")
    world.check(step)
    assert [track.group for track in container_of(world, 2).tracks] == ["A"]
    assert container_of(world, 2).video.cut_of is None


def test_an_episode_in_parts_has_a_file_of_each_part_and_the_levers_of_the_episode():
    world = World()
    for part in (1, 2):
        world.put(1, f"ref-p{part}", 1, group=REF, part=part)
        world.put(2, f"g1-p{part}", 1, group="G1", player="kodik", part=part)
    world.put(4, "comp", 1, end=3, group=REF, height=2160)  # an episode in parts is not cut out of a compilation
    step = world.sync("серия в двух частях")
    world.check(step)
    parts = sorted(each.name for each in world.containers.values())
    assert parts == ["s01e01 pt1 [AniStar] 720p.mkv", "s01e01 pt2 [AniStar] 720p.mkv"]
    assert all([track.group for track in each.tracks] == ["G1"] for each in world.containers.values())
    world.choose(off_episodes=frozenset({(1, 1)}))
    world.check(world.sync("серия отключена"))
    assert all(each.off for each in world.containers.values())


def test_a_deleted_video_that_waits_for_another_one_takes_no_new_work():
    """В-9: the deleted video stays only for want of another one — a compilation of another group that covers the episode
    brings no voice-over to it (nothing changes), and the command keeps waiting."""
    world = World()
    world.put(1, "ref2", 2, group=REF)
    world.check(world.sync("исходно"))
    world.drop(1, "ref2")
    world.delete("video", container_of(world, 2).name)
    world.put(4, "comp", 1, end=3, group="A", height=720, player="kodik")
    step = world.sync("склейка при удалённом видео")
    world.check(step)
    assert not step.plan.ops and not container_of(world, 2).tracks and world.choices.delete
