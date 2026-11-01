"""The players of one module instance: one object per player, shared by every site and by the resolution of streams.

A player holds state worth sharing (the token of a VK session, the Vibix catalog and its browser capture), so a second
object of the same player would ask the host twice for what the first already knows.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from ..policy import Pacer
from .allvideo import AllVideoPlayer
from .contract import Player
from .vibix import VibixPlayer
from .vk import VkPlayer

# The gap between the asks of players to one host, so that a burst is not read as a missing source (rate limit).
PROBE_INTERVAL_S = 0.12


class PlayerRegistry:
    def __init__(self, players: Iterable[Player] = ()) -> None:
        self._players: dict[str, Player] = {}
        # One pacer for every ask of a player: the hosts of the players are shared by all the sites that show them.
        self.pacer = Pacer(lambda: PROBE_INTERVAL_S)
        for player in players:
            self.register(player)

    @classmethod
    def standard(cls, browser_getter: Callable | None = None, key_path: str | Path | None = None,
                 manifest_dir: str | Path | None = None) -> "PlayerRegistry":
        """The players that stand on their own: VK, AllVideo, Vibix (which needs the browser pool and the cache paths)."""
        return cls([VkPlayer(), AllVideoPlayer(), VibixPlayer(browser_getter, key_path, manifest_dir)])

    def register(self, player: Player) -> None:
        if player.name in self._players:
            raise ValueError(f"player {player.name!r} is registered twice")
        self._players[player.name] = player

    def get(self, name: str) -> Player:
        return self._players[name]

    def __contains__(self, name: str) -> bool:
        return name in self._players

    def __iter__(self):
        return iter(self._players.values())
