"""A download that says it is done is a finished one only when the video has no hole: ffmpeg skips a segment it cannot open
and still exits 0, and the audio and the length of the file stay whole, so only the pauses between the frames tell.
A look that could not be made is not a file without holes."""
import asyncio
import os
import shutil
import subprocess

import pytest

from core import ffprobe, storage
from core.ffprobe import video_gaps
from modules.downloads import ytdlp
from modules.downloads.module import _transient_download_error

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg is not installed")


def make(path, *args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(path)], check=True)


async def freed(path: str) -> None:
    """A download holds its name until the programs it started have ended; the next one on the name waits for that."""
    free = asyncio.get_running_loop().create_future()
    storage.when_free(path, lambda: free.done() or free.set_result(None))
    await asyncio.wait_for(free, 10)


@pytest.fixture(autouse=True)
def announced(monkeypatch):
    """The sources of these tests announce the length of the clips, ten seconds."""
    async def ten(*_args, **_kwargs):
        return 10.0
    monkeypatch.setattr(ytdlp, "_announced_length", ten)


@pytest.fixture
def clips(tmp_path):
    whole, holed, sound, slow = tmp_path / "whole.mp4", tmp_path / "holed.mp4", tmp_path / "sound.m4a", tmp_path / "slow.mp4"
    make(whole, "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=10", "-c:v", "libx264", "-pix_fmt", "yuv420p")
    # The frames of seconds 3-6 are taken out and the rest keep their time: a hole in the video, as a skipped segment leaves.
    make(holed, "-i", str(whole), "-vf", "select='not(between(t,3,6))'", "-fps_mode", "passthrough", "-c:v", "libx264",
         "-pix_fmt", "yuv420p")
    make(sound, "-f", "lavfi", "-i", "sine=duration=3", "-c:a", "aac")
    # A video of one frame in two seconds: every pause is longer than a second, and none is a loss.
    make(slow, "-f", "lavfi", "-i", "testsrc=size=160x120:rate=1/2:duration=10", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-fps_mode", "passthrough")
    return whole, holed, sound, slow


@pytest.mark.asyncio
async def test_a_hole_in_the_video_is_found_with_its_place_and_length_and_a_whole_file_has_none(clips):
    whole, holed, _, _ = clips
    seen = await video_gaps(str(whole), longer_than=1.0)
    assert seen.checked and seen.holes == ()
    seen = await video_gaps(str(holed), longer_than=1.0)
    [(at, length)] = seen.holes
    assert seen.checked and round(at, 2) == 2.96 and round(length, 2) == 3.08


@pytest.mark.asyncio
async def test_a_pause_is_a_hole_only_when_it_is_long_against_the_pace_of_the_video_too(clips):
    _, _, _, slow = clips
    seen = await video_gaps(str(slow), longer_than=1.0)
    assert seen.checked and seen.holes == ()  # a frame in two seconds is the pace of this video, not a loss


@pytest.mark.asyncio
async def test_a_file_with_no_video_is_not_a_file_without_holes(clips):
    _, _, sound, _ = clips
    seen = await video_gaps(str(sound), longer_than=1.0)
    assert not seen.checked and "видео" in seen.reason


@pytest.mark.asyncio
async def test_a_look_that_could_not_be_made_is_told_as_such_and_not_as_a_file_without_holes(tmp_path, monkeypatch):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    for path in (broken, tmp_path / "missing.mp4"):
        seen = await video_gaps(str(path), longer_than=1.0)
        assert not seen.checked and seen.holes == () and seen.reason
    monkeypatch.setattr(ffprobe.shutil, "which", lambda _name: None)
    seen = await video_gaps(str(broken), longer_than=1.0)
    assert not seen.checked and "ffprobe" in seen.reason


@pytest.mark.asyncio
async def test_a_look_that_ran_out_of_time_is_not_a_file_without_holes(clips, monkeypatch):
    whole, _, _, _ = clips

    async def hangs(*_args, **_kwargs):
        raise TimeoutError("ffprobe did not answer")

    monkeypatch.setattr(ffprobe, "run_capture", hangs)
    seen = await video_gaps(str(whole), longer_than=1.0)
    assert not seen.checked and seen.reason


@pytest.mark.asyncio
async def test_a_download_with_a_hole_is_discarded_and_is_a_failure_the_queue_tries_again(clips, tmp_path, monkeypatch):
    whole, holed, _, _ = clips
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
    await freed(target)
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


@pytest.mark.asyncio
async def test_a_file_that_is_already_there_is_looked_at_too_and_a_look_that_fails_keeps_it_for_the_next_try(clips, tmp_path,
                                                                                                          monkeypatch):
    """The work file that a stop left is taken as the download; it is not a whole video until it has been looked at."""
    whole, holed, _, _ = clips
    target = str(tmp_path / "out" / "episode.mp4")
    os.makedirs(os.path.dirname(target))
    shutil.copy(holed, target)
    ok, error = await ytdlp.download("https://cdn.test/v.m3u8", target, lambda _data: None, whole_video=True)
    assert not ok and "обрыв" in error and not os.path.exists(target)  # a file with a hole is not kept

    shutil.copy(whole, target)

    async def cannot(_path, **_kwargs):
        return ffprobe.Gaps(False, (), "ffprobe не ответил")

    monkeypatch.setattr(ytdlp, "video_gaps", cannot)
    ok, error = await ytdlp.download("https://cdn.test/v.m3u8", target, lambda _data: None, whole_video=True)
    assert not ok and "обрыв" in error and "не выполнена" in error and _transient_download_error(error)
    assert os.path.exists(target)  # nothing was proved against it: the next try looks again


@pytest.mark.parametrize("text,expected", [
    ("0.00\n0.04\n0.08\n", [0.0, 0.04, 0.08]),
    ("0.080000,\n0.000000,\n0.040000,\n", [0.0, 0.04, 0.08]),  # a packet with side data adds an empty field
    ("N/A\n0.04\nN/A,\n", [0.04]),
    ("0.04,0.00,\n", [0.04]),
    ("", []),
    ("\n\n", []),
])
def test_the_times_of_the_frames_are_read_from_each_form_ffprobe_prints(text, expected):
    assert ffprobe.frame_times(text) == expected


@pytest.mark.asyncio
async def test_a_file_whose_packets_carry_side_data_is_looked_at_and_a_file_without_times_is_not_taken_for_whole(monkeypatch, tmp_path):
    target = tmp_path / "x.mp4"
    target.write_bytes(b"x")

    def answer(text):
        async def run(cmd, timeout):
            return 0, text.encode(), b""
        return run

    monkeypatch.setattr(ffprobe, "run_capture", answer("".join(f"{index * 0.04:.6f},\n" for index in range(50))))
    assert (await video_gaps(str(target), longer_than=1.0)).checked
    monkeypatch.setattr(ffprobe, "run_capture", answer("".join(f"{index * 0.04:.6f},\n" for index in range(25))
                                                       + "".join(f"{2 + index * 0.04:.6f},\n" for index in range(25))))
    seen = await video_gaps(str(target), longer_than=1.0)
    assert seen.checked and len(seen.holes) == 1 and round(seen.holes[0][1], 2) == 1.04
    monkeypatch.setattr(ffprobe, "run_capture", answer("N/A\nN/A,\n0.04\n"))
    seen = await video_gaps(str(target), longer_than=1.0)
    assert not seen.checked and "меток времени" in seen.reason


@pytest.mark.asyncio
async def test_a_download_shorter_than_its_source_announced_is_discarded_whichever_way_it_was_read(clips, tmp_path,
                                                                                                    monkeypatch):
    """A program that stopped early (a killed run continued, a playlist cut short) still says it is done."""
    whole, _, _, _ = clips
    target = str(tmp_path / "out" / "episode.mp4")

    async def writes(*_args, **_kwargs):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy(whole, target)
        return True, ""

    async def thirty(*_args, **_kwargs):
        return 30.0

    monkeypatch.setattr(ytdlp, "_download_owned", writes)
    monkeypatch.setattr(ytdlp, "_announced_length", thirty)
    for whole_video in (True, False):  # a voice-over is not looked at for holes, but it is as long as its source too
        await freed(target)
        ok, error = await ytdlp.download("https://vk.com/video-1_2", target, lambda _data: None, whole_video=whole_video)
        assert not ok and "скачано 10 с из ожидаемых ~30 с" in error and _transient_download_error(error)
        assert not os.path.exists(target)


@pytest.mark.asyncio
async def test_a_work_file_a_stop_left_is_taken_only_at_the_announced_length(clips, tmp_path, monkeypatch):
    whole, _, _, _ = clips
    target = str(tmp_path / "out" / "episode.mp4")
    os.makedirs(os.path.dirname(target))
    shutil.copy(whole, target)

    async def thirty(*_args, **_kwargs):
        return 30.0

    monkeypatch.setattr(ytdlp, "_announced_length", thirty)
    ok, error = await ytdlp.download("https://vk.com/video-1_2", target, lambda _data: None, whole_video=True)
    assert not ok and "неполная" in error and not os.path.exists(target)


@pytest.mark.asyncio
async def test_a_source_that_states_no_length_leaves_the_download_unfinished_and_keeps_the_file(clips, tmp_path,
                                                                                               monkeypatch):
    whole, _, _, _ = clips
    target = str(tmp_path / "out" / "episode.mp4")
    os.makedirs(os.path.dirname(target))
    shutil.copy(whole, target)

    async def unknown(*_args, **_kwargs):
        return 0.0

    monkeypatch.setattr(ytdlp, "_announced_length", unknown)
    ok, error = await ytdlp.download("https://vk.com/video-1_2", target, lambda _data: None)
    assert not ok and "не сообщил длину" in error and _transient_download_error(error)
    assert os.path.exists(target)  # nothing was proved against it: the next try looks again


# --- what a file holds, not what its header says -------------------------------------------------------------------------------

def cut_in_half(path) -> None:
    with open(path, "r+b") as out:
        out.truncate(os.path.getsize(path) // 2)


@pytest.fixture
def halves(tmp_path):
    """A clip with its index in front (MP4, faststart) and one in Matroska, each whole and with its second half lost: the
    header of both keeps the whole length."""
    made = {}
    for name, extra in (("clip.mp4", ["-movflags", "+faststart"]), ("clip.mkv", [])):
        whole, cut = tmp_path / f"whole-{name}", tmp_path / f"cut-{name}"
        make(whole, "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=40", "-f", "lavfi", "-i",
             "sine=frequency=300:duration=40", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", *extra)
        shutil.copyfile(whole, cut)
        cut_in_half(cut)
        made[name] = (whole, cut)
    return made


@pytest.mark.asyncio
async def test_the_readable_length_is_where_the_data_ends_and_not_the_length_the_header_states(halves):
    for name, (whole, cut) in halves.items():
        assert await ffprobe.duration_of(str(cut)) == pytest.approx(40, abs=0.1), name  # the header lies
        assert await ffprobe.readable_length(str(whole)) == pytest.approx(40, abs=0.1), name
        assert await ffprobe.readable_length(str(cut)) < 30, name
    assert await ffprobe.readable_length("/no/such/file.mp4") == 0.0


@pytest.mark.asyncio
async def test_a_download_whose_file_lost_its_second_half_is_not_taken_for_whole(halves, monkeypatch):
    """The source announces the whole length, the file on disk has it in its header and half of the data."""
    whole, cut = halves["clip.mp4"]

    async def forty(*_args, **_kwargs):
        return 40.0

    async def progress(_data):
        pass

    monkeypatch.setattr(ytdlp, "_announced_length", forty)
    ok, error = await ytdlp.download("https://cdn.test/episode.mp4", str(cut), progress, whole_video=True)

    assert not ok and "неполная загрузка" in error and not cut.exists()


@pytest.fixture
def uneven(tmp_path):
    """Whole files whose audio and picture end apart by design (an audio track shorter, or longer, than the picture), in
    MP4 and in Matroska, and each of them with its second half lost."""
    made = {}
    for name, (video_s, audio_s) in (("short_audio", (60, 45)), ("short_video", (45, 60))):
        for suffix, extra in (("mp4", ["-movflags", "+faststart"]), ("mkv", [])):
            whole, cut = tmp_path / f"{name}.{suffix}", tmp_path / f"cut_{name}.{suffix}"
            make(whole, "-f", "lavfi", "-i", f"testsrc=size=160x90:rate=10:duration={video_s}", "-f", "lavfi", "-i",
                 f"sine=frequency=300:duration={audio_s}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", *extra)
            shutil.copyfile(whole, cut)
            cut_in_half(cut)
            made[f"{name}.{suffix}"] = (whole, cut)
    return made


@pytest.mark.asyncio
async def test_a_stream_shorter_by_design_is_whole_and_a_stream_cut_short_is_where_the_file_ends(uneven):
    """Each stream is held to its own end: the shorter audio of a whole file does not make it short, and the longer audio
    of a file does not hide a picture that stops short of its end."""
    for name, (whole, cut) in uneven.items():
        assert await ffprobe.readable_length(str(whole)) == pytest.approx(60, abs=0.2), name
        assert await ffprobe.readable_length(str(cut)) < 35, name


@pytest.mark.asyncio
async def test_a_whole_download_with_an_audio_track_shorter_than_its_picture_is_taken(uneven, monkeypatch):
    whole, _ = uneven["short_audio.mp4"]

    async def sixty(*_args, **_kwargs):
        return 60.0

    async def progress(_data):
        pass

    monkeypatch.setattr(ytdlp, "_announced_length", sixty)
    assert await ytdlp.download("https://cdn.test/episode.mp4", str(whole), progress, whole_video=True) == (True,
                                                                                                         "файл уже существует")
    assert whole.exists()


@pytest.fixture
def delayed(tmp_path):
    """Whole files whose audio starts after the picture (by 2 and by 10 seconds) and ends before it, in MP4 and Matroska;
    and the same files with their last quarter lost (the MP4 with its index in front, so that it still reads)."""
    made = {}
    for delay in (2, 10):
        for suffix, extra in (("mp4", ["-movflags", "+faststart"]), ("mkv", [])):
            whole, cut = tmp_path / f"delayed{delay}.{suffix}", tmp_path / f"cut_delayed{delay}.{suffix}"
            make(whole, "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=60", "-itsoffset", str(delay), "-f",
                 "lavfi", "-i", "sine=frequency=300:duration=45", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                 *extra)
            shutil.copyfile(whole, cut)
            with open(cut, "r+b") as out:
                out.truncate(os.path.getsize(cut) * 3 // 4)
            made[f"{delay}.{suffix}"] = (whole, cut)
    return made


@pytest.mark.asyncio
async def test_a_stream_that_starts_late_is_held_to_its_own_end_as_its_format_states_it(delayed, monkeypatch):
    """Matroska states where a stream ends, from the zero of the file; MP4 states where it starts and how long it lasts.
    Read each for what it means, a whole file with late, short audio is whole and a cut one is cut."""
    async def sixty(*_args, **_kwargs):
        return 60.0

    async def progress(_data):
        pass

    monkeypatch.setattr(ytdlp, "_announced_length", sixty)
    for name, (whole, cut) in delayed.items():
        assert await ffprobe.readable_length(str(whole)) == pytest.approx(60, abs=0.2), name
        assert await ffprobe.readable_length(str(cut)) < 50, name
        assert (await ytdlp.download("https://cdn.test/e." + name, str(whole), progress, whole_video=True))[0], name
        assert whole.exists(), name
