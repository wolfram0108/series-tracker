"""The page players of Ruchime in the order they are tried, and the Vibix series-scope player until it moves to players/.

Priority is VK, then AllVideo, then Vibix. A player reports what it gave, so a button without a playable
stream never becomes a media candidate.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.reasons import reason

from ..players.allvideo import AllVideoPlayer
from ..players.contract import Kind, Player, PlayerRef
from ..players.vk import VkPlayer
from ..policy import Verdict


@dataclass(frozen=True)
class PlayerProbe:
    ref: PlayerRef
    available: bool
    resolution: int | None = None
    reason: str = ""
    transient: bool = False


def probe_from(verdict: Verdict, ref: PlayerRef, label: str) -> PlayerProbe:
    """What a settled ask means to the provider; `transient` is an answer that stayed unsure to the end."""
    reading = verdict.reading
    if reading.kind is Kind.PLAYABLE:
        return PlayerProbe(ref, True, max(reading.resolutions, default=None))
    if reading.kind is Kind.NOT_FOUND:
        return PlayerProbe(ref, False, reason=reason("player_video_missing", player=label))
    if reading.kind is Kind.RESTRICTED:
        return PlayerProbe(ref, False, reason=reason("video_private"))
    if reading.kind is Kind.HTTP_ERROR:
        return PlayerProbe(ref, False, reason=reason("player_status", player=label, status=reading.status),
                           transient=verdict.exhausted)
    if reading.kind is Kind.NO_ANSWER:
        return PlayerProbe(ref, False, reason=reason("player_no_answer", player=label), transient=True)
    return PlayerProbe(ref, False, reason=reason("player_unconfirmed", player=label), transient=True)


class VibixPlayer:
    name = "vibix"
    series_scope = True

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        if "vibix-embed" not in text:
            return None
        return PlayerRef(name=self.name, url=page_url)


PLAYER_ORDER: tuple[Player, ...] = (VkPlayer(), AllVideoPlayer(), VibixPlayer())
