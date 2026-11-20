"""The container of an episode (ЭТАП_11): one MKV with the video of the reference and the voice-overs in order.

Nothing is encoded again: the video and every audio track are copied as they are (`-c copy`), the way track-muxer
builds its containers (`mux/build.py`): the first track is the default one, the others are not; the tracks interleave
evenly, the interleave bounded (`INTERLEAVE_DELTA_US`). The flags of every stream are the build's own, none passes from an
input (the picture: default; the voice-overs: the first; the subtitles: as the plan says). The container says what it holds by its own tags, so that the file is its own
ledger: `ST_VIDEO` (the identity of the video), and for every audio track `ST_VOICE` (the name of the voice-over group),
the title the player shows (the user's name of the track, else the name of the group), `ST_DUB` (the key of the voice-over, which a rename of its group does not change: the track is known by it),
`ST_ALIGNED_TO` (the identity of the video the track was aligned to) and `ST_OWN` (1 for the video's own sound), the
language of its voice-over as the user gave it to the group (none given: Matroska's own `und`, nothing is claimed), and
`ST_PUBLICATION`, the number of the publication that built it: two containers of the same video and tracks (another
resolution) are told apart by it, so a publication cut short knows whether its file is the one under the name. Whether a
build is done is told by reading the result back (`contents`), never by the code ffmpeg returned.

Subtitles (stage 15, DESIGN.md 7.1.1) are the tracks the plan chose, in its order, each with `ST_SUB` (the key of the row
it came from), its name as title, its language and the flag «forced»; the fonts of the files they come from go with them.
Each of them comes into the build as an input of its own (`_taken_out`): ffmpeg takes its next packet from the input of the
stream that is furthest behind, and the signs of an episode (one event in all of it) stay the furthest behind for good, so the
file they share with the picture would be read to its end before any voice-over of the other files; the container would hold
all of its picture and then all of its sound from the start again, and a player shows no picture or hangs.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, replace
from typing import Awaitable, Callable, Sequence

from .ffprobe import film_stream, layout_of, streams_of
from .proc import LOST, program, run_capture, run_lines

# What a build tells as it goes (`build(report=…)`): the stage and its figures — "build" {progress, speed_x, eta,
# written_mb} (ffmpeg -progress), "verify" {} (the result read back).
Report = Callable[[str, dict], Awaitable[None]]


async def _silent(_stage: str, _figures: dict) -> None:
    return None

# What Matroska holds for a track whose language nobody stated (ffmpeg writes it so): no language is claimed.
UNSTATED = "und"


def identity(source_id: int | None, row_key: str | None) -> str:
    """The identity of a video: the same source and the same row of its table, whatever the resolution (ЭТАП_11, point 5).
    Empty when either is unknown: such a video is the same as no other."""
    return f"{source_id}:{row_key}" if source_id is not None and row_key else ""
# A build copies streams and a read looks at the head of the file; both end in seconds even for a 2160p episode, so these
# are safeguards: a build or a read past them is a failure, not "done".
BUILD_TIMEOUT_S = 600.0
# How far apart the packets of the streams may lie in the output (ffmpeg's own default, 10 s). Never unbounded: a stream with a few
# packets (the signs of an episode are one event in all of it) would hold the muxer, and everything else would pile up in memory.
INTERLEAVE_DELTA_US = 10_000_000
READ_TIMEOUT_S = 60.0
# The video of a container is the video it was built from: the durations differ only by the rounding of the streams.
DURATION_SLACK_S = 1.0


@dataclass(frozen=True)
class AudioIn:
    """A track to put into a container: the `stream`-th audio stream of `path`, the voice-over `label`, and the identity of
    the video it is synchronous with."""

    label: str
    path: str
    stream: int
    aligned_to: str
    # The video's own sound (not a voice-over aligned to it): what an alignment of a new voice-over listens to.
    own: bool = False
    # The key of the voice-over (voices' voice_key): what the track is known by; the label is only its name.
    key: str = ""
    # The title of the track in the file: the user's name of the track; none, the label.
    title: str = ""
    # The language of the voice-over, as the user gave it to its group; none, nothing is written.
    language: str = ""

    @property
    def shown(self) -> str:
        return self.title or self.label

    @property
    def stated(self) -> str:
        return self.language or UNSTATED


@dataclass(frozen=True)
class SubtitleIn:
    """A subtitles track to put into a container: the `stream`-th subtitles stream of `path`, its key (`ST_SUB`), its name,
    its language and how it lies in the container (the default one, forced), as the output says."""

    path: str
    stream: int
    key: str
    title: str = ""
    language: str = ""
    forced: bool = False
    default: bool = False
    # The font files of the release the track is drawn with, beside it (not attachments of its own file).
    fonts: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubtitleOut:
    """A subtitles track a container holds, as its tags say; `index` is its place among the subtitles of the file."""

    index: int
    key: str
    title: str = ""
    language: str = ""
    forced: bool = False
    default: bool = False
    # The statistics tag of the file says the track ends where it begins: it holds no events (to be told by counting them).
    at_start: bool = False


@dataclass(frozen=True)
class AudioOut:
    """An audio track a container holds, as its tags say."""

    index: int
    label: str
    aligned_to: str
    default: bool
    language: str = ""
    own: bool = False
    key: str = ""
    title: str = ""


@dataclass(frozen=True)
class Contents:
    video: str
    duration: float
    has_video: bool
    audio: list[AudioOut] = field(default_factory=list)
    publication: str = ""
    # The height of the film as the file holds it (0 when unknown): what is named after the video is named by this.
    height: int = 0
    subtitles: list[SubtitleOut] = field(default_factory=list)

    @property
    def labels(self) -> list[str]:
        return [track.label for track in self.audio]

    @property
    def signature(self) -> list[tuple[str, str, str, bool, str]]:
        """What the tracks are, in order: name, title, key, own sound, language."""
        return [(track.label, track.title, track.key, track.own, track.language) for track in self.audio]

    @property
    def own_track(self) -> int:
        """The index among the audio streams of the video's own sound; the first one when no tag says (a plain file)."""
        return next((track.index for track in self.audio if track.own), 0)


PROVENANCE = "provenance.json"  # the attachment that says where what the container holds came from (stage 12)


async def build(output: str, video: str, video_identity: str, tracks: Sequence[AudioIn], *, publication: str = "",
                timeout: float = BUILD_TIMEOUT_S, provenance: dict | None = None, report: Report = _silent,
                subtitles: Sequence[SubtitleIn] = ()) -> str | None:
    """Write the container at `output` (an .mkv the caller puts in place); None when it was built and reads back as asked,
    else the reason. The video is the film of `video` (core.ffprobe.film_stream); a track of the same file is taken
    from it."""
    with tempfile.TemporaryDirectory(prefix="st-subtitles-") as scratch:
        return await _build(output, video, video_identity, tracks, publication=publication, timeout=timeout,
                            provenance=provenance, report=report, subtitles=subtitles, scratch=scratch)


async def _build(output: str, video: str, video_identity: str, tracks: Sequence[AudioIn], *, publication: str,
                 timeout: float, provenance: dict | None, report: Report, subtitles: Sequence[SubtitleIn],
                 scratch: str) -> str | None:
    """`build`, with `scratch`, the directory the subtitles taken out of their files wait in, which the caller removes."""
    executable = program("ffmpeg")
    if executable is None:
        return "ffmpeg_missing"
    film = film_stream(await streams_of(video, timeout=READ_TIMEOUT_S) or [])
    if film is None:
        return "no_video"  # the file has no film to build of (no video stream, or only a picture it carries)
    expected = await contents(video)
    taken = await _taken_out(executable, subtitles, scratch, timeout)
    if isinstance(taken, str):
        return taken
    # The fonts the chosen subtitles are drawn with: the attachments of the Matroska files they come from (DESIGN.md 7.1);
    # the chapters are the video's. The table of a container built here is no font: it is written anew below.
    fonts = await _fonts([each.path for each in subtitles])
    sources = [video] + [path for path in dict.fromkeys([*(track.path for track in tracks), *(each.path for each in taken),
                                                          *(path for path, _stream in fonts)]) if path != video]
    index_of = {path: number for number, path in enumerate(sources)}
    cmd = [executable, "-y", "-hide_banner", "-loglevel", "error"]
    for path in sources:
        cmd += ["-i", path]
    cmd += ["-map", f"0:{film['index']}"]
    for track in tracks:
        cmd += ["-map", f"{index_of[track.path]}:a:{track.stream}"]
    for each in taken:
        cmd += ["-map", f"{index_of[each.path]}:s:{each.stream}"]
    for path, stream in fonts:
        cmd += ["-map", f"{index_of[path]}:{stream['index']}"]
    # `-c copy` passes the flags of an input on, so the build says them for every kind of stream itself: the picture is the
    # default one and nothing else (below the voice-overs and the subtitles).
    cmd += ["-map_metadata", "-1", "-map_chapters", "0", "-c", "copy", "-max_interleave_delta", str(INTERLEAVE_DELTA_US),
            "-disposition:v:0", "default", "-metadata", f"ST_VIDEO={video_identity}", "-metadata", f"ST_PUBLICATION={publication}"]
    for number, each in enumerate(subtitles):
        cmd += [*([f"-metadata:s:s:{number}", f"language={each.language}"] if each.language else []),
                *([f"-metadata:s:s:{number}", f"title={each.title}"] if each.title else []),
                f"-metadata:s:s:{number}", f"ST_SUB={each.key}",
                f"-disposition:s:{number}", "+".join(flag for flag, on in (("default", each.default), ("forced", each.forced))
                                                     if on) or "0"]
    for number, (_path, stream) in enumerate(fonts):
        cmd += [f"-metadata:s:t:{number}", f"filename={stream['filename']}",
                f"-metadata:s:t:{number}", f"mimetype={stream['mimetype'] or 'application/x-truetype-font'}"]
    # The font files that lie beside the subtitles, each name once and none a file's own attachment has already given.
    names = {stream["filename"] for _path, stream in fonts}
    beside = []
    for path in (font for each in subtitles for font in each.fonts):
        if os.path.basename(path) not in names:
            names.add(os.path.basename(path))
            beside.append(path)
    for offset, path in enumerate(beside):
        number = len(fonts) + offset
        cmd += ["-attach", path, f"-metadata:s:t:{number}", f"filename={os.path.basename(path)}",
                f"-metadata:s:t:{number}", f"mimetype={_FONT_TYPES.get(os.path.splitext(path)[1].lower(), 'application/x-truetype-font')}"]
    for number, track in enumerate(tracks):
        cmd += [f"-metadata:s:a:{number}", f"title={track.shown}",
                *([f"-metadata:s:a:{number}", f"language={track.language}"] if track.language else []),
                f"-metadata:s:a:{number}", f"ST_VOICE={track.label}",
                f"-metadata:s:a:{number}", f"ST_DUB={track.key}",
                f"-metadata:s:a:{number}", f"ST_ALIGNED_TO={track.aligned_to}",
                f"-metadata:s:a:{number}", f"ST_OWN={1 if track.own else 0}",
                f"-disposition:a:{number}", "default" if number == 0 else "0"]
    attached = None
    if provenance is not None:
        # The table of what the container holds travels inside it: the library can be told again from its files alone.
        attached = output + "." + PROVENANCE
        with open(attached, "w", encoding="utf-8") as out:
            json.dump(provenance, out, ensure_ascii=False, sort_keys=True)
        table = f"-metadata:s:t:{len(fonts) + len(beside)}"
        cmd += ["-attach", attached, table, "mimetype=application/json", table, f"filename={PROVENANCE}"]
    cmd += ["-progress", "pipe:1", "-nostats", "-f", "matroska", output]
    length = expected.duration if expected is not None else 0.0
    fields: dict[str, str] = {}

    async def told(line: str) -> None:
        key, _, value = line.partition("=")
        fields[key] = value
        if key == "progress":
            await report("build", progress_of(fields, length))
            fields.clear()

    try:
        code, err = await run_lines(cmd, timeout=timeout, on_line=told)
    except (OSError, TimeoutError) as exc:
        return f"ffmpeg_failed: {type(exc).__name__}"
    finally:
        if attached is not None and os.path.exists(attached):
            os.remove(attached)
    # An end whose status was lost tells nothing: the container that was written is what is judged.
    if code not in (0, LOST):
        return _failure(code, err)
    await report("verify", {})
    made = await contents(output)
    if made is not None and made.publication != publication:
        return "container_publication_wrong"
    problem = problem_of(made, video_identity, signature_of(tracks), expected.duration if expected is not None else None)
    if problem is None and made is not None and [each.key for each in made.subtitles] != [each.key for each in subtitles]:
        return "container_subtitles_wrong"
    return problem


def _failure(code: int, err: bytes) -> str:
    """Why an ffmpeg that ended with `code` failed. A program the system killed (no memory) says nothing: its code is all
    the reason there is."""
    said = err.decode(errors="replace").strip()[-300:]
    return f"ffmpeg_failed: {said or (f'killed by signal {-code}' if code < 0 else f'exit code {code}')}"


async def _taken_out(executable: str, subtitles: Sequence[SubtitleIn], scratch: str, timeout: float) -> list[SubtitleIn] | str:
    """The subtitles of a build, each in a file of its own, so that each is an input of its own (the module's docstring says
    why). A track of a file that holds other streams is taken out of it into `scratch`, every such track of one file in one
    pass over it; a track that is the only stream of its file (the ASS file of a release) is taken as it is. The reason when
    a pass fails."""
    holds: dict[str, bool] = {}
    for each in subtitles:
        if each.path not in holds:
            streams = await streams_of(each.path, timeout=READ_TIMEOUT_S) or []
            holds[each.path] = sum(1 for stream in streams if stream.get("codec_type") != "attachment") > 1
    taken = list(subtitles)
    for pass_number, path in enumerate(path for path, shared in holds.items() if shared):
        cmd = [executable, "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", path]
        files: dict[int, str] = {}
        for number, each in enumerate(subtitles):
            if each.path == path:
                files[number] = os.path.join(scratch, f"{pass_number}-{number}.mks")
                cmd += ["-map", f"0:s:{each.stream}", "-c", "copy", "-f", "matroska", files[number]]
        try:
            code, _out, err = await run_capture(cmd, timeout=timeout)
        except (OSError, TimeoutError) as exc:
            return f"ffmpeg_failed: {type(exc).__name__}"
        if code not in (0, LOST):
            return _failure(code, err)
        for number, name in files.items():
            if not os.path.exists(name):  # an end whose status was lost tells nothing: the file that was written is judged
                return f"ffmpeg_failed: no subtitles came out of {os.path.basename(path)}"
            taken[number] = replace(subtitles[number], path=name, stream=0)
    return taken


_FONT_TYPES = {".ttf": "application/x-truetype-font", ".otf": "application/vnd.ms-opentype", ".ttc": "application/x-font-ttf"}


async def _fonts(paths: Sequence[str]) -> list[tuple[str, dict]]:
    """The font attachments of the Matroska files among `paths` (core.ffprobe.layout_of), each name once: a font two files
    carry is the same font."""
    found, names = [], set()
    for path in dict.fromkeys(paths):
        if os.path.splitext(path)[1].lower() not in (".mkv", ".mka", ".mks"):
            continue
        layout, _said = await layout_of(path, timeout=READ_TIMEOUT_S)
        for each in (layout or {}).get("streams") or []:
            if (each["kind"] == "attachment" and each["filename"] and each["filename"] != PROVENANCE
                    and each["filename"] not in names):
                names.add(each["filename"])
                found.append((path, each))
    return found


def progress_of(fields: dict[str, str], length: float) -> dict:
    """The figures of a block of ffmpeg -progress: the share of the length written, the speed (×N), the time left, MB."""
    try:
        written = int(fields.get("out_time_us") or fields.get("out_time_ms") or 0) / 1_000_000
    except ValueError:
        written = 0.0
    try:
        speed = float((fields.get("speed") or "").rstrip("x").strip() or 0)
    except ValueError:
        speed = 0.0
    try:
        size = int(fields.get("total_size") or 0)
    except ValueError:
        size = 0
    share = min(100, int(written / length * 100)) if length > 0 else None
    return {"progress": share, "speed_x": round(speed, 1) if speed else None,
            "eta": int((length - written) / speed) if speed > 0 and length > written else None,
            "written_mb": round(size / 1024 ** 2, 1) if size else None}


async def provenance_of(path: str, *, timeout: float = READ_TIMEOUT_S) -> dict | None:
    """The provenance a container carries (`build(provenance=...)`); None when it carries none or cannot be read."""
    executable = program("ffmpeg")
    if executable is None:
        return None
    dumped = path + ".provenance.read"
    try:
        await run_capture([executable, "-v", "error", "-y", f"-dump_attachment:m:filename:{PROVENANCE}", dumped,
                           "-i", path], timeout=timeout)
        if not os.path.exists(dumped):
            return None
        with open(dumped, encoding="utf-8") as found:
            return json.load(found)
    except (OSError, TimeoutError, ValueError):
        return None
    finally:
        if os.path.exists(dumped):
            os.remove(dumped)


def signature_of(tracks: Sequence[AudioIn]) -> list[tuple[str, str, str, bool, str]]:
    """What a container built of `tracks` holds, in the terms of `Contents.signature`."""
    return [(track.label, track.shown, track.key, track.own, track.stated) for track in tracks]


def problem_of(made: Contents | None, video_identity: str, signature: Sequence[tuple[str, str, str, bool, str]],
               duration: float | None) -> str | None:
    """Why a built container is not the one asked for; None when it is: its video, its tracks in order (name, title, key,
    own sound, language), the first the default one, and the length of the video it was built from."""
    if made is None:
        return "container_unreadable"
    if not made.has_video or made.video != video_identity:
        return "container_video_wrong"
    if made.signature != [tuple(each) for each in signature]:
        return "container_tracks_wrong"
    if made.audio and not (made.audio[0].default and not any(track.default for track in made.audio[1:])):
        return "container_default_wrong"
    if duration and abs(made.duration - duration) > DURATION_SLACK_S:
        return "container_length_wrong"
    return None


async def contents(path: str, *, timeout: float = READ_TIMEOUT_S) -> Contents | None:
    """What a media file holds, by its tags; None when it cannot be read. A file not built here has no tags: its video
    identity is empty and its audio tracks are labelled by their titles."""
    executable = program("ffprobe")
    if executable is None:
        return None
    cmd = [executable, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    try:
        code, out, _ = await run_capture(cmd, timeout=timeout)
        if code not in (0, LOST):  # a lost status says nothing: the whole answer read to its end is judged
            return None
        info = json.loads(out.decode() or "{}")
    except (OSError, TimeoutError, ValueError):
        return None
    tags = _tags(info.get("format", {}))
    streams = info.get("streams", [])
    audio = []
    for number, stream in enumerate(each for each in streams if each.get("codec_type") == "audio"):
        own = _tags(stream)
        audio.append(AudioOut(index=number, label=own.get("st_voice") or own.get("title", ""),
                              title=own.get("title", ""), aligned_to=own.get("st_aligned_to", ""),
                              default=bool((stream.get("disposition") or {}).get("default")),
                              language=own.get("language") or UNSTATED, own=own.get("st_own") == "1",
                              key=own.get("st_dub", "")))
    subtitles = []
    for number, stream in enumerate(each for each in streams if each.get("codec_type") == "subtitle"):
        own = _tags(stream)
        subtitles.append(SubtitleOut(index=number, key=own.get("st_sub", ""), title=own.get("title", ""),
                                     language=own.get("language", ""),
                                     forced=bool((stream.get("disposition") or {}).get("forced")),
                                     default=bool((stream.get("disposition") or {}).get("default")),
                                     at_start=bool(own.get("duration")) and set(own["duration"]) <= set("0:.")))
    try:
        duration = float(info.get("format", {}).get("duration") or 0.0)
    except ValueError:
        duration = 0.0
    film = film_stream(streams)
    return Contents(video=tags.get("st_video", ""), duration=duration, has_video=film is not None, audio=audio,
                    publication=tags.get("st_publication", ""), height=int((film or {}).get("height") or 0),
                    subtitles=subtitles)


def _tags(holder: dict) -> dict[str, str]:
    """Tags with their names in lower case: MKV keeps them in upper case, MP4 as they were written."""
    return {str(name).lower(): str(value) for name, value in (holder.get("tags") or {}).items()}
