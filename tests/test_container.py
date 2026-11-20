"""The container of an episode (core/container.py) on real ffmpeg: what is built reads back as asked, nothing is encoded
again, the order and the default track are those given, a container is rebuilt from itself, and a build that is not
what was asked says why."""
import asyncio
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


def _torrent_video(tmp, seconds: int = 3) -> str:
    """A Matroska file as a release gives it: a picture, a sound, ASS subtitles in two languages (one forced), a font they
    are drawn with, and two chapters. Each subtitles stream is one event of a second, wherever the episode ends."""
    ass = tmp / "signs.ass"
    ass.write_text("[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize\nStyle: Default,Kurinto,20\n\n"
                   "[Events]\nFormat: Layer, Start, End, Style, Text\nDialogue: 0,0:00:00.50,0:00:01.50,Default,Надпись\n",
                   encoding="utf-8")
    font = tmp / "Kurinto.ttf"
    font.write_bytes(b"\x00\x01\x00\x00" + b"font" * 64)
    chapters = tmp / "chapters.txt"
    chapters.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1500\ntitle=Пролог\n"
                        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1500\nEND=3000\ntitle=Серия\n", encoding="utf-8")
    return make(tmp / "E01.mkv", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x90:rate=10",
                "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}", "-i", str(ass), "-i", str(ass), "-i", str(chapters),
                "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-map_chapters", "4", "-c:v", "mpeg4", "-c:a", "aac",
                "-c:s", "copy", "-metadata:s:s:0", "language=rus", "-metadata:s:s:0", "title=Надписи",
                "-disposition:s:0", "forced", "-metadata:s:s:1", "language=eng",
                "-attach", str(font), "-metadata:s:t", "mimetype=application/x-truetype-font",
                "-metadata:s:t", "filename=Kurinto.ttf")


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
