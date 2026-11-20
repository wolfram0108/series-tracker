"""The subtitles of the plan (stage 15, DESIGN.md 7.1.1): the rows of subtitles of a torrent file are tracks of the container
in the order of the user's table, taken from their file when they are on the video's timeline, kept from the file that
holds them, and never taken for a voice-over or a video."""

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


def held(name: str, items: tuple[Subtitle, ...]) -> Container:
    video = Video(RowId(1, "page/E01.mkv#1"), Snapshot("page", "torrent:page", "Оригинал", 1080), (1, 1), TIMELINE)
    return Container(name, video, subtitles=items)


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
