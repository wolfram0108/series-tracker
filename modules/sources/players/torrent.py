"""The player of the rows of a torrent source: a release is a line of it (`torrent:<release>`), its link names a track of a
file the release downloaded (core/contracts/torrent.py). Nothing is asked of a host: the file is on disk once the release is
downloaded, and the rows are made by `unpack`, not found on a page."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.contracts.torrent import TORRENT_PLAYER, parse_link, video_of_link
from core.reasons import reason

from .contract import Answer, Kind, PlayerRef, Reading, StreamUnavailable

if TYPE_CHECKING:
    import httpx


class TorrentPlayer:
    name = TORRENT_PLAYER
    series_scope = False
    lines = True  # every release of a page is a line of the player, chosen between as players are

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        return None

    async def fetch(self, ref: PlayerRef, http: "httpx.AsyncClient", page_url: str) -> Answer:
        return Answer(status=None, error="a torrent track is asked of no host")

    def read(self, answer: Answer) -> Reading:
        return Reading(kind=Kind.NOT_PLAYER)

    def video_of(self, url: str) -> str:
        return video_of_link(url)

    def owns(self, url: str) -> bool:
        return parse_link(url) is not None

    async def stream(self, url: str, http: "httpx.AsyncClient", max_height: int | None, pacer: Any) -> dict:
        # The track is in a file of the release on disk: it is taken from there by the sync, never streamed from a host.
        raise StreamUnavailable(reason("torrent_track_not_streamed"), transient=False)
