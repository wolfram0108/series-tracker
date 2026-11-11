"""What an HLS media playlist holds besides the film: the blocks a host put into the stream from elsewhere.

One rule for whoever reads a playlist: the downloader saves the stream without them (modules/downloads/hls_native.py), and
the measure of the frame of a stream takes the first segment of what the downloader would save (modules/sources/players/hls.py).
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit


@dataclass(frozen=True)
class Insertion:
    """A block of segments the host put into the stream from elsewhere (an advertisement): where from, how many, how long."""
    origin: str
    segments: int
    seconds: float


# The tags that belong to the segment after them (a tag of the whole playlist comes before the first of them).
_SEGMENT_TAGS = ("#EXTINF", "#EXT-X-KEY", "#EXT-X-MAP", "#EXT-X-BYTERANGE", "#EXT-X-DISCONTINUITY",
                 "#EXT-X-PROGRAM-DATE-TIME")


def _origin(address: str) -> str:
    split = urlsplit(address)
    return f"{split.hostname}{posixpath.dirname(split.path)}"


def without_insertions(text: str, base: str) -> tuple[str, list[Insertion]]:
    """A media playlist without the blocks a host put into it from elsewhere.

    The stream is cut by #EXT-X-DISCONTINUITY into blocks; the origin of the stream (the host and folder of its segments)
    is the one that holds most of its length; a block every segment of which comes from another origin is an insertion
    and is not part of the episode (a CDN puts its advertisement into the playlist so, in another place at every answer).
    A key a dropped block set is not left to the blocks after it: the key before it is set again."""
    head, blocks, unit, started = [], [[]], [], False
    for line in (each.strip() for each in text.splitlines()):
        if not line:
            continue
        is_segment = not line.startswith("#")
        if not started and not is_segment and not line.startswith(_SEGMENT_TAGS):
            head.append(line)
            continue
        started = True
        if line.startswith("#EXT-X-DISCONTINUITY") and not line.startswith("#EXT-X-DISCONTINUITY-SEQUENCE"):
            blocks.append([])
        unit.append(line)
        if is_segment:
            blocks[-1].append(unit)
            unit = []
    tail = unit
    durations: dict[str, float] = {}
    for block in blocks:
        for each in block:
            seconds = next((float(tag.split(":", 1)[1].split(",")[0] or 0) for tag in each if tag.startswith("#EXTINF:")), 0.0)
            origin = _origin(urljoin(base, each[-1]))
            durations[origin] = durations.get(origin, 0.0) + seconds
    if not durations:
        return text, []
    own = max(durations, key=durations.get)
    out, dropped, key, lost_key = list(head), [], None, None
    for block in blocks:
        origins = {_origin(urljoin(base, each[-1])) for each in block}
        if block and own not in origins:
            seconds = sum(float(tag.split(":", 1)[1].split(",")[0] or 0) for each in block for tag in each
                          if tag.startswith("#EXTINF:"))
            dropped.append(Insertion(", ".join(sorted(origins)), len(block), round(seconds, 1)))
            if any(tag.startswith("#EXT-X-KEY:") for each in block for tag in each):
                lost_key = key or "#EXT-X-KEY:METHOD=NONE"
            continue
        for index, each in enumerate(block):
            tags = [tag for tag in each if not (tag.startswith("#EXT-X-DISCONTINUITY") and not out[len(head):])]
            if index == 0 and lost_key is not None and not any(tag.startswith("#EXT-X-KEY:") for tag in tags):
                tags.insert(0, lost_key)
            lost_key = None
            key = next((tag for tag in reversed(tags) if tag.startswith("#EXT-X-KEY:")), key)
            out += tags
    out += tail
    return ("\n".join(out) + "\n", dropped) if dropped else (text, [])
