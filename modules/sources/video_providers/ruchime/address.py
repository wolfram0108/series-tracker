"""Series address of a Ruchime release: the page plus what the user chose about its players.

The choice travels in the address, as at dunhuatv.ru, so the series row needs no extra columns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

PLAYERS = ("vk", "allvideo", "vibix")
_VIBIX_LINK = re.compile(r"^vibix=(\d+)/(\d+)$")


@dataclass(frozen=True)
class Choice:
    """`vibix_season` is the 1-based position of a season in the Vibix catalog; `off` names disabled players."""

    vibix_season: int | None = None
    off: frozenset[str] = frozenset()

    def toggled(self, player: str) -> "Choice":
        return Choice(self.vibix_season, self.off ^ {player})

    def with_season(self, position: int) -> "Choice":
        return Choice(position, self.off)


def choice_of(url: str) -> Choice:
    query = parse_qs(urlparse(url).query)
    season = (query.get("vibix") or [""])[0]
    off = {name for value in query.get("off", []) for name in value.split(",")} & set(PLAYERS)
    return Choice(int(season) if season.isdigit() and int(season) > 0 else None, frozenset(off))


def address_of(page_url: str, choice: Choice) -> str:
    """The page alone while nothing is chosen, so series added before the choice existed keep their address."""
    query: dict[str, object] = {}
    if choice.vibix_season is not None:
        query["vibix"] = choice.vibix_season
    if choice.off:
        query["off"] = ",".join(name for name in PLAYERS if name in choice.off)
    return f"{page_url}?{urlencode(query, safe=',')}" if query else page_url


def vibix_link(page_url: str, season: int, number: int) -> str:
    """Candidate link of one Vibix episode: the catalog entry the scan checked is the one that gets downloaded."""
    return f"{page_url}#vibix={season}/{number}"


def parse_vibix_link(url: str) -> tuple[int, int] | None:
    found = _VIBIX_LINK.match(urlparse(url).fragment)
    return (int(found.group(1)), int(found.group(2))) if found else None
