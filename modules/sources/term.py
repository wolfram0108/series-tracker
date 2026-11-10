"""How long what a source answered stays current: the table of a page, the listing of a VK channel, the address a parser
of a video application gave for an episode. One term for all of them, the owner's setting `sources_term_hours` (whole
hours, an hour by default); a read the user asks for takes nothing kept, whatever the term.

The term is the subject of the task (what is asked again and when), not a wait: nothing sleeps on it."""
from __future__ import annotations

from datetime import timedelta

TERM_KEY = "sources_term_hours"
DEFAULT_HOURS = 1


def hours_of(value: object) -> int | None:
    """The hours a stored or sent value names: a whole number of at least one; None for anything else."""
    try:
        hours = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return hours if hours >= 1 else None


class Term:
    """The term shared by the readers of the module: the setting changes it for every one of them at once."""

    def __init__(self, hours: int = DEFAULT_HOURS) -> None:
        self.hours = hours

    @property
    def seconds(self) -> float:
        return self.hours * 3600.0

    @property
    def delta(self) -> timedelta:
        return timedelta(hours=self.hours)
