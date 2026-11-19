"""The container of an episode (core/container.py) on real ffmpeg: what is built reads back as asked, nothing is encoded
again, the order and the default track are those given, a container is rebuilt from itself, and a build that is not
what was asked says why."""
import asyncio
import os
import shutil
import subprocess

import pytest

from core import container
from core.container import AudioIn, SubtitleIn

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def make(path, *args) -> str:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args, str(path)], check=True)
    return str(path)


@pytest.fixture
def media(tmp_path):
    video = make(tmp_path / "ref.mp4", "-f", "lavfi", "-i", "testsrc=duration=3:size=160x90:rate=10",
                 "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-c:v", "mpeg4", "-c:a", "aac", "-shortest")
    dubs = {name: make(tmp_path / f"{name}.flac", "-f", "lavfi", "-i", f"sine=frequency={hz}:duration=3")
            for name, hz in (("ruchime", 500), ("anistar", 700))}
    other = make(tmp_path / "other.mp4", "-f", "lavfi", "-i", "testsrc=duration=5:size=160x90:rate=10",
                 "-f", "lavfi", "-i", "sine=frequency=900:duration=5", "-c:v", "mpeg4", "-c:a", "aac", "-shortest")
    return tmp_path, video, dubs, other


def codecs(path) -> list[str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
                         check=True, capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def test_a_container_holds_the_video_and_the_tracks_in_the_order_given_copied_as_they_are(media):
    tmp, video, dubs, _ = media
    out = str(tmp / "ep.mkv")
    tracks = [AudioIn("ruchime", dubs["ruchime"], 0, "1:a", language="rus"), AudioIn("ПВА ШОУ", video, 0, "1:a", language="chi"),
              AudioIn("anistar", dubs["anistar"], 0, "1:a")]
    assert asyncio.run(container.build(out, video, "1:a", tracks)) is None
    made = asyncio.run(container.contents(out))
    assert made.video == "1:a" and made.labels == ["ruchime", "ПВА ШОУ", "anistar"]
    assert [track.default for track in made.audio] == [True, False, False]
    assert [track.aligned_to for track in made.audio] == ["1:a"] * 3
    # Each track bears the language of its voice-over; one nobody gave a language to claims none (Matroska's «und»).
    assert [track.language for track in made.audio] == ["rus", "chi", "und"]
    assert codecs(out) == ["mpeg4", "flac", "aac", "flac"]  # nothing encoded again


def test_a_container_is_rebuilt_from_itself_in_a_new_order_and_without_a_track(media):
    tmp, video, dubs, _ = media
    first = str(tmp / "first.mkv")
    asyncio.run(container.build(first, video, "1:a", [AudioIn("ПВА ШОУ", video, 0, "1:a"),
                                                       AudioIn("ruchime", dubs["ruchime"], 0, "1:a"),
                                                       AudioIn("anistar", dubs["anistar"], 0, "1:a")]))
    again = str(tmp / "again.mkv")
    was = asyncio.run(container.contents(first))
    order = {track.label: track.index for track in was.audio}
    tracks = [AudioIn(label, first, order[label], "1:a") for label in ("anistar", "ПВА ШОУ")]
    assert asyncio.run(container.build(again, first, "1:a", tracks)) is None
    assert asyncio.run(container.contents(again)).labels == ["anistar", "ПВА ШОУ"]
    assert codecs(again) == ["mpeg4", "flac", "aac"]


def test_the_tracks_of_a_container_go_into_a_new_video_of_the_same_identity(media):
    """Point 6 of ЭТАП_11: the tracks are taken out of the old container and put next to the new video, not fetched again."""
    tmp, video, dubs, _ = media
    old = str(tmp / "old.mkv")
    asyncio.run(container.build(old, video, "1:a", [AudioIn("ПВА ШОУ", video, 0, "1:a"),
                                                     AudioIn("ruchime", dubs["ruchime"], 0, "1:a")]))
    better = make(tmp / "better.mp4", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x180:rate=10",
                  "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-c:v", "mpeg4", "-c:a", "aac", "-shortest")
    new = str(tmp / "new.mkv")
    assert asyncio.run(container.build(new, better, "1:a", [AudioIn("ПВА ШОУ", better, 0, "1:a"),
                                                             AudioIn("ruchime", old, 1, "1:a")])) is None
    found = asyncio.run(container.contents(new))
    assert found.labels == ["ПВА ШОУ", "ruchime"] and found.height == 180  # the height of the film the file holds


def test_the_title_of_a_track_is_the_users_name_and_its_group_stays_the_tag(media):
    tmp, video, dubs, _ = media
    out = str(tmp / "titled.mkv")
    assert asyncio.run(container.build(out, video, "1:a", [AudioIn("ПВА ШОУ", video, 0, "1:a", own=True),
                                                         AudioIn("ruchime", dubs["ruchime"], 0, "1:a", title="ПМ_ruchime")])) is None
    found = asyncio.run(container.contents(out))
    assert [(track.label, track.title) for track in found.audio] == [("ПВА ШОУ", "ПВА ШОУ"), ("ruchime", "ПМ_ruchime")]
    assert container.problem_of(found, "1:a", [("ПВА ШОУ", "ПВА ШОУ", "", True, "und"), ("ruchime", "ruchime", "", False, "und")],
                                None) == "container_tracks_wrong"  # a file with another title is not the one asked for


@pytest.mark.parametrize("case, expected", [
    ("unreadable", "container_unreadable"), ("video", "container_video_wrong"), ("tracks", "container_tracks_wrong"),
    ("language", "container_tracks_wrong"), ("length", "container_length_wrong"), ("good", None)])
def test_a_build_that_is_not_what_was_asked_says_why(media, case, expected):
    tmp, video, dubs, other = media
    out = str(tmp / "ep.mkv")
    asyncio.run(container.build(out, video, "1:a", [AudioIn("ПВА ШОУ", video, 0, "1:a")]))
    made = None if case == "unreadable" else asyncio.run(container.contents(out))
    identity = "2:b" if case == "video" else "1:a"
    signature = ([("ruchime", "ruchime", "", False, "und")] if case == "tracks" else [("ПВА ШОУ", "ПВА ШОУ", "", False, "und")]
                 if case != "language" else [("ПВА ШОУ", "ПВА ШОУ", "", False, "rus")])
    duration = 5.0 if case == "length" else 3.0
    assert container.problem_of(made, identity, signature, duration) == expected


@pytest.mark.parametrize("code, said, expected", [
    (-9, b"", "ffmpeg_failed: killed by signal 9"), (1, b"", "ffmpeg_failed: exit code 1"),
    (1, b"Invalid argument\n", "ffmpeg_failed: Invalid argument")])
def test_an_ffmpeg_that_fails_says_what_it_said_or_how_it_ended(media, monkeypatch, code, said, expected):
    """A build the system killed (no memory) leaves no word of ffmpeg's: the reason in the log is how it ended, not an empty text."""
    tmp, video, _dubs, _ = media

    async def failing(cmd, **kwargs):
        return code, said

    monkeypatch.setattr(container, "run_lines", failing)
    assert asyncio.run(container.build(str(tmp / "ep.mkv"), video, "1:a", [AudioIn("ПВА ШОУ", video, 0, "1:a")])) == expected


def test_a_file_not_built_here_reads_with_no_identity_and_its_tracks_by_title(media):
    _, video, _, _ = media
    found = asyncio.run(container.contents(video))
    assert found.has_video and found.video == "" and len(found.audio) == 1 and found.duration > 2.5
    assert asyncio.run(container.contents(str(video) + ".missing")) is None


def test_a_container_carries_its_provenance_and_gives_it_back(media):
    """Stage 12: the table of what the container holds travels inside it, through a rebuild too."""
    tmp, video, dubs, _ = media
    table = {"video": {"source": "сайт", "row": "1"}, "tracks": [{"voice": "ruchime", "aligned_to": "1:a"}]}
    first = str(tmp / "first.mkv")
    assert asyncio.run(container.build(first, video, "1:a", [AudioIn("ПВА ШОУ", video, 0, "1:a", own=True),
                                                            AudioIn("ruchime", dubs["ruchime"], 0, "1:a")],
                                       provenance=table)) is None
    assert asyncio.run(container.provenance_of(first)) == table
    again = str(tmp / "again.mkv")
    newer = {**table, "rev": 2}
    assert asyncio.run(container.build(again, first, "1:a", [AudioIn("ПВА ШОУ", first, 0, "1:a", own=True)],
                                       provenance=newer)) is None
    assert asyncio.run(container.provenance_of(again)) == newer
    assert asyncio.run(container.provenance_of(video)) is None  # a file not built here carries none
    assert sorted(os.listdir(tmp)) == sorted(set(os.listdir(tmp)) - {"first.mkv.provenance.json"})


def _torrent_video(tmp) -> str:
    """A Matroska file as a release gives it: a picture, a sound, ASS subtitles in two languages (one forced), a font they
    are drawn with, and two chapters."""
    ass = tmp / "signs.ass"
    ass.write_text("[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize\nStyle: Default,Kurinto,20\n\n"
                   "[Events]\nFormat: Layer, Start, End, Style, Text\nDialogue: 0,0:00:00.50,0:00:01.50,Default,Надпись\n",
                   encoding="utf-8")
    font = tmp / "Kurinto.ttf"
    font.write_bytes(b"\x00\x01\x00\x00" + b"font" * 64)
    chapters = tmp / "chapters.txt"
    chapters.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1500\ntitle=Пролог\n"
                        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1500\nEND=3000\ntitle=Серия\n", encoding="utf-8")
    return make(tmp / "E01.mkv", "-f", "lavfi", "-i", "testsrc=duration=3:size=160x90:rate=10",
                "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-i", str(ass), "-i", str(ass), "-i", str(chapters),
                "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-map_chapters", "4", "-c:v", "mpeg4", "-c:a", "aac",
                "-c:s", "copy", "-metadata:s:s:0", "language=rus", "-metadata:s:s:0", "title=Надписи",
                "-disposition:s:0", "forced", "-metadata:s:s:1", "language=eng",
                "-attach", str(font), "-metadata:s:t", "mimetype=application/x-truetype-font",
                "-metadata:s:t", "filename=Kurinto.ttf")


def test_the_chosen_subtitles_go_in_their_order_with_the_fonts_of_their_file_and_the_chapters_of_the_video(media):
    """DESIGN.md 7.1.1: the subtitles are the tracks the plan chose, in its order (here the second of the file first), each
    with its key, its name, its language and the flag «forced»; the fonts of the file they come from go with them, the
    chapters are the video's; what the plan did not choose stays out."""
    from core.ffprobe import layout_of
    tmp, _video, dubs, _ = media
    video = _torrent_video(tmp)
    out = str(tmp / "built.mkv")
    tracks = [AudioIn("Оригинал", video, 0, "1:E01", own=True), AudioIn("ruchime", dubs["ruchime"], 0, "1:E01")]
    chosen = [SubtitleIn(video, 1, "1:full:", "Full", "eng", default=True),
              SubtitleIn(video, 0, "1:signs:", "Надписи", "rus", forced=True)]
    assert asyncio.run(container.build(out, video, "1:E01", tracks, provenance={"video": "E01"}, subtitles=chosen)) is None

    layout, _ = asyncio.run(layout_of(out))
    subtitles = [s for s in layout["streams"] if s["kind"] == "subtitle"]
    attached = [s["filename"] for s in layout["streams"] if s["kind"] == "attachment"]
    assert [(s["codec"], s["language"], s["title"], s["forced"]) for s in subtitles] == [
        ("ass", "eng", "Full", False), ("ass", "rus", "Надписи", True)]
    assert [(each.key, each.title, each.default, each.forced) for each in asyncio.run(container.contents(out)).subtitles] == [
        ("1:full:", "Full", True, False), ("1:signs:", "Надписи", False, True)]
    assert sorted(attached) == ["Kurinto.ttf", container.PROVENANCE]
    assert layout["chapters"] == 2

    none = str(tmp / "none.mkv")  # no subtitles chosen: none of the file's go, nor their fonts; the chapters stay
    assert asyncio.run(container.build(none, video, "1:E01", tracks[:1], provenance={"video": "E01"})) is None
    layout, _ = asyncio.run(layout_of(none))
    assert [s["kind"] for s in layout["streams"] if s["kind"] in ("subtitle", "attachment")] == ["attachment"]
    assert layout["chapters"] == 2

    again = str(tmp / "again.mkv")  # a container rebuilt from itself takes its subtitles by their keys, the table is one
    held = asyncio.run(container.contents(out)).subtitles
    assert asyncio.run(container.build(again, out, "1:E01", [AudioIn("Оригинал", out, 0, "1:E01", own=True)],
                                       provenance={"video": "E01"},
                                       subtitles=[SubtitleIn(out, each.index, each.key, each.title, each.language,
                                                             forced=each.forced, default=each.default) for each in held])) is None
    layout, _ = asyncio.run(layout_of(again))
    assert sorted(s["filename"] for s in layout["streams"] if s["kind"] == "attachment") == ["Kurinto.ttf", container.PROVENANCE]
    assert [(each.key, each.default, each.forced) for each in asyncio.run(container.contents(again)).subtitles] == [
        ("1:full:", True, False), ("1:signs:", False, True)]
    assert layout["chapters"] == 2


def test_a_stream_with_one_event_in_the_whole_episode_does_not_hold_the_muxer(media, monkeypatch):
    """The signs of an episode may be one event in all of it. The interleave was unbounded (`-max_interleave_delta 0`): the muxer
    waited for the next packet of that stream and kept everything else in memory to the end of the file, so an episode of 48
    minutes with 31 voice-overs filled the machine and ffmpeg was killed with no word. The interleave is bounded now."""
    tmp, _video, dubs, _ = media
    cmds = []
    real = container.run_lines

    async def seen(cmd, **kwargs):
        cmds.append(cmd)
        return await real(cmd, **kwargs)

    monkeypatch.setattr(container, "run_lines", seen)
    video = _torrent_video(tmp)  # its signs are one event of 1 s in 3 s
    out = str(tmp / "sparse.mkv")
    tracks = [AudioIn("Оригинал", video, 0, "1:E01", own=True), AudioIn("ruchime", dubs["ruchime"], 0, "1:E01")]
    chosen = [SubtitleIn(video, 0, "1:signs:", "Надписи", "rus", forced=True)]
    assert asyncio.run(container.build(out, video, "1:E01", tracks, provenance={"video": "E01"}, subtitles=chosen)) is None
    (cmd,) = [each for each in cmds if "-max_interleave_delta" in each]
    assert 0 < int(cmd[cmd.index("-max_interleave_delta") + 1]) <= container.INTERLEAVE_DELTA_US
    assert [each.key for each in asyncio.run(container.contents(out)).subtitles] == ["1:signs:"]


def test_a_video_of_another_container_carries_its_chapters_and_no_subtitles(media):
    tmp, video, dubs, _ = media
    out = str(tmp / "plain.mkv")
    assert asyncio.run(container.build(out, video, "1:x", [AudioIn("a", video, 0, "1:x", own=True)])) is None
    assert codecs(out) == ["mpeg4", "aac"]


def test_external_subtitles_take_the_font_files_beside_them_once_each(media):
    """DESIGN.md 7.1: an ASS file of a release is drawn with the font files that lie beside it; they are attached to the
    container, each name once, after the attachments of the files the subtitles come from."""
    from core.ffprobe import layout_of
    tmp, video, dubs, _ = media
    ass = tmp / "ext.ass"
    ass.write_text("[Script Info]\nScriptType: v4.00+\n\n[Events]\nFormat: Layer, Start, End, Style, Text\n"
                   "Dialogue: 0,0:00:00.50,0:00:01.50,Default,Надпись\n", encoding="utf-8")
    fonts = []
    for name in ("Arial.ttf", "Bold.otf"):
        (tmp / name).write_bytes(b"\x00\x01\x00\x00" + b"font" * 64)
        fonts.append(str(tmp / name))
    out = str(tmp / "ext.mkv")
    chosen = [SubtitleIn(str(ass), 0, "1:ext:", "Полные", "rus", fonts=tuple(fonts)),
              SubtitleIn(str(ass), 0, "1:ext2:", "Копия", "rus", fonts=tuple(fonts[:1]))]
    assert asyncio.run(container.build(out, video, "1:x", [AudioIn("a", video, 0, "1:x", own=True)], provenance={"video": "x"},
                                       subtitles=chosen)) is None
    layout, _ = asyncio.run(layout_of(out))
    assert sorted(s["filename"] for s in layout["streams"] if s["kind"] == "attachment") == [
        "Arial.ttf", "Bold.otf", container.PROVENANCE]
    assert [each.key for each in asyncio.run(container.contents(out)).subtitles] == ["1:ext:", "1:ext2:"]
