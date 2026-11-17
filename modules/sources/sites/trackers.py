"""The torrent trackers as sites (stage 15): the page of a tracker is a source of a series like the page of any site.

What the page lists (its releases, read from the tracker) is `sources.releases.get`; the table of the page is the tracks of
the releases `torrents` downloaded and `unpack` read: a site of a tracker reads no network for its table, it asks `unpack`
(`unpack.table.get`), and the table is read again when `unpack` says it changed (`unpack.table.changed`).

The domains of a tracker are its own and the mirrors of the settings (table `trackers`); the canonical address of a page is
on the tracker's own domain, whatever mirror the address was written with, so one page is one table.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from core import NO_HANDLER, BusRequestError
from core.contracts import SourceType
from core.reasons import code_of, reason

from . import ReadContext, SiteError, TableDraft, on_domain


@dataclass(frozen=True)
class _Page:
    """How an address names a page: by a group of its path (`prefix` + the group is the canonical path), or by a parameter
    of its query on the path `prefix`."""
    path: re.Pattern
    prefix: str
    query: str | None = None


class TrackerSite:
    source_type = SourceType.TORRENT.value

    def __init__(self, name: str, home: str, page: _Page) -> None:
        self.name, self._home, self._page = name, home, page
        self._mirrors: tuple[str, ...] = ()

    @property
    def domains(self) -> tuple[str, ...]:
        return (self._home, *(m for m in self._mirrors if m != self._home))

    def configure(self, tracker_mirrors: dict[str, list[str]] | None = None, **_wiring) -> None:
        if tracker_mirrors is not None:
            self._mirrors = tuple(m.lower() for m in tracker_mirrors.get(self.name, ()))

    def _id(self, url: str) -> str | None:
        parsed = urlparse(url)
        found = self._page.path.search(parsed.path)
        if found is None:
            return None
        if self._page.query is None:
            return found.group(1)
        value = parse_qs(parsed.query).get(self._page.query, [""])[0]
        return value if value.isdigit() else None

    def match_url(self, url: str) -> bool:
        return on_domain(url, self.domains) and self._id(url) is not None

    def canonical_url(self, url: str) -> str:
        page = self._id(url)
        if self._page.query is None:
            return f"https://{self._home}{self._page.prefix}{page}"
        return f"https://{self._home}{self._page.prefix}?{self._page.query}={page}"

    async def read(self, url: str, ctx: ReadContext) -> TableDraft:
        if ctx.ask is None:
            raise SiteError(reason("unpack_not_running"))
        try:
            answer = await ctx.ask("unpack.table.get", {"url": url})
        except BusRequestError as exc:
            if code_of(str(exc)) == NO_HANDLER:
                raise SiteError(reason("unpack_not_running")) from exc
            raise
        return TableDraft.model_validate(answer)


def tracker_sites() -> list[TrackerSite]:
    """The trackers the module reads; their names are the canonical names of the table `trackers`."""
    return [
        TrackerSite("kinozal", "kinozal.tv", _Page(re.compile(r"^/details\.php$"), "/details.php", "id")),
        TrackerSite("rutracker", "rutracker.org", _Page(re.compile(r"^/forum/viewtopic\.php$"), "/forum/viewtopic.php", "t")),
        # A tab of the page of a release (`…/torrents`, as the site's own address bar shows it) is the same release.
        TrackerSite("anilibria", "anilibria.top",
                    _Page(re.compile(r"^/(?:anime/releases/)?release/([^/?#]+)(?:/[a-z-]+)?/?$"), "/anime/releases/release/")),
        TrackerSite("anilibria_tv", "anilibria.tv", _Page(re.compile(r"^/release/([^/?#]+\.html)$"), "/release/")),
        TrackerSite("astar", "astar.bz", _Page(re.compile(r"^/([^?#]+\.html)$"), "/")),
    ]
