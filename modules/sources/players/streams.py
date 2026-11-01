"""The stream of a candidate, asked for right before the download: the addresses of the files expire, so they are not kept.

A candidate link names its player: a VK player link with its key, an AllVideo embed, a Vibix catalog entry. The player that
owns the link resolves it; an address no player owns is not handled here.
"""
from __future__ import annotations

from urllib.parse import urldefrag

import httpx

from core.reasons import reason

from ..policy import probe_from, settle
from .contract import Kind, PlayerRef, StreamUnavailable
from .registry import PlayerRegistry
from .vibix import VibixFailure
from .vk_files import pick_file

# The page that embeds the players, as the hosts see it: VK asks the API on behalf of this domain.
EMBED_PAGE = "https://1.ruchime.org/"


async def resolve_stream(players: PlayerRegistry, url: str, http: httpx.AsyncClient,
                         max_height: int | None = None) -> dict | None:
    """{stream_url, headers, progressive?} for a link a player owns; None for any other address.

    Raises StreamUnavailable when the player owns the link but has no stream for it now."""
    if "vk" in players and players.get("vk").owns(url):
        return await _resolve_vk(players, url, http, max_height)
    if "allvideo" in players and players.get("allvideo").owns(url):
        return await players.get("allvideo").resolve(url, http)
    if "vibix" in players and players.get("vibix").owns(url):
        try:
            return await players.get("vibix").resolve(url, http)
        except VibixFailure as exc:
            raise StreamUnavailable(exc.reason, transient=exc.transient) from exc
    for name in ("cvh", "kodik"):
        if name in players and players.get(name).owns(url):
            return await _resolve_listed(players, name, url, http, max_height)
    return None


async def _resolve_listed(players: PlayerRegistry, name: str, url: str, http: httpx.AsyncClient,
                          max_height: int | None) -> dict:
    """A link into a list (an item of CDN VideoHub or of Kodik) -> its best file within the cap now. The page the link is
    a fragment of is the page that embeds the player: it is what the player is asked as."""
    player = players.get(name)
    asked = player.capped(max_height) if hasattr(player, "capped") else player
    ref = PlayerRef(name, url)
    verdict = await settle(asked, ref, http, urldefrag(url).url, pacer=players.pacer)
    picked = pick_file(verdict.reading.files, max_height) if verdict.reading.kind is Kind.PLAYABLE else None
    if picked is None:
        probe = probe_from(verdict, ref, player.label)
        raise StreamUnavailable(reason("player_failed", player=player.label, cause=probe.reason), probe.transient)
    return {"stream_url": picked[1], "headers": dict(player.stream_headers)}


async def _resolve_vk(players: PlayerRegistry, url: str, http: httpx.AsyncClient, max_height: int | None) -> dict:
    """A player link with its key -> the address of one file now."""
    player = players.get("vk")
    ref = player.ref_of(url)
    verdict = await settle(player.with_files(), ref, http, EMBED_PAGE, pacer=players.pacer)
    probe = probe_from(verdict, ref, "VK")
    picked = pick_file(verdict.reading.files, max_height) if verdict.reading.kind is Kind.PLAYABLE else None
    if picked is None:
        raise StreamUnavailable(probe.reason or reason("player_unconfirmed", player="VK"),
                                transient=probe.transient or probe.available)
    return {"stream_url": picked[1], "headers": {}, "progressive": True}
