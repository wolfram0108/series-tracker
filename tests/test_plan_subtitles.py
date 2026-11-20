"""The subtitles of the plan (stage 15, DESIGN.md 7.1.1): the rows of subtitles of a torrent file are tracks of the container
in the order of the user's table, taken from their file when they are on the video's timeline, kept from the file that
holds them, and never taken for a voice-over or a video."""
from dataclasses import replace

from core.plan.files import container_json, container_of
from core.plan.planner import plan
from core.plan.tables import Choices, Container, Inputs, OnlineRow, RowId, Snapshot, Subtitle, Video

TIMELINE = RowId(1, "page/E01.mkv", "")
OTHER = RowId(2, "other/E01.mkv", "")


def sound(stream: int, group: str, position: int, source: int = 1, file: str = "page/E01.mkv") -> OnlineRow:
    return OnlineRow(RowId(source, f"{file}#{stream}"), 1, 1, 1, f"torrent:{file.split('/')[0]}", group, 1080,
                     link=f"torrent:{file}#{stream}", position=position, timeline_key=file)


def subtitles(name: str, stream: int, position: int, source: int = 1, file: str = "page/E01.mkv",
              language: str = "rus", kind: str = "full", playable: bool = True) -> OnlineRow:
    return OnlineRow(RowId(source, f"{file}#s{stream}"), 1, 1, 1, f"torrent:{file.split('/')[0]}", "", 0,
                     link=f"torrent:{file}#{stream}", label=name, position=position, timeline_key=file,
                     track="subtitles", language=language, subtitle_type=kind, playable=playable)


ROWS = (sound(1, "Оригинал", 0), subtitles("Полные", 0, 1), subtitles("Надписи", 1, 2, kind="signs"))


def goal_of(rows=ROWS, containers=(), **choices):
    return plan(Inputs(rows, containers, Choices(groups={1: ("Оригинал",)}, sources=(1, 2), **choices))).goal((1, 1))


def held(name: str, items: tuple[Subtitle, ...]) -> Container:
    video = Video(RowId(1, "page/E01.mkv#1"), Snapshot("page", "torrent:page", "Оригинал", 1080), (1, 1), TIMELINE)
    return Container(name, video, subtitles=items)


def test_the_rows_of_subtitles_are_no_voice_overs_and_no_video_and_go_in_the_order_of_the_layout():
    goal = goal_of()
    assert goal.video.kind == "download" and goal.tracks == ()
    assert [(way.subtitle.name, way.kind, way.subtitle.subtitle_type) for way in goal.subtitles] == [
        ("Полные", "take", "full"), ("Надписи", "take", "signs")]
    made = plan(Inputs(ROWS, (), Choices(groups={1: ("Оригинал",)})))
    assert [op.kind for op in made.ops if op.kind == "take_subtitles"] == ["take_subtitles", "take_subtitles"]
    # Rows given among the rows of sound are set apart: no reader of `online` sees them.
    inputs = Inputs(ROWS, (), Choices())
    assert [row.label for row in inputs.subtitles] == ["Полные", "Надписи"] and len(inputs.online) == 1


def test_the_users_order_puts_his_variants_first_and_a_variant_he_left_out_is_no_track():
    order = {(1, 1): (("Надписи", 1, "torrent:page"),)}
    assert [way.subtitle.name for way in goal_of(subtitle_order=order).subtitles] == ["Надписи", "Полные"]
    deny = {(1, 1): frozenset({("Полные", 1, "torrent:page")})}
    assert [way.subtitle.name for way in goal_of(subtitle_deny=deny).subtitles] == ["Надписи"]


def test_subtitles_of_another_timeline_wait_for_their_alignment_and_are_told():
    rows = ROWS + (subtitles("Full", 0, 3, source=2, file="other/E01.mkv", language="eng"),)
    goal = goal_of(rows)
    assert [way.subtitle.name for way in goal.subtitles] == ["Полные", "Надписи"]
    assert goal.unaligned == (("Full", 2, "torrent:other"),)


def test_what_the_file_holds_on_the_timeline_stays_and_a_file_that_is_the_goal_is_not_built_again():
    items = tuple(Subtitle(row.id, row.label, row.player, row.timeline, row.language, row.subtitle_type,
                           default=row.subtitle_type == "full", forced=row.subtitle_type == "signs") for row in ROWS[1:])
    file = held("s01e01.mkv", items)
    goal = goal_of(containers=(file,))
    assert goal.video.kind == "asset"
    assert [(way.kind, way.container) for way in goal.subtitles] == [("reuse", file), ("reuse", file)]
    made = plan(Inputs(ROWS, (file,), Choices(groups={1: ("Оригинал",)}, sources=(1, 2))))
    assert not made.ops and made.groups[0].kept == (((1, 1), "s01e01.mkv"),)
    # Another order is another file: built again, its subtitles taken from the file that holds them.
    swapped = plan(Inputs(ROWS, (file,), Choices(groups={1: ("Оригинал",)}, sources=(1, 2),
                                                subtitle_order={(1, 1): (("Надписи", 1, "torrent:page"),)})))
    assert [op.kind for op in swapped.ops] == ["build"]
    assert [way.subtitle.name for way in swapped.goal((1, 1)).subtitles] == ["Надписи", "Полные"]


def test_subtitles_whose_release_is_archived_are_kept_from_the_file_that_holds_them():
    """After a clean sync the files of a release are dropped (archived): its rows stand, not playable; the container is the
    one place the subtitles are, and they stay."""
    archived = tuple(row.replaced(playable=False) for row in ROWS)
    items = (Subtitle(ROWS[1].id, "Полные", "torrent:page", TIMELINE, "rus", "full"),)
    file = held("s01e01.mkv", items)
    goal = goal_of(archived, containers=(file,))
    assert [(way.subtitle.name, way.kind) for way in goal.subtitles] == [("Полные", "reuse")]


def test_a_file_whose_subtitles_the_goal_does_not_carry_is_hidden_unless_the_user_left_them_out():
    items = (Subtitle(ROWS[1].id, "Полные", "torrent:page", TIMELINE, "rus", "full"),)
    old = held("s01e01 old.mkv", items)
    # Another video of another timeline becomes the reference: the subtitles of the old file are on no timeline of it.
    rows = (sound(1, "Оригинал", 0, source=2, file="other/E01.mkv"),) + ROWS[1:]
    made = plan(Inputs(rows, (old,), Choices(groups={1: ("Оригинал",)}, sources=(2, 1),
                                             reference={(1, 1): (("Оригинал", 2, "torrent:other"),)})))
    group = made.groups[0]
    assert "s01e01 old.mkv" in dict(group.hide) and group.remove == ()
    deny = {(1, 1): frozenset({("Полные", 1, "torrent:page"), ("Надписи", 1, "torrent:page")})}
    made = plan(Inputs(rows, (old,), Choices(groups={1: ("Оригинал",)}, sources=(2, 1), subtitle_deny=deny,
                                             reference={(1, 1): (("Оригинал", 2, "torrent:other"),)})))
    assert made.groups[0].remove == ("s01e01 old.mkv",)


def test_the_first_full_track_is_the_default_one_and_the_first_of_signs_the_forced_one_with_nothing_set():
    """How a track lies in the container is the standard of a player, not a setting: the first full track taken is the
    default one, the first track of signs the forced one, whatever order the user gave."""
    flags = lambda goal: [(way.subtitle.name, way.subtitle.default, way.subtitle.forced) for way in goal.subtitles]  # noqa: E731
    assert flags(goal_of()) == [("Полные", True, False), ("Надписи", False, True)]
    rows = ROWS + (subtitles("Полные 2", 2, 3), subtitles("Надписи 2", 3, 4, kind="signs"), subtitles("Без типа", 4, 5, kind=""))
    order = {(1, 1): (("Надписи 2", 1, "torrent:page"), ("Полные 2", 1, "torrent:page"))}
    assert flags(goal_of(rows, subtitle_order=order)) == [("Надписи 2", False, True), ("Полные 2", True, False),
                                                           ("Полные", False, False), ("Надписи", False, False),
                                                           ("Без типа", False, False)]
    # A variant left out lays nothing: the next one of its type takes its place.
    deny = {(1, 1): frozenset({("Полные", 1, "torrent:page")})}
    assert flags(goal_of(rows, subtitle_deny=deny)) == [("Надписи", False, True), ("Полные 2", True, False),
                                                         ("Надписи 2", False, False), ("Без типа", False, False)]
    # A file whose tracks lie otherwise is built again from what it holds; one that lies so stays.
    items = tuple(Subtitle(row.id, row.label, row.player, row.timeline, row.language, row.subtitle_type) for row in ROWS[1:])
    made = plan(Inputs(ROWS, (held("s01e01.mkv", items),), Choices(groups={1: ("Оригинал",)}, sources=(1, 2))))
    assert [op.kind for op in made.ops] == ["build"]
    assert [(way.kind, way.subtitle.default, way.subtitle.forced) for way in made.goal((1, 1)).subtitles] == [
        ("reuse", True, False), ("reuse", False, True)]
    lying = held("s01e01.mkv", (replace(items[0], default=True), replace(items[1], forced=True)))
    assert not plan(Inputs(ROWS, (lying,), Choices(groups={1: ("Оригинал",)}, sources=(1, 2)))).ops


def test_the_table_of_the_asset_keeps_the_subtitles_of_a_container():
    file = held("s01e01.mkv", (Subtitle(RowId(1, "page/E01.mkv#s0"), "Полные", "torrent:page", TIMELINE, "rus", "full"),
                               Subtitle(RowId(1, "page/E01.mkv#s1"), "Надписи", "torrent:page", TIMELINE, "rus", "signs",
                                        default=True, forced=True)))
    assert container_of(container_json(file)) == file
