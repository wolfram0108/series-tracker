"""The page players of Ruchime in the order they are tried, and the Vibix series-scope player until it moves to players/.

Priority is VK, then AllVideo, then Vibix. A player reports what it gave, so a button without a playable
stream never becomes a media candidate.
"""
from __future__ import annotations

from ..players.allvideo import AllVideoPlayer
from ..players.contract import Player, PlayerRef
from ..players.vk import VkPlayer


class VibixPlayer:
    name = "vibix"
    series_scope = True

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        if "vibix-embed" not in text:
            return None
        return PlayerRef(name=self.name, url=page_url)


PLAYER_ORDER: tuple[Player, ...] = (VkPlayer(), AllVideoPlayer(), VibixPlayer())
