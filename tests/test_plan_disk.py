"""The planner of the table of an episode on a disk (tests/plan_disk.py): real files made by ffmpeg, the service's own
downloader and container builder, the service's own client talking to a model of the alignment service, the journal of
the application, crashes at every step, adoption from the files."""
import asyncio
import os
import random
import subprocess
from dataclasses import replace

import pytest

from core import container as mkv
from core.plan.files import BuildFailed, dub_key
from core.plan.tables import RowId
from plan_disk import Crash, DiskWorld
from plan_world import Chaos

REF = "AniStar"


@pytest.fixture
def disk(tmp_path):
    world = DiskWorld(str(tmp_path))
    yield world
    world.close()


def episode(world, number: int, voices: int, *, season: int = 1) -> None:
    world.put(1, f"ref{season}{number}", number, season=season, group=REF)
    for each in range(1, voices + 1):
        world.put(2, f"g{each}s{season}e{number}", number, season=season, group=f"G{each}", player="kodik")


def visible(world, number: int, season: int = 1):
    found = [each for each in world.containers.values() if not each.off and each.video.episode == (season, number)]
    assert len(found) == 1, sorted(world.containers)
    return found[0]


def test_an_episode_is_downloaded_aligned_and_built_with_its_provenance(disk):
    episode(disk, 1, 2)
    step = disk.sync("первая синхронизация")
    disk.check(step)
    assert not step.failed and [track.group for track in visible(disk, 1).tracks] == ["G1", "G2"]


def test_a_better_reference_realigns_the_offline_voice_out_of_the_old_container(disk):
    """The offline voice-over is laid onto the new video from the old container, by the number of its track
    (`dub_atracks`), through the service's own client; the model of the service checks its sound against the picture."""
    episode(disk, 1, 2)
    disk.check(disk.sync("исходно"))
    disk.drop(2, "g2s1e1")
    disk.put(3, "hd1", 1, group=REF, player="vk", height=1080)
    disk.choose(sources=(3, 1, 2))
    step = disk.sync("лучший референс")
    disk.check(step)
    goal = step.plan.goal((1, 1))
    assert goal.video.kind == "download" and {way.voice: way.kind for way in goal.tracks} == {"G1": "download", "G2": "realign"}
    assert visible(disk, 1).video.id.row_key == "hd1" and len(disk.containers) == 1


def test_a_cut_out_of_a_compilation_through_the_model_of_the_service(disk):
    disk.put(1, "single2", 2, group=REF, player="cvh")
    disk.put(4, "comp", 1, end=3, group=REF, height=2160)
    disk.put(2, "g1e2", 2, group="G1", player="kodik")
    step = disk.sync("разбор")
    disk.check(step)
    assert visible(disk, 2).video.cut_of == RowId(4, "comp", "comp")


@pytest.mark.parametrize("answer", ["suspect", "critical", "mismatch", "crash", "lost"])
def test_every_answer_of_the_service_through_its_protocol(disk, answer):
    episode(disk, 1, 2)
    disk.check(disk.sync("исходно"))
    disk.put(1, "hd1", 1, group=REF, height=1080, video="hd1")
    disk.answers[((1, 1), "G2")] = answer
    step = disk.sync(f"ответ: {answer}")
    disk.check(step)
    disk.calm()
    disk.settle()


def test_a_file_lost_from_disk_is_gone_from_the_table_and_made_again(disk):
    episode(disk, 1, 1)
    disk.check(disk.sync("исходно"))
    disk.lose_file(visible(disk, 1).name)
    step = disk.sync("файл пропал")
    disk.check(step)
    assert step.plan.goal((1, 1)).video.kind == "download"


def test_tracks_placed_beside_the_container_are_packed_into_it_and_their_files_go(disk):
    """A file made before the planner may hold the video alone, its voice-overs placed beside it and never packed: the
    table names their files. The next sync builds the container with them inside out of those files (nothing downloaded,
    nothing aligned) and removes the files."""
    episode(disk, 1, 2)
    disk.check(disk.sync("исходно"))
    container = visible(disk, 1)
    path = disk.library(container.name)
    held = asyncio.run(mkv.contents(path))
    beside = []
    for track in container.tracks:
        stream = next(each.index for each in held.audio if each.key == dub_key(track.dub))
        name = container.name.removesuffix(".mkv") + f".{track.group}.ru.mka"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-map", f"0:a:{stream}", "-c", "copy",
                        disk.library(name)], check=True)
        beside.append(replace(track, file=name))
    own = next(each for each in held.audio if each.own)
    alone = path + ".alone.mkv"
    assert asyncio.run(mkv.build(alone, path, held.video, [
        mkv.AudioIn(own.label, path, own.index, own.aligned_to, own=True)])) is None
    os.replace(alone, path)
    disk.containers[container.name] = replace(container, tracks=tuple(beside))
    disk._write_table()

    step = disk.sync("упаковка дорожек, лежащих рядом")
    assert {op.kind for op in step.plan.ops} == {"build"}
    disk.check(step)
    assert len(asyncio.run(mkv.contents(path)).audio) == 3
    assert not any(os.path.exists(disk.library(each.file)) for each in beside)
    assert not any(track.file for track in visible(disk, 1).tracks)


def _replacement(world) -> None:
    episode(world, 1, 2)
    episode(world, 2, 1)
    world.check(world.sync("исходно"))
    world.drop(2, "g2s1e1")
    world.renumber(1, "ref12", 3)  # the reference of episode 2 is numbered 3 now
    world.put(3, "hd1", 1, group=REF, height=1080)
    world.choose(sources=(3, 1, 2))


def test_a_crash_before_the_journal_keeps_the_snapshot(tmp_path):
    world = DiskWorld(str(tmp_path))
    try:
        _replacement(world)
        before = dict(world.containers)
        world.crash_at = -1
        with pytest.raises(Crash):
            world.sync("обрыв до журнала")
        world.recover()
        assert world.containers == before
        world.check(world.settle())
    finally:
        world.close()


@pytest.mark.parametrize("step_no", range(12))
def test_a_crash_at_any_step_of_the_journal_is_carried_to_the_end(tmp_path, step_no):
    world = DiskWorld(str(tmp_path))
    try:
        _replacement(world)
        world.crash_at = step_no
        try:
            world.sync("обрыв на шаге журнала")
        except Crash:
            world.recover()
        world.crash_at = None
        world.check(world.settle())
    finally:
        world.close()


@pytest.mark.parametrize("seed", range(3))
def test_chaos_on_the_disk(tmp_path, seed):
    world = DiskWorld(str(tmp_path))
    try:
        Chaos(world, random.Random(seed), seasons=(1,), episodes=(1, 2, 3)).history(6)
    finally:
        world.close()


def test_a_compilation_as_a_voice_over_through_the_model_of_the_service(disk):
    """Д-19 on files: the model of the service finds the episode's stretch in the compilation by its sound."""
    disk.put(1, "single2", 2, group=REF, height=1080)
    disk.put(4, "comp", 1, end=3, group="A", height=720, player="kodik")
    step = disk.sync("склейка-озвучка")
    disk.check(step)
    assert [track.group for track in visible(disk, 2).tracks] == ["A"]


def test_an_episode_in_parts_on_the_disk(disk):
    for part in (1, 2):
        disk.put(1, f"ref-p{part}", 1, group=REF, part=part)
        disk.put(2, f"g1-p{part}", 1, group="G1", player="kodik", part=part)
    step = disk.sync("части серии")
    disk.check(step)
    assert sorted(disk.containers) == ["s01e01 pt1 [AniStar] 720p.mkv", "s01e01 pt2 [AniStar] 720p.mkv"]


def test_a_voice_switched_off_is_removed_from_the_container_on_the_disk(disk):
    """В-10 on files: the container is built anew without the track; the decoded tracks are still the episode's."""
    episode(disk, 1, 2)
    disk.check(disk.sync("исходно"))
    disk.choose(off_groups=frozenset({"G1"}))
    step = disk.sync("озвучка отключена")
    disk.check(step)
    assert [track.group for track in visible(disk, 1).tracks] == ["G2"] and len(disk.containers) == 1


def test_the_names_of_the_tracks_are_written_into_the_file_and_kept_by_a_retag(disk):
    """The user's names of the tracks are the titles of the sounds of the file, the groups stay its tags; a file switched
    off is moved and tagged anew and keeps them."""
    episode(disk, 1, 2)
    disk.check(disk.sync("исходно"))
    disk.choose(titles={"G1": "ПМ_G1", REF: "Оригинал"})
    step = disk.sync("имена дорожек")
    disk.check(step)
    assert {op.kind for op in step.plan.ops} == {"build"}
    name = visible(disk, 1).name
    held = asyncio.run(mkv.contents(disk.library(name)))
    assert [(track.label, track.title) for track in held.audio] == [
        (REF, "Оригинал"), ("G1", "ПМ_G1"), ("G2", "G2")]
    disk.choose(off_episodes=frozenset({(1, 1)}))
    step = disk.sync("серия выключена")
    disk.check(step)
    (hidden,) = disk.containers.values()
    held = asyncio.run(mkv.contents(disk.library(hidden.name)))
    assert hidden.off and [track.title for track in held.audio] == ["Оригинал", "ПМ_G1", "G2"]


def test_delete_of_the_only_video_leaves_the_file_as_it_is(disk):
    """В-9 on files: the sources give nothing of the episode any more — the file is not touched."""
    episode(disk, 1, 1)
    disk.check(disk.sync("исходно"))
    disk.drop(1, "ref11")
    disk.drop(2, "g1s1e1")
    name = visible(disk, 1).name
    disk.delete("video", name)
    step = disk.sync("удалить единственное видео")
    disk.check(step)
    assert not step.plan.ops and sorted(disk.containers) == [name]


def test_a_container_that_fails_to_build_leaves_its_episode_and_the_sync_applies_the_others(disk):
    """A build that fails (the check of the container read back) is of its group: the file of that episode stays as it
    was, the other episodes are applied, and the next sync builds it."""
    episode(disk, 1, 1)
    episode(disk, 2, 1)
    build = disk.files.build

    async def failing(made, group, results, report_for=None):
        if (1, 1) in group.built:
            raise BuildFailed(made.goal((1, 1)).name, "container_length_wrong")
        return await build(made, group, results, report_for)

    disk.files.build = failing
    step = disk.sync("сборка первой серии не удалась")
    disk.check(step)
    assert [each.video.episode for each in disk.containers.values()] == [(1, 2)]
    disk.files.build = build
    disk.check(disk.sync("снова"))
    assert visible(disk, 1).tracks and visible(disk, 2).tracks
