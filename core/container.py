"""The container of an episode (ЭТАП_11): one MKV with the video of the reference and the voice-overs in order.

Nothing is encoded again: the video and every audio track are copied as they are (`-c copy`), the way track-muxer
builds its containers (`mux/build.py`): the first track is the default one, the others are not; the tracks interleave
evenly (`-max_interleave_delta 0`). The container says what it holds by its own tags, so that the file is its own
ledger: `ST_VIDEO` (the identity of the video), and for every audio track `ST_VOICE` (the name of the voice-over group),
the title the player shows (the user's name of the track, else the name of the group), `ST_DUB` (the key of the voice-over, which a rename of its group does not change: the track is known by it),
`ST_ALIGNED_TO` (the identity of the video the track was aligned to) and `ST_OWN` (1 for the video's own sound), and
`ST_PUBLICATION`, the number of the publication that built it: two containers of the same video and tracks (another
resolution) are told apart by it, so a publication cut short knows whether its file is the one under the name. Whether a
build is done is told by reading the result back (`contents`), never by the code ffmpeg returned.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Sequence

from .ffprobe import film_stream, streams_of
from .proc import LOST, program, run_capture

LANGUAGE = "rus"


def identity(source_id: int | None, row_key: str | None) -> str:
    """The identity of a video: the same source and the same row of its table, whatever the resolution (ЭТАП_11, point 5).
    Empty when either is unknown: such a video is the same as no other."""
    return f"{source_id}:{row_key}" if source_id is not None and row_key else ""
# A build copies streams and a read looks at the head of the file; both end in seconds even for a 2160p episode, so these
# are safeguards: a build or a read past them is a failure, not "done".
BUILD_TIMEOUT_S = 600.0
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

    @property
    def shown(self) -> str:
        return self.title or self.label


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

    @property
    def labels(self) -> list[str]:
        return [track.label for track in self.audio]

    @property
    def signature(self) -> list[tuple[str, str, str, bool]]:
        """What the tracks are, in order: name, title, key, own sound."""
        return [(track.label, track.title, track.key, track.own) for track in self.audio]

    @property
    def own_track(self) -> int:
        """The index among the audio streams of the video's own sound; the first one when no tag says (a plain file)."""
        return next((track.index for track in self.audio if track.own), 0)


PROVENANCE = "provenance.json"  # the attachment that says where what the container holds came from (stage 12)


async def build(output: str, video: str, video_identity: str, tracks: Sequence[AudioIn], *, publication: str = "",
                timeout: float = BUILD_TIMEOUT_S, provenance: dict | None = None) -> str | None:
    """Write the container at `output` (an .mkv the caller puts in place); None when it was built and reads back as asked,
    else the reason. The video is the film of `video` (core.ffprobe.film_stream); a track of the same file is taken
    from it."""
    executable = program("ffmpeg")
    if executable is None:
        return "ffmpeg_missing"
    film = film_stream(await streams_of(video, timeout=READ_TIMEOUT_S) or [])
    if film is None:
        return "no_video"  # the file has no film to build of (no video stream, or only a picture it carries)
    sources = [video] + [path for path in dict.fromkeys(track.path for track in tracks) if path != video]
    index_of = {path: number for number, path in enumerate(sources)}
    cmd = [executable, "-y", "-hide_banner", "-loglevel", "error"]
    for path in sources:
        cmd += ["-i", path]
    cmd += ["-map", f"0:{film['index']}"]
    for track in tracks:
        cmd += ["-map", f"{index_of[track.path]}:a:{track.stream}"]
    cmd += ["-map_metadata", "-1", "-map_chapters", "-1", "-c", "copy", "-max_interleave_delta", "0",
            "-metadata", f"ST_VIDEO={video_identity}", "-metadata", f"ST_PUBLICATION={publication}"]
    for number, track in enumerate(tracks):
        cmd += [f"-metadata:s:a:{number}", f"title={track.shown}", f"-metadata:s:a:{number}", f"language={LANGUAGE}",
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
        cmd += ["-attach", attached, "-metadata:s:t", "mimetype=application/json", "-metadata:s:t", f"filename={PROVENANCE}"]
    cmd += ["-f", "matroska", output]
    try:
        code, _, err = await run_capture(cmd, timeout=timeout)
    except (OSError, TimeoutError) as exc:
        return f"ffmpeg_failed: {type(exc).__name__}"
    finally:
        if attached is not None and os.path.exists(attached):
            os.remove(attached)
    # An end whose status was lost tells nothing: the container that was written is what is judged.
    if code not in (0, LOST):
        return f"ffmpeg_failed: {err.decode(errors='replace').strip()[-300:]}"
    made = await contents(output)
    expected = await contents(video)
    if made is not None and made.publication != publication:
        return "container_publication_wrong"
    return problem_of(made, video_identity, signature_of(tracks), expected.duration if expected is not None else None)


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


def signature_of(tracks: Sequence[AudioIn]) -> list[tuple[str, str, str, bool]]:
    """What a container built of `tracks` holds, in the terms of `Contents.signature`."""
    return [(track.label, track.shown, track.key, track.own) for track in tracks]


def problem_of(made: Contents | None, video_identity: str, signature: Sequence[tuple[str, str, str, bool]],
               duration: float | None) -> str | None:
    """Why a built container is not the one asked for; None when it is: its video, its tracks in order (name, title, key,
    own sound), the first the default one, and the length of the video it was built from."""
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
                              language=own.get("language", ""), own=own.get("st_own") == "1",
                              key=own.get("st_dub", "")))
    try:
        duration = float(info.get("format", {}).get("duration") or 0.0)
    except ValueError:
        duration = 0.0
    film = film_stream(streams)
    return Contents(video=tags.get("st_video", ""), duration=duration, has_video=film is not None, audio=audio,
                    publication=tags.get("st_publication", ""), height=int((film or {}).get("height") or 0))


def _tags(holder: dict) -> dict[str, str]:
    """Tags with their names in lower case: MKV keeps them in upper case, MP4 as they were written."""
    return {str(name).lower(): str(value) for name, value in (holder.get("tags") or {}).items()}
