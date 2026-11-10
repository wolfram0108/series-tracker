"""The tracks of a card of the packing (modules/planner/packing.py): the container's tracks in its order as numbers of the
season, where each comes from, the tracks of the file before it does not hold, and why a container is built when no track
came or went."""
from types import SimpleNamespace as N

from core.contracts import PackCard
from modules.planner.packing import PackBook, tracks_of

ORDER = ("Ref", "A", "B", "C")


def way(voice, kind="reuse"):
    return N(voice=voice, kind=kind, snapshot=N(group=voice))


def goal(ways, own_at=0, video_id="v1"):
    return N(tracks=tuple(ways), own_at=own_at,
             video=N(snapshot=N(group="Ref"), container=N(video=N(id=video_id))))


def before(*labels, video_id="v1"):
    return N(tracks=[N(snapshot=N(group=label)) for label in labels], video=N(id=video_id))


def kinds(tracks):
    return [(track.number, track.label, track.kind) for track in tracks]


def test_a_track_made_in_the_sync_is_new_one_the_file_held_is_kept_and_one_it_loses_is_gone():
    tracks, reason = tracks_of(goal([way("A"), way("C", "download")]), before("A", "B"), ORDER)
    assert kinds(tracks) == [(1, "Ref", "own"), (2, "A", "kept"), (4, "C", "new"), (3, "B", "gone")] and reason == ""


def test_a_container_with_the_same_tracks_is_built_for_the_order_the_video_or_the_names():
    assert tracks_of(goal([way("B"), way("A")]), before("A", "B"), ORDER)[1] == "order"
    assert tracks_of(goal([way("A"), way("B")], video_id="v2"), before("A", "B"), ORDER)[1] == "video"
    assert tracks_of(goal([way("A"), way("B")]), before("A", "B"), ORDER)[1] == "names"


def test_a_voice_over_the_order_of_the_season_has_not_has_no_number():
    tracks, _ = tracks_of(goal([way("Z", "download")], own_at=1), None, ORDER)
    assert kinds(tracks) == [(None, "Z", "new"), (1, "Ref", "own")]


def test_a_built_container_waits_for_the_journal_in_no_stage_and_the_put_in_place_counts_from_the_journal():
    now = [0.0]
    book = PackBook(clock=lambda: now[0], wall=lambda: "t")
    for series_id in (1, 2):
        book.cards[f"{series_id}:s01e01"] = PackCard(id=f"{series_id}:s01e01", series_id=series_id, name=f"{series_id}.mkv")
        book._since[f"{series_id}:s01e01"] = 0.0
        book._steps[f"{series_id}:s01e01"] = {f"{series_id}.mkv"}
        book._enter(book.cards[f"{series_id}:s01e01"], "build")
    now[0] = 10.0
    book.built("1:s01e01")
    book.built("2:s01e01")
    card = book.cards["1:s01e01"]
    assert card.status == "built" and not [stage for stage in card.stages if stage.state == "active"]
    now[0] = 155.0
    assert book.view()[0].figures.wait_s == 145.0
    book.journal(1, [("publish", "1.mkv")])
    assert card.status == "apply" and card.figures.wait_s == 145.0 and card.figures.steps_of == 1
    assert book.cards["2:s01e01"].status == "built", "the journal of one series does not move the cards of another"
    now[0] = 157.0
    book.applied(1, lambda name: 10.0)
    assert {stage.name: stage.seconds for stage in card.stages} == {"queue": 0.0, "build": 10.0, "verify": 0.0, "apply": 2.0}


def test_a_build_that_failed_gives_its_card_the_code_of_the_problem_and_one_that_waited_gives_none():
    book = PackBook(clock=lambda: 0.0, wall=lambda: "t")
    for key in ("1:s01e01", "1:s01e02"):
        book.cards[key] = PackCard(id=key, series_id=1, name=f"{key}.mkv")
        book._since[key] = 0.0
        book._enter(book.cards[key], "build")
    book.failed(["1:s01e01"], "container_length_wrong")
    book.waited(["1:s01e02"])
    first, second = book.cards["1:s01e01"], book.cards["1:s01e02"]
    assert (first.status, first.failure, first.waited) == ("failed", "container_length_wrong", False)
    assert (second.status, second.failure, second.waited) == ("failed", "", True)


def test_the_journal_of_a_series_is_shown_step_by_step_whichever_episode_its_files_are_and_goes_after_the_last():
    now = [0.0]
    book = PackBook(clock=lambda: now[0], wall=lambda: "2026-11-11T02:50:00+00:00")
    steps = [("move", "e331.mkv", "e331.mkv.moving"), ("move", "e331.mkv.moving", "e331.mkv.off"),
             ("publish", ".plan-staging/e330.mkv", "e330.mkv"), ("retag", "e331.mkv.off")]
    book.journal_begins({"id": 40, "name": "Владыка"}, "run-1", steps)
    assert book.running()
    for done, step in enumerate(steps):
        book.journal_step(40, step)
        now[0] += 30
        (shown,) = book.journals_view()
        assert (shown.series_id, shown.series_name, shown.kind, shown.steps_of, shown.steps_done) == (40, "Владыка", "sync", 4, done)
        assert shown.step == step[0] and shown.seconds == now[0]
        assert shown.file == {0: "e331.mkv", 1: "e331.mkv.off", 2: "e330.mkv", 3: "e331.mkv.off"}[done]
        book.journal_step_done(40)
    assert [each.file for each in [book.journals_view()[0]]] == ["e331.mkv.off"]
    book.journal_ends(40)
    assert book.journals_view() == [] and not book.running()


async def test_the_planner_tells_each_step_of_a_journal_as_it_carries_it_out_and_that_the_journal_ended(tmp_path):
    from core import Bus
    from core.db import Database
    from modules.planner.module import PlannerModule
    from schema_db import migrated

    bus = Bus()
    planner = PlannerModule(bus, Database(str(migrated(tmp_path / "p.db"))))
    told = bus.subscribe("planner.journal.changed")
    seen = []

    class Files:
        async def step(self, step, _table):
            seen.append([(each.step, each.file, each.steps_done) for each in planner.packing.journals_view()])

    steps = [("move", "e331.mkv", "e331.mkv.moving"), ("retag", "e331.mkv.off")]
    planner.packing.journal_begins({"id": 40, "name": "Владыка"}, "run-1", steps)
    await planner.carry_out(40, Files(), steps, [])
    assert seen == [[("move", "e331.mkv", 0)], [("retag", "e331.mkv.off", 1)]]
    packets = []
    while not told.queue.empty():
        packets.append(told.queue.get_nowait().payload["journals"])
    assert packets[-1] == []
