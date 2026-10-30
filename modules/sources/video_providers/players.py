"""The page players of Ruchime in the order they are tried.

Priority is VK, then AllVideo, then Vibix. A player reports what it gave, so a button without a playable
stream never becomes a media candidate.
"""
from __future__ import annotations

from ..players.allvideo import AllVideoPlayer
from ..players.contract import Player
from ..players.vibix import VibixPlayer
from ..players.vk import VkPlayer


PLAYER_ORDER: tuple[Player, ...] = (VkPlayer(), AllVideoPlayer(), VibixPlayer())
