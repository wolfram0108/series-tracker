"""The planner of the table of an episode on a disk (tests/plan_disk.py): real files made by ffmpeg, the service's own
downloader and container builder, the service's own client talking to a model of the alignment service, the journal of
the application, crashes at every step, adoption from the files."""

import pytest

from plan_disk import DiskWorld

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


def test_a_voice_switched_off_is_removed_from_the_container_on_the_disk(disk):
    """В-10 on files: the container is built anew without the track; the decoded tracks are still the episode's."""
    episode(disk, 1, 2)
    disk.check(disk.sync("исходно"))
    disk.choose(off_groups=frozenset({"G1"}))
    step = disk.sync("озвучка отключена")
    disk.check(step)
    assert [track.group for track in visible(disk, 1).tracks] == ["G2"] and len(disk.containers) == 1
