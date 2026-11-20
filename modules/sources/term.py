"""The terms the readers of the module share: the table of a page, the listing of a VK channel, the address a parser of the provider gave for an
episode are current for the term of their site. The setting `sources.terms` changes them for every reader at once, so they hold one object.
What a term is and how it is counted is calc/sources/term.py."""
from __future__ import annotations

from datetime import timedelta

from calc.sources.term import hours_of, term_of, term_of_site


class Term:
    """The terms shared by the readers of the module: the setting changes them for every one of them at once."""

    def __init__(self, minutes: dict[str, int] | None = None) -> None:
        self.minutes: dict[str, int] = dict(minutes or {})

    def of_site(self, site: str) -> timedelta:
        return term_of_site(self.minutes, site)

    def of(self, url: str) -> timedelta:
        """The term of the site of an address."""
        return term_of(self.minutes, url)

    def hours_of(self, url: str) -> int:
        """The term of the site of an address in whole hours, at least one (the socket of the provider counts hours)."""
        return hours_of(self.minutes, url)
