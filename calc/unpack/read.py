"""What the module unpack decides of the reading of the files of a release: which files are read, what a news of a release
changes, and what the count of the events of the subtitles tracks of a file makes of what was read of it. The files are read by
ffprobe in the module."""
from __future__ import annotations

from typing import Any, Mapping

from core.contracts.torrent import FileKind, ReleaseFile, ReleaseView, kind_of

READ = {FileKind.VIDEO, FileKind.AUDIO, FileKind.SUBTITLES}


def files_to_read(release: ReleaseView, known: Mapping[str, Mapping[str, Any]]) -> list[ReleaseFile]:
    """The video, sound and subtitles files of the version that need a read: those not read yet, or read for another version
    or size. A file read before the events of its subtitles were counted is read again: a track with none is no row. A file
    the client was told to leave (priority 0) is not read."""
    return [f for f in release.files if kind_of(f.path) in READ and f.priority != 0 and not (
        (stored := known.get(f.path)) and stored["infohash"] == release.infohash and stored["size"] == f.size
        and not any(s["kind"] == "subtitle" and s.get("events") is None for s in stored["streams"]))]


def shape_of(release: ReleaseView) -> tuple:
    """What the module takes of a release: a news that changes only how far its files are is none of its business."""
    return (release.state, release.infohash, release.folder, tuple((f.path, f.size, f.priority) for f in release.files))


def subtitle_tracks(layout: Mapping[str, Any]) -> list[dict]:
    """The subtitles streams of what was read of a file."""
    return [stream for stream in layout["streams"] if stream["kind"] == "subtitle"]


def with_events(layout: dict, counts: Mapping[int, int]) -> tuple[dict, list[dict]]:
    """The read of a file with the events of each subtitles stream counted (`counts`: stream → events), and the tracks that
    hold none: they are no row of the table."""
    streams = [{**stream, "events": counts.get(stream["index"], 0)} if stream["kind"] == "subtitle" else stream
               for stream in layout["streams"]]
    return {**layout, "streams": streams}, [stream for stream in subtitle_tracks({"streams": streams}) if stream["events"] == 0]
