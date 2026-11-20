"""The planner of a series in a world of tables (tests/plan_world.py): every situation is only other inputs, and after
every sync the invariants of the model hold (docs/missions/voice-alignment/stage-12/episode-table/MODEL.md)."""


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


def test_a_cut_the_service_fell_on_is_no_record_and_comes_next_time():
    """A cut the service fell on judged nothing of the compilation: the episode takes its single video for this sync,
    nothing is recorded, and the next sync cuts it again."""
    world = World()
    world.put(1, "single3", 3, group=REF, player="cvh")
    world.put(4, "comp", 1, end=5, group=REF, height=2160)
    world.cut_answers[(1, 3)] = "crash"
    step = world.sync("разбор: служба упала")
    world.check(step)
    assert container_of(world, 3).video.cut_of is None and not world.failed and not step.replanned
    world.cut_answers.clear()
    step = world.sync("служба больше не падает")
    world.check(step)
    assert container_of(world, 3).video.cut_of is not None


# --- faults, one kind at a time --------------------------------------------------------------------------------------

# --- the service's own rule, the levers, compilations as voice-overs, parts ------------------------------------------

# --- the work is of each episode: a group is applied when its own steps have ended -------------------------------------
