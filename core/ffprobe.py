"""Duration of a media file or stream through ffprobe, with the bounded life of core.proc.

The duration a file states is a word of its header: a file cut short keeps a whole header (an MP4 with its index in
front, a Matroska written to the end once), so what a file holds is told by the packets it has (`readable_length`)."""
from __future__ import annotations

import shutil
import statistics
from dataclasses import dataclass

from .proc import run_capture

DEFAULT_TIMEOUT_S = 30.0
# A pause is a hole only when it is also long against the pace of the video: a video of a frame in two seconds has no hole.
PACE_FACTOR = 8
# Fewer timed frames than this cannot tell the usual interval, and the look is not made. A pause is told from the pace of
# 118 finished files of a library (24 to 30 frames a second): the longest pause of any was 0.2 s and 5 usual intervals at
# most, so HOLE_S (1 s) is five times above the one and PACE_FACTOR (8) is above the other by more than half.
MIN_TIMED_FRAMES = 3
# The tail of a file read for the end of its data: long against the interval between two key frames of a video (a few
# seconds), so that a seek to it lands on data that is there, short against a file, so that the read is a small one.
TAIL_S = 30.0


async def duration_of(target: str, *, input_args: list[str] | None = None,
                      timeout: float = DEFAULT_TIMEOUT_S) -> float:
    """Seconds of a file or stream; 0 when ffprobe is missing or cannot read the target.

    `input_args` go before the target (protocol whitelist, headers of a stream)."""
    executable = shutil.which("ffprobe")
    if not executable:
        return 0.0
    cmd = [executable, "-v", "error", *(input_args or []), "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", target]
    try:
        code, out, _ = await run_capture(cmd, timeout=timeout)
        return float(out.decode().strip()) if code == 0 and out else 0.0
    except (OSError, ValueError):
        return 0.0


async def readable_length(path: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> float:
    """Seconds of a file as far as its data goes: the end of the last packet of the streams that reach its tail, from the
    start of the file; 0 when ffprobe is missing, the file does not read, or no packet is there where its header says
    the end is. A stream that ends early (a subtitle) does not reach the tail and is not counted."""
    executable = shutil.which("ffprobe")
    if not executable:
        return 0.0
    try:
        code, out, _ = await run_capture([executable, "-v", "error", "-show_entries", "format=duration,start_time",
                                          "-of", "default=noprint_wrappers=1", path], timeout=timeout)
        stated = dict(line.split("=", 1) for line in out.decode().splitlines() if "=" in line) if code == 0 else {}
        declared, start = _number(stated.get("duration")), _number(stated.get("start_time")) or 0.0
        if not declared:
            return 0.0
        tail = f"{max(0.0, start + declared - TAIL_S)}%"
        code, out, _ = await run_capture([executable, "-v", "error", "-read_intervals", tail, "-show_entries",
                                          "packet=stream_index,pts_time,duration_time", "-of", "csv=p=0", path],
                                         timeout=timeout)
    except (OSError, TimeoutError):
        return 0.0
    if code != 0:
        return 0.0
    ends: dict[str, float] = {}
    for line in out.decode().splitlines():
        fields = line.strip().split(",")
        at = _number(fields[1]) if len(fields) > 1 else None
        if at is not None:
            lasts = (_number(fields[2]) if len(fields) > 2 else None) or 0.0
            ends[fields[0]] = max(ends.get(fields[0], 0.0), at + lasts)
    return max(0.0, min(ends.values()) - start) if ends else 0.0


def _number(word: str | None) -> float | None:
    try:
        return float(word) if word not in (None, "", "N/A") else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Gaps:
    """What the look at the video of a file found. `checked` is False when the look could not be made (ffprobe is missing,
    failed or did not answer, the file does not read or has no video): that says nothing about the file, and is told so."""

    checked: bool
    holes: tuple[tuple[float, float], ...] = ()  # (second where it begins, seconds it lasts)
    reason: str = ""


async def video_gaps(path: str, *, longer_than: float, timeout: float = 120.0) -> Gaps:
    """The pauses between two frames of the first video stream that are longer than `longer_than` seconds and longer than
    `PACE_FACTOR` times the usual interval between its frames: a piece the download lost leaves such a pause, while the
    audio and the length of the file stay whole. A loss at the very end of the video leaves no pause to see: that is the
    check of the readable length the download makes (`readable_length`)."""
    executable = shutil.which("ffprobe")
    if not executable:
        return Gaps(False, reason="ffprobe не найден")
    cmd = [executable, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0", path]
    try:
        code, out, _ = await run_capture(cmd, timeout=timeout)
    except (OSError, TimeoutError) as exc:
        return Gaps(False, reason=f"ffprobe не ответил: {exc}")
    if code != 0:
        return Gaps(False, reason="файл не читается")
    if not out.strip():
        return Gaps(False, reason="в файле нет видео")
    times = frame_times(out.decode())
    if len(times) < MIN_TIMED_FRAMES:
        return Gaps(False, reason="у кадров файла нет меток времени")
    intervals = [after - before for before, after in zip(times, times[1:])]
    limit = max(longer_than, PACE_FACTOR * (statistics.median(intervals) if intervals else 0.0))
    return Gaps(True, tuple((before, after - before) for before, after in zip(times, times[1:]) if after - before > limit))


def frame_times(text: str) -> list[float]:
    """The times of the packets ffprobe printed, one for each line. A packet with side data has an empty field after its
    time ("0.080000,"), and a packet without a time says N/A: the first is a time, the second is not."""
    times = (line.split(",", 1)[0].strip() for line in text.splitlines())
    return sorted(float(each) for each in times if _is_number(each))


def _is_number(word: str) -> bool:
    try:
        float(word)
    except ValueError:
        return False
    return True
