"""What the module `sources` is asked and answers besides the tables of the pages (core/contracts/release_table.py): the pages that
cannot be read, the terms of the sites, the mirrors of the trackers, the file of a release, the stream and the download of a link."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from .frozen import Frozen
from .release_table import TableReady, UnreadPart


# --- the pages ---------------------------------------------------------------------------------------------------------------------

class PagesCheck(BaseModel):
    """`sources.pages.check`: the addresses to ask about; none, none is blocked."""

    urls: list[str] = []


class BlockedPage(Frozen):
    """A page that cannot be read now because the provider that reads it is not up; `reason` is the code of core/reasons."""

    url: str
    reason: str


class PagesCheckReply(Frozen):
    blocked: tuple[BlockedPage, ...]


class PageUrl(BaseModel):
    """`sources.releases.get`, `torrents.releases.get` and `torrents.release.check`: the page of a tracker whose releases are asked."""

    url: str


class ManifestApplied(Frozen):
    """The event `sources.provider.manifest`: the manifest of the provider was applied, its sites and players are those of this revision."""

    provider: str
    revision: str


class PagesForgotten(Frozen):
    """The event `sources.pages.forgotten`: the tables of these pages are gone, no source lists them."""

    urls: tuple[str, ...]


class PlayerReading(Frozen):
    """What a read gave of one player: its rows, by state and by height (the frame of a row that plays)."""

    player: str
    label: str
    rows: int
    states: dict[str, int]
    heights: dict[str, int]


class RowCause(Frozen):
    """A cause a row was left without a video with, counted: the player, the state the row stands in, the code of the reason."""

    player: str
    state: str
    reason: str
    rows: int


class TableTrace(Frozen):
    """The event `sources.table.trace`, for the dump of a run (modules/runlog): what a read of a page does. `begin` tells the
    page was taken for a read (`fresh`: the read ignores what is held); `end` tells how the read ended (`ok`, the `reason` of
    a failure, the `seconds` it took) and, for a read that gave a table, what it gave: the `rows`, the `players`, the `causes`
    of the rows without a video and the `unread` parts. The fields a kind does not tell are not set."""

    url: str
    site: str
    kind: Literal["begin", "end"]
    fresh: bool | None = None
    ok: bool | None = None
    reason: str | None = None
    seconds: float | None = None
    rows: int | None = None
    players: tuple[PlayerReading, ...] | None = None
    causes: tuple[RowCause, ...] | None = None
    unread: tuple[UnreadPart, ...] | None = None


class TableCancel(BaseModel):
    """`sources.table.cancel`: the read of the page that goes now stops."""

    url: str


class TableCancelled(Frozen):
    """`sources.table.cancel`: whether a read of the page was going (and is stopped)."""

    cancelled: bool


class TablesFresh(BaseModel):
    """`sources.tables.fresh`: the addresses whose tables are asked about."""

    urls: list[str] = []


class TablesFreshReply(Frozen):
    """The tables of the pages (by their canonical address) with the time they were read and the end of their term; a page with no table
    read is left out."""

    pages: tuple[TableReady, ...]


# --- the terms of the sites -----------------------------------------------------------------------------------------------------

class TermSite(Frozen):
    """A site some source of a series reads: whether it is a site of the provider (no web address), how many sources read it and the
    term of what it answered, in minutes."""

    site: str
    provider: bool
    sources: int
    minutes: int


class TermsReply(Frozen):
    sites: tuple[TermSite, ...]


class TermSet(BaseModel):
    """`sources.terms.set`: the term of one site. `minutes` is what the hand sent, checked by the module (a whole number from one to a month)."""

    site: str | None = None
    minutes: Any = None


class TermSaved(Frozen):
    """`sources.terms.set`: the term was stored (`minutes`), or refused with the code of the reason."""

    ok: bool
    minutes: int | None = None
    reason: str | None = None


# --- the trackers ----------------------------------------------------------------------------------------------------------------------

class Tracker(Frozen):
    """A tracker as its table keeps it (`trackers`), the columns in the order of the table; `mirrors` and `ui_features` are read from their JSON."""

    id: int
    canonical_name: str
    display_name: str
    mirrors: tuple[str, ...]
    parser_class: str
    auth_type: str
    ui_features: dict[str, Any]


class TrackersReply(Frozen):
    trackers: tuple[Tracker, ...]


class MirrorsSet(BaseModel):
    """`sources.tracker.set_mirrors`: the domains a tracker is read on."""

    tracker_id: int
    mirrors: list[str]


class MirrorsSaved(Frozen):
    ok: bool


# --- the file of a release and the stream of a link ------------------------------------------------------------------------

class TorrentFileGet(BaseModel):
    """`sources.torrent_file.get`: the `.torrent` behind a link, by the id of its release; kept in the cache of the module."""

    url: str
    torrent_id: str


class TorrentFileDrop(BaseModel):
    """`sources.torrent_file.drop`: the cached file of a release goes (a series was deleted)."""

    torrent_id: str


class TorrentFile(Frozen):
    content_b64: str
    from_cache: bool


class VideoStream(BaseModel):
    """`sources.video.stream`: the stream of a candidate link, asked for right before the download."""

    url: str
    max_height: int | None = None


class StreamReply(Frozen):
    """`sources.video.stream`: whether a player owns the link and what it says of it. `handled` false: no player does, the caller may
    give the address to a generic downloader. With a stream: its address, headers, and the way it is taken (`progressive`: one ready
    file read in parts; `playlist`: an HLS playlist taken by its segments; `player_fetch`: the player downloads the file itself, see
    `sources.video.fetch`; `fetch_by` `provider`: the player is a provider of the socket). With `error`: the link belongs to a player
    that has no stream for it, so the caller must not hand the page to a generic downloader; `transient` says it may pass, `unasked` that
    the player was not reached (the provider is not up), so the error is nobody's failure."""

    handled: bool
    stream_url: str | None = None
    headers: dict[str, str] | None = None
    progressive: bool | None = None
    playlist: bool | None = None
    player_fetch: bool | None = None
    fetch_by: str | None = None
    error: str | None = None
    transient: bool | None = None
    unasked: bool | None = None


class VideoFetch(BaseModel):
    """`sources.video.fetch`: the file of a link whose player downloads it itself, into `target`."""

    url: str
    target: str
    max_height: int | None = None
    connections: int = 1


class VideoFetched(Frozen):
    """`sources.video.fetch`: `ok` with the seconds and the longest stall and the height of the file, or not with the `error`; `final` is the
    source's last word (no such file), `kind` is `final`, `broken` (a way that broke) or `unasked` (the source was not reached)."""

    ok: bool
    seconds: float | None = None
    longest: float | None = None
    height: int | None = None
    error: str | None = None
    final: bool | None = None
    kind: str | None = None


class VideoFetchStop(BaseModel):
    target: str


class VideoFetchStopped(Frozen):
    stopped: bool

