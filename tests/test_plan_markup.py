"""The markup reference of a series (stage 14, docs/missions/voice-alignment/stage-14/DESIGN.md) in the world of the
planner (tests/plan_world.py): the events С-1…С-20 of the design one by one, then random histories around the mark. After
every sync the world holds its invariants, the markup ones (Э-2…Э-7) among them."""


from core.plan.tables import RowId
from modules.planner.view import view_of
from plan_world import World

PVA, ORIGINAL = "ПВА", "Оригинал"
PICTURE, MARKUP, DUBS, OTHER = 1, 3, 2, 5  # the sources: the high picture, the markup reference, voice-overs, a second original
THE_ORIGINAL = (ORIGINAL, MARKUP, "cvh")  # the variant the owner puts first in the queue of the markup reference


def queue(*variants, episodes=(1, 2, 3)) -> dict:
    """The queue of the markup reference set on the series: every episode inherits it."""
    return {(1, episode): tuple(variants) for episode in episodes}


def world_of(*, markup: tuple | None = THE_ORIGINAL, dubs: int = 2, episodes=(1,), pva: int = 2160, original: int = 1080) -> World:
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
    world.choose(markup=queue(markup) if markup is not None else {})
    return world


def synced(world: World, title: str):
    step = world.sync(title)
    world.check(step)
    return step


def file_of(world: World, episode: int = 1):
    found = [each for each in world.containers.values() if f"e{episode:02d}" in each.name and not each.off]
    assert len(found) == 1, sorted(world.containers)
    return found[0]


def row(source: int, key: str) -> RowId:
    return RowId(source, key, key)


# --- the window is told the variant of the markup reference the plan takes and the one the file lies on ---------------

def told(world: World, step) -> dict:
    return view_of(0, world, step.plan, running=False, rows=step.online)


# --- С-1, С-2: the mark ------------------------------------------------------------------------------------------

# --- С-3, Э-6: another picture on the same timeline --------------------------------------------------------------

# --- С-4…С-7: failures -------------------------------------------------------------------------------------------

# --- С-9…С-13: the mark taken off, moved, the row gone or changed -------------------------------------------------

# --- С-14, С-15: the picture gone, «Удалить» ---------------------------------------------------------------------

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


# --- the class fixed on the way: the sound of the timeline of a compilation cut -----------------------------------

# The seeds of every action of the simulator, with a queue of the markup reference on every episode, that broke the invariants
# once (two files under one number after a renumbering of a source, a row downloaded twice when a plan in between did not ask
# it, a deleted video that waits and a title the user gave later): each stays as a history of its own.
# --- the timeline a cut file lies on is known by the file ------------------------------------------------------------

# --- random histories around the mark ------------------------------------------------------------------------------

# The seeds after the first eighty that broke the invariants once (a file of a cut that forgot its timeline, a cut of a picture
# no higher than the timeline that cannot be downloaded): each stays as a history of its own.
