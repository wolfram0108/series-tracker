"""How long what a source answered stays current: the table of a page, the listing of a VK channel, the address a parser
of the provider of external sources gave for an episode. Each site has its own term (the owner's setting `sources.terms`: minutes by
the site, release_table.site_of), since each site has its own cost of asking; a site the setting does not name has
DEFAULT_MINUTES. A read the user asks for takes nothing kept, whatever the term.

The term is the subject of the task (what is asked again and when), not a wait: nothing sleeps on it."""
from __future__ import annotations

import json
import math
from datetime import timedelta

from core.contracts.release_table import site_of

TERMS_KEY = "sources.terms"
DEFAULT_MINUTES = 60
# A term longer than a month keeps a page past any use of the term; it is a mistake of the hand.
MOST_MINUTES = 31 * 24 * 60


def minutes_of(value: object) -> int | None:
    """The minutes a sent value names: a whole number from one to MOST_MINUTES; None for anything else."""
    if isinstance(value, bool):
        return None
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return minutes if 1 <= minutes <= MOST_MINUTES else None


def terms_of(value: str | None) -> dict[str, int]:
    """The terms a stored value holds, site → minutes; an entry that is not one is left out."""
    try:
        found = json.loads(value) if value else {}
    except ValueError:
        found = {}
    if not isinstance(found, dict):
        return {}
    return {str(site): minutes for site, minutes in ((site, minutes_of(each)) for site, each in found.items())
            if minutes is not None}


class Term:
    """The terms shared by the readers of the module: the setting changes them for every one of them at once."""

    def __init__(self, minutes: dict[str, int] | None = None) -> None:
        self.minutes: dict[str, int] = dict(minutes or {})

    def of_site(self, site: str) -> timedelta:
        return timedelta(minutes=self.minutes.get(site, DEFAULT_MINUTES))

    def of(self, url: str) -> timedelta:
        """The term of the site of an address."""
        return self.of_site(site_of(url))

    def hours_of(self, url: str) -> int:
        """The term of the site of an address in whole hours, at least one (the socket of the provider counts hours)."""
        return max(1, math.ceil(self.of(url) / timedelta(hours=1)))
