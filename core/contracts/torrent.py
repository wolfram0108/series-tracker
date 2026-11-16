"""The torrent source (stage 15): the releases a tracker page lists, and the link of a row of a release's track.

A page of a tracker lists releases (`sources.releases.get`); a release is downloaded by `torrents` into its folder, its
files are read by `unpack`, and every track becomes a row of the table of the page, as a row of a site. The player of
such a row is a release: `torrent:<release>`, a line of the player `torrent`; its link names the file and the stream.
"""
from __future__ import annotations

from urllib.parse import quote, unquote

from pydantic import BaseModel, ConfigDict, Field

TORRENT_PLAYER = "torrent"
_SCHEME = "torrent:"


class PageRelease(BaseModel):
    """A release as the page lists it. `release` is its id on the page, the same for every version of it: the page itself
    for a page of one release (kinozal, rutracker), the quality for a catalog of a release per quality (anilibria), the
    episodes and the quality for a catalog of a release per episode (astar)."""

    model_config = ConfigDict(extra="forbid")

    release: str
    title: str = ""
    link: str | None = None
    magnet: str | None = None
    published: str | None = None
    episodes: str | None = None
    quality: str | None = None
    infohash: str | None = None


class PageReleases(BaseModel):
    """`sources.releases.get {url}`: what the page lists now, read from the tracker; `url` is the canonical address."""

    model_config = ConfigDict(extra="forbid")

    url: str
    site: str
    title: str = ""
    releases: list[PageRelease] = Field(default_factory=list)


def player_of(release: str) -> str:
    """The player a row of a release's track names: the release is a line of the player `torrent`."""
    return f"{TORRENT_PLAYER}:{release}"


def track_link(release: str, video: str, stream: int, file: str | None = None) -> str:
    """`torrent:<release>/<video>#<stream>`: the stream `stream` of the video file `video` (paths in the release); a track of
    an external file names it with `?file=<path>`, the stream being that file's. The video of the row is always the video
    file, so every track of one video is known as one video whatever file it lies in."""
    link = f"{_SCHEME}{quote(release, safe='')}/{quote(video, safe='/')}"
    if file is not None:
        link += f"?file={quote(file, safe='/')}"
    return f"{link}#{stream}"


def parse_link(link: str) -> tuple[str, str, str, int] | None:
    """(release, video, file, stream) of a track link, `file` being the video itself for a stream of the container; None
    for any other address."""
    if not link.startswith(_SCHEME):
        return None
    body, _, stream = link[len(_SCHEME):].rpartition("#")
    body, _, query = body.partition("?file=")
    release, _, video = body.partition("/")
    if not release or not video or not stream.isdigit():
        return None
    video = unquote(video)
    return unquote(release), video, unquote(query) if query else video, int(stream)


def video_of_link(link: str) -> str:
    """Which video a track row is: the video file in its release (the video track of a source is never compared by content)."""
    parsed = parse_link(link)
    return f"{parsed[0]}/{parsed[1]}" if parsed else ""
