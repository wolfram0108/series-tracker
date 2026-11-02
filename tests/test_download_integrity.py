"""A download that says it is done is a finished one only when the video has no hole: ffmpeg skips a segment it cannot open
and still exits 0, and the audio and the length of the file stay whole, so only the pauses between the frames tell."""
import os
import shutil
import subprocess

import pytest

from core.ffprobe import video_gaps
from modules.downloads import ytdlp
from modules.downloads.module import _transient_download_error

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg is not installed")


def make(path, *args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(path)], check=True)


@pytest.fixture
def clips(tmp_path):
    whole, holed, sound = tmp_path / "whole.mp4", tmp_path / "holed.mp4", tmp_path / "sound.m4a"
    make(whole, "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=10", "-c:v", "libx264", "-pix_fmt", "yuv420p")
    # The frames of seconds 3-6 are taken out and the rest keep their time: a hole in the video, as a skipped segment leaves.
    make(holed, "-i", str(whole), "-vf", "select='not(between(t,3,6))'", "-fps_mode", "passthrough", "-c:v", "libx264",
         "-pix_fmt", "yuv420p")
    make(sound, "-f", "lavfi", "-i", "sine=duration=3", "-c:a", "aac")
    return whole, holed, sound


@pytest.mark.asyncio
async def test_a_hole_in_the_video_is_found_with_its_place_and_length_and_a_whole_file_has_none(clips):
    whole, holed, sound = clips
    assert await video_gaps(str(whole), longer_than=1.0) == []
    [(at, length)] = await video_gaps(str(holed), longer_than=1.0)
    assert round(at, 2) == 2.96 and round(length, 2) == 3.08
    assert await video_gaps(str(sound), longer_than=1.0) == []  # a file with no video has no hole to find


@pytest.mark.asyncio
async def test_a_file_ffprobe_cannot_read_is_not_taken_for_a_file_with_a_hole(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert await video_gaps(str(broken), longer_than=1.0) == []
    assert await video_gaps(str(tmp_path / "missing.mp4"), longer_than=1.0) == []


@pytest.mark.asyncio
async def test_a_download_with_a_hole_is_discarded_and_is_a_failure_the_queue_tries_again(clips, tmp_path, monkeypatch):
    whole, holed, _ = clips
    target = str(tmp_path / "out" / "episode.mp4")

    async def writes(holed_file):
        async def fake(*_args, **_kwargs):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy(holed_file, target)
            return True, ""
        return fake

    monkeypatch.setattr(ytdlp, "_download_owned", await writes(holed))
    ok, error = await ytdlp.download("https://cdn.test/v.m3u8", target, lambda _data: None, whole_video=True)
    assert not ok and "обрыв" in error and "неполная" in error and _transient_download_error(error)
    assert not os.path.exists(target)

    # A voice-over is not asked to be whole: the service puts a cut of it right as silence.
    ok, error = await ytdlp.download("https://cdn.test/v.m3u8", target, lambda _data: None)
    assert ok and os.path.exists(target)
    os.remove(target)

    monkeypatch.setattr(ytdlp, "_download_owned", await writes(whole))
    ok, error = await ytdlp.download("https://cdn.test/v.m3u8", target, lambda _data: None, whole_video=True)
    assert ok and error == "" and os.path.exists(target)


@pytest.mark.asyncio
async def test_a_failed_download_is_not_looked_at_for_holes(tmp_path, monkeypatch):
    async def failed(*_args, **_kwargs):
        return False, "видео недоступно"

    async def never(*_args, **_kwargs):
        raise AssertionError("a failed download has no file to look at")

    monkeypatch.setattr(ytdlp, "_download_owned", failed)
    monkeypatch.setattr(ytdlp, "video_gaps", never)
    assert await ytdlp.download("https://cdn.test/v.m3u8", str(tmp_path / "e.mp4"), lambda _data: None,
                                whole_video=True) == (False, "видео недоступно")
