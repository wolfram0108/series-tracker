"""The players of the order of sources as the screen lists them, and where each is among the pages asked about."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from core.contracts import PlayerFacts, PlayerPlace
from core.contracts.provider import ManifestPlace, ManifestPlayer
from core.contracts.torrent import TORRENT_PLAYER


@dataclass(frozen=True)
class Registered:
    """A player of the registry of the module: its name and label, and whether it has lines (a family of a provider is listed by its lines)."""

    name: str
    label: str
    has_lines: bool


def _places(player: str, admitting: Iterable[ManifestPlace], pages: Mapping[tuple[str, str], int],
            labels: Mapping[str, str]) -> tuple[PlayerPlace, ...]:
    """The sites where a player is: those the manifest says it is in (with how many of the pages are its, none when none is), and every
    other site whose stored page holds its rows; the one with the most pages first."""
    found = {place.name: PlayerPlace(name=place.name, label=place.label, sources=pages.get((player, place.name), 0))
             for place in admitting}
    for (name, site), count in pages.items():
        if name == player and site not in found:
            found[site] = PlayerPlace(name=site, label=labels.get(site, site), sources=count)
    return tuple(sorted(found.values(), key=lambda place: (-place.sources, place.label)))


def players_list(registered: Iterable[Registered], provided: Iterable[ManifestPlayer], pages: Mapping[tuple[str, str], int],
                 heights: Mapping[str, int], labels: Mapping[str, str]) -> tuple[PlayerFacts, ...]:
    """Every player a row of a table can be of, with the words a window names it by: the players of the registry (a player with lines is
    listed by its lines, but the torrent: its releases take its one place), then the players the manifest of the provider names
    (`line:<code>` of the sources of the provider), each in every place the manifest says it is. `pages` is how many stored pages of each
    site hold rows of each player ({(player, site): pages}), `heights` the best height each player's rows were measured at, `labels` the
    label of each site of the provider."""
    players = [PlayerFacts(name=each.name, label=each.label or each.name,
                           kind="torrent" if each.name == TORRENT_PLAYER else "site",
                           height=heights.get(each.name), places=_places(each.name, (), pages, labels))
               for each in registered if not each.has_lines or each.name == TORRENT_PLAYER]
    for each in provided:
        players.append(PlayerFacts(name=each.name, label=each.label or each.name, kind=each.kind,
                                   height=heights.get(each.name), places=_places(each.name, each.places, pages, labels)))
    return tuple(players)
