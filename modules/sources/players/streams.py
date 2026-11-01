"""The stream of a candidate, asked for right before the download: the addresses of the files expire, so they are not kept.

A candidate link names its player: a VK player link with its key, an AllVideo embed, a Vibix catalog entry, an item of
CDN VideoHub or of Kodik. The player that owns the link gives the stream, within the height the download may take; an
address no player owns is not handled here. A link a player owns is never "not handled": when the player cannot give
the stream, the answer is `StreamUnavailable`, so that a page of a player is not handed to a generic downloader.

A new player is found here by registering it: nothing in this module names a player.
"""
from __future__ import annotations

import httpx

from .contract import StreamPlayer
from .registry import PlayerRegistry


async def resolve_stream(players: PlayerRegistry, url: str, http: httpx.AsyncClient,
                         max_height: int | None = None) -> dict | None:
    """{stream_url, headers, progressive?} for a link a player owns; None for any other address.

    Raises StreamUnavailable when the player owns the link but has no stream for it now."""
    for player in players:
        if isinstance(player, StreamPlayer) and player.owns(url):
            return await player.stream(url, http, max_height, players.pacer)
    return None
