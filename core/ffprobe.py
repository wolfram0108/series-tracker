"""Duration of a media file or stream through ffprobe, with the bounded life of core.proc.

The duration a file states is a word of its header: a file cut short keeps a whole header (an MP4 with its index in
front, a Matroska written to the end once), so what a file holds is told by the packets it has (`readable_length`)."""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass

from .proc import LOST, program, run_capture

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
# How far short of its end a stream may stop and still be whole: the last packet of a stream may end a little before the
# end its header states (a frame, an audio packet), never by a second.
END_SLACK_S = 1.0
# The streams a file of a series is checked by; subtitles and attachments have no end to reach.
MEDIA_KINDS = frozenset({"video", "audio"})
# A picture a file carries as a video stream (a cover, a thumbnail track, a still image) is no picture of the film: it
# has one frame, or a few, and no end to reach. This rule is the only one: the stream specifiers of ffmpeg ("V") leave out
# a different set of pictures, so the film is chosen here and named to ffmpeg and ffprobe by its index.
STILL_PICTURE = ("attached_pic", "timed_thumbnails", "still_image")
STREAM_ENTRIES = f"stream=index,codec_type,height:stream_disposition={','.join(STILL_PICTURE)}"


def is_still_picture(stream: dict) -> bool:
    """Whether a stream ffprobe describes (with its disposition) is a picture carried along, not the video of the file."""
    disposition = stream.get("disposition") or {}
    return any(disposition.get(flag) for flag in STILL_PICTURE)


def film_stream(streams: list[dict]) -> dict | None:
    """The video of the film among the streams ffprobe describes: the first video stream that is no picture carried along."""
    return next((each for each in streams if each.get("codec_type") == "video" and not is_still_picture(each)), None)


async def streams_of(target: str, *, input_args: list[str] | None = None,
                     timeout: float = DEFAULT_TIMEOUT_S, data: bytes | None = None) -> list[dict] | None:
    """The streams of a file or a stream, with their kinds, heights and dispositions; None when they cannot be read.
    `input_args` go before the target (the headers of a stream); `data` is what target `pipe:0` reads (the head of a
    stream fetched by the caller)."""
    executable = program("ffprobe")
    if not executable:
        return None
    try:
        code, out, _ = await run_capture([executable, "-v", "error", *(input_args or []), "-show_entries", STREAM_ENTRIES,
                                          "-of", "json", target], timeout=timeout, data=data)
        # A lost status says nothing: the whole answer read to its end is judged (an empty one is no answer).
        if code == LOST and not out.strip():
            return None
        return json.loads(out.decode() or "{}").get("streams") or [] if code in (0, LOST) else None
    except (OSError, TimeoutError, ValueError):
        return None


async def duration_of(target: str, *, input_args: list[str] | None = None,
                      timeout: float = DEFAULT_TIMEOUT_S) -> float:
    """Seconds of a file or stream; 0 when ffprobe is missing or cannot read the target.

    `input_args` go before the target (protocol whitelist, headers of a stream)."""
    executable = program("ffprobe")
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
    """Seconds of a file as far as its data goes, from the start of the file; 0 when ffprobe is missing or the file does
    not read. Each stream of video and audio is held to its own end, as the header states it: a stream that is shorter
    than the others by design (an audio track that ends before the picture) is whole when its data reach its own end, and
    a stream whose data stop short of it is where the file is cut. A file whose streams all reach their ends is as long as
    its header says; one with a stream cut short is as long as the earliest cut. A file whose streams state no end of
    their own is held to the end of the file by its longest stream."""
    executable = program("ffprobe")
    if not executable:
        return 0.0
    try:
        entries = ("format=format_name,duration,start_time:stream=index,codec_type,start_time,duration"
                   f":stream_disposition={','.join(STILL_PICTURE)}:stream_tags=DURATION")
        code, out, _ = await run_capture([executable, "-v", "error", "-show_entries", entries, "-of", "json", path],
                                         timeout=timeout)
        stated = json.loads(out.decode() or "{}") if code == 0 else {}
        declared = _number((stated.get("format") or {}).get("duration"))
        start = _number((stated.get("format") or {}).get("start_time")) or 0.0
        if not declared:
            return 0.0
        matroska = "matroska" in str((stated.get("format") or {}).get("format_name") or "")
        ends = {str(each.get("index")): _stream_end(each, matroska=matroska) for each in stated.get("streams") or []
                if each.get("codec_type") in MEDIA_KINDS and not is_still_picture(each)}
        known = [end for end in ends.values() if end is not None]
        from_s = max(0.0, min(known or [start + declared]) - TAIL_S)
        code, out, _ = await run_capture([executable, "-v", "error", "-read_intervals", f"{from_s}%", "-show_entries",
                                          "packet=stream_index,pts_time,duration_time", "-of", "csv=p=0", path],
                                         timeout=timeout)
    except (OSError, TimeoutError, ValueError):
        return 0.0
    if code != 0:
        return 0.0
    reached: dict[str, float] = {}
    for line in out.decode().splitlines():
        fields = line.strip().split(",")
        at = _number(fields[1]) if len(fields) > 1 else None
        if at is not None and fields[0] in ends:
            lasts = (_number(fields[2]) if len(fields) > 2 else None) or 0.0
            reached[fields[0]] = max(reached.get(fields[0], 0.0), at + lasts)
    if not known:
        whole = bool(reached) and max(reached.values()) >= start + declared - END_SLACK_S
        return declared if whole else max(0.0, max(reached.values(), default=0.0) - start)
    cuts = [reached.get(index, 0.0) for index, end in ends.items()
            if end is not None and reached.get(index, 0.0) < end - END_SLACK_S]
    return max(0.0, min(cuts) - start) if cuts else declared


def _stream_end(stream: dict, *, matroska: bool) -> float | None:
    """Where a stream ends by its header, each field read for what it means in its format. Matroska states the end of a
    stream in its tag DURATION, as a time from the zero of the file (an audio track that starts at 2 s and lasts 45 ends
    at 47): nothing is added to it, and the `duration` ffprobe gives such a stream is the length of the file, not of the
    stream. Elsewhere (MP4, MPEG-TS) the stream has a start and a length of its own: it ends at their sum."""
    if matroska:
        return _clock((stream.get("tags") or {}).get("DURATION"))
    length = _number(stream.get("duration"))
    return None if length is None else (_number(stream.get("start_time")) or 0.0) + length


def _clock(text: str | None) -> float | None:
    """'00:00:45.023000000' -> 45.023."""
    try:
        hours, minutes, seconds = str(text).split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (ValueError, AttributeError):
        return None


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
    executable = program("ffprobe")
    if not executable:
        return Gaps(False, reason="ffprobe не найден")
    streams = await streams_of(path, timeout=timeout)
    if streams is None:
        return Gaps(False, reason="файл не читается")
    film = film_stream(streams)
    if film is None:
        return Gaps(False, reason="в файле нет видео")
    cmd = [executable, "-v", "error", "-select_streams", str(film["index"]), "-show_entries", "packet=pts_time", "-of",
           "csv=p=0", path]
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
