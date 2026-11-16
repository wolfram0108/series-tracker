"""Duration of a media file or stream through ffprobe, with the bounded life of core.proc.

The duration a file states is a word of its header: a file cut short keeps a whole header (an MP4 with its index in
front, a Matroska written to the end once), so what a file holds is told by the packets it has (`readable_length`)."""
from __future__ import annotations

import asyncio
import json
import os
import weakref

from .proc import LOST, program, run_capture

DEFAULT_TIMEOUT_S = 30.0
# ffprobe is work of a processor and some tens of megabytes each: at most one a core runs at once in the process, however
# many readers ask, so that a burst of measures neither starves the service nor runs the machine out of memory.
PROBES_AT_ONCE = os.cpu_count() or 2
_GATES: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _probes() -> asyncio.Semaphore:
    """The gate of the ffprobe runs of the running loop (a semaphore belongs to the loop it waits in)."""
    loop = asyncio.get_running_loop()
    gate = _GATES.get(loop)
    if gate is None:
        gate = _GATES[loop] = asyncio.Semaphore(PROBES_AT_ONCE)
    return gate
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


async def probe(target: str, *, input_args: list[str] | None = None, timeout: float = DEFAULT_TIMEOUT_S,
                data: bytes | None = None) -> tuple[list[dict] | None, str]:
    """The streams of a file or a stream, with their kinds, heights and dispositions, and what ffprobe said when it could
    not read them (the last line of its errors: a refused connection, a status of the host, data that are no media).
    `input_args` go before the target (the headers of a stream); `data` is what target `pipe:0` reads (the head of a
    stream fetched by the caller)."""
    executable = program("ffprobe")
    if not executable:
        return None, "ffprobe is not installed"
    try:
        async with _probes():
            code, out, err = await run_capture([executable, "-v", "error", *(input_args or []), "-show_entries",
                                                STREAM_ENTRIES, "-of", "json", target], timeout=timeout, data=data)
    except TimeoutError:
        return None, f"ffprobe gave no answer in {timeout:.0f} s"
    except OSError as exc:
        return None, f"ffprobe did not run: {exc}"
    said = (err.decode(errors="replace").strip().splitlines() or [""])[-1]
    # A lost status says nothing: the whole answer read to its end is judged (an empty one is no answer).
    if code == LOST and not out.strip() or code not in (0, LOST):
        return None, said or f"ffprobe ended with {code}"
    try:
        return json.loads(out.decode() or "{}").get("streams") or [], said
    except ValueError:
        return None, said or "ffprobe gave no readable answer"


async def streams_of(target: str, *, input_args: list[str] | None = None,
                     timeout: float = DEFAULT_TIMEOUT_S, data: bytes | None = None) -> list[dict] | None:
    """The streams of a file or a stream (`probe`); None when they cannot be read."""
    return (await probe(target, input_args=input_args, timeout=timeout, data=data))[0]


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
        async with _probes():
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
        async with _probes():
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
        async with _probes():
            code, out, _ = await run_capture([executable, "-v", "error", "-read_intervals", f"{from_s}%",
                                              "-show_entries", "packet=stream_index,pts_time,duration_time", "-of",
                                              "csv=p=0", path], timeout=timeout)
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


LAYOUT_ENTRIES = ("stream=index,codec_type,codec_name,channels,height:stream_tags=language,title,filename,mimetype"
                  ":stream_disposition=default,forced,attached_pic")


async def layout_of(path: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> tuple[dict | None, str]:
    """What a file holds, for the editor of a torrent source: its streams (index, kind, codec, channels, height, language,
    title, default, forced; an attachment with its file name) and how many chapters it has; None and what ffprobe said
    when it could not read the file."""
    executable = program("ffprobe")
    if not executable:
        return None, "ffprobe is not installed"
    try:
        async with _probes():
            code, out, err = await run_capture([executable, "-v", "error", "-show_entries", LAYOUT_ENTRIES,
                                                "-show_chapters", "-of", "json", path], timeout=timeout)
    except TimeoutError:
        return None, f"ffprobe gave no answer in {timeout:.0f} s"
    except OSError as exc:
        return None, f"ffprobe did not run: {exc}"
    said = (err.decode(errors="replace").strip().splitlines() or [""])[-1]
    if code not in (0, LOST) or not out.strip():
        return None, said or f"ffprobe ended with {code}"
    try:
        answer = json.loads(out.decode())
    except ValueError:
        return None, said or "ffprobe gave no readable answer"
    streams = []
    for each in answer.get("streams") or []:
        tags, disposition = each.get("tags") or {}, each.get("disposition") or {}
        streams.append({"index": each.get("index"), "kind": each.get("codec_type") or "", "codec": each.get("codec_name") or "",
                        "channels": each.get("channels"), "height": each.get("height"),
                        "language": tags.get("language") or "", "title": tags.get("title") or "",
                        "filename": tags.get("filename") or "", "mimetype": tags.get("mimetype") or "",
                        "default": bool(disposition.get("default")), "forced": bool(disposition.get("forced")),
                        "picture": bool(disposition.get("attached_pic"))})
    return {"streams": streams, "chapters": len(answer.get("chapters") or [])}, said
