"""The sites some source of a series reads, with how many sources read each and its term: what the tab of the terms lists."""
from __future__ import annotations

from typing import Iterable, Mapping

from calc.sources.address import is_web_address
from calc.sources.term import DEFAULT_MINUTES
from core.contracts import SourceType, TermSite
from core.contracts.release_table import site_of


def terms_view(addresses: Iterable[tuple[str, SourceType]], minutes: Mapping[str, int]) -> tuple[TermSite, ...]:
    """Every site some source reads (not a tracker: its table is the release `torrents` keeps), whether it is a site of the provider (an
    address that is no web address; the window lists those apart), how many sources read it and its term in minutes. The sites with the most
    sources come first, those of the provider last."""
    counted: dict[str, int] = {}
    provided: set[str] = set()
    for url, source_type in addresses:
        if source_type == SourceType.TORRENT:
            continue
        site = site_of(url)
        if site:
            counted[site] = counted.get(site, 0) + 1
            if not is_web_address(url):
                provided.add(site)
    order = sorted(counted, key=lambda site: (site in provided, -counted[site], site))
    return tuple(TermSite(site=site, provider=site in provided, sources=counted[site],
                          minutes=minutes.get(site, DEFAULT_MINUTES)) for site in order)


def sources_by_site(addresses: Iterable[str], site_of_address) -> dict[str, int]:
    """How many of the addresses each site reads; `site_of_address` names the site that reads an address, none for one no site reads."""
    found: dict[str, int] = {}
    for url in addresses:
        site = site_of_address(url or "")
        if site:
            found[site] = found.get(site, 0) + 1
    return found
