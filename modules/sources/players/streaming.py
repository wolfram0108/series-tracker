"""The stream of a link into a list (an item of CDN VideoHub or of Kodik): one way for the players that share it."""
from __future__ import annotations

from urllib.parse import urldefrag

import httpx
from core.reasons import reason

from ..policy import Pacer, probe_from, settle
from .contract import Kind, PlayerRef, StreamUnavailable
from .vk_files import pick_file


async def listed_stream(player, url: str, http: httpx.AsyncClient, max_height: int | None, pacer: Pacer) -> dict:
    """A link into a list -> its best file within the cap now. The page the link is a fragment of is the page that
    embeds the player: it is what the player is asked as."""
    asked = player.capped(max_height)
    ref = PlayerRef(player.name, url)
    verdict = await settle(asked, ref, http, urldefrag(url).url, pacer=pacer)
    picked = pick_file(verdict.reading.files, max_height) if verdict.reading.kind is Kind.PLAYABLE else None
    if picked is None:
        probe = probe_from(verdict, ref, player.label)
        raise StreamUnavailable(reason("player_failed", player=player.label, cause=probe.reason), probe.transient)
    # What the file is decides how it is downloaded: a ready file in parts, a playlist by its segments; a player that
    # names neither is read by ffmpeg over one connection.
    kind = getattr(player, "stream_kind", "")
    return {"stream_url": picked[1], "headers": dict(player.stream_headers),
            "progressive": kind == "file", "playlist": kind == "playlist"}
