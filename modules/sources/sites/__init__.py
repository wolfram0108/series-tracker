"""The contract of a site and the registry of sites.

A site reads one page into a table of one form (`TableDraft`). Everything around the read (the term of a table, one
read per page, the storage, the events) belongs to the core in `table.py`; adding a site is registering it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from core.contracts import TableRow, UnreadPart

if TYPE_CHECKING:
    import httpx

# (phase, current, total): `phase` is one of the codes below, the counts are those of the phase.
Progress = Callable[[str, int, int], None]

# The phases of a read, as `progress` reports them; the interface words each as `sources.phase.<code>`.
PHASE_RELEASE = "release"    # the page of the release and the list of its episodes
PHASE_EPISODES = "episodes"  # the page of each episode
PHASE_PLAYERS = "players"    # the asks of the players on the rows
PHASE_CATALOG = "catalog"    # the catalog of a player that describes the whole series (Vibix)
PHASE_VIDEOS = "videos"      # the videos of a channel
PHASE_RECHECK = "recheck"    # the rows whose answer stayed unsure, looked at again when the rest is done
PHASES = (PHASE_RELEASE, PHASE_EPISODES, PHASE_PLAYERS, PHASE_CATALOG, PHASE_VIDEOS, PHASE_RECHECK)


class SiteError(Exception):
    """The site refuses to give a table; `reason` is a code of `core.reasons`, never a sentence."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ReadContext:
    """What the core gives a site for one read: the shared client, and the way to report how far the read is."""
    http: "httpx.AsyncClient"
    progress: Progress
    # Only the last this many episodes of each voice-over are read (a stand does not load a site with hundreds of requests);
    # None reads the whole page, as the product does.
    recent: int | None = None
    # The user asked for this read now: raw data that an earlier read of another page left for sharing is not taken.
    fresh: bool = False
    # The rows of the last complete table of the page: what a player said of a link that names its video for good (the
    # frame of a playlist) is taken from here instead of being asked again.
    last_rows: tuple[TableRow, ...] = ()
    # How many rows of each line of an application card to read (core/row_limit.py: `all`, `N` the first N, `-N` the last
    # N); a site that reads no lines ignores it. The core keeps the rows of the earlier read that this one did not take.
    rows: str = "all"
    # The players the owner switched off for every source (their names in the order of sources: `kodik`, `line:CO4K`):
    # a site asks none of them and lists no new row of them; the core keeps their rows of the last read as they were.
    off: frozenset[str] = frozenset()


class TableDraft(BaseModel):
    """What a site returns: the head, the rows and the parts of the page it could not read (`UnreadPart`; the core keeps
    their rows of the read before). Row keys are unique within the page (the core rejects a repeat)."""

    model_config = ConfigDict(extra="forbid")

    names: list[str] = Field(default_factory=list)
    # The name of the source a window shows in its head; empty when the place of the address is the name.
    title: str = ""
    site_voice: str = ""
    # The channel of the site the page belongs to (the community of a VK channel); a site of one publisher gives none.
    channel: str = ""
    rows: list[TableRow] = Field(default_factory=list)
    unread: list[UnreadPart] = Field(default_factory=list)


class Site(Protocol):
    """A site in the registry. `canonical_url` is the key of the page's table; `read` accepts it.

    The canonical address carries everything `read` needs, so the table is read the same way whoever asks. A site may
    also have `configure(**wiring)` for the wiring of the module (browser pool, cache paths, players, the VK API);
    it ignores the wiring it does not know."""

    name: str
    domains: tuple[str, ...]
    # The kind of source a page of this site makes in a series (`vk_video` or `web_video`); a window does not guess it.
    source_type: str

    def match_url(self, url: str) -> bool:
        """Whether the address is a page this site reads (the domain is already known to be the site's)."""
        ...

    def canonical_url(self, url: str) -> str:
        """The one spelling of the page's address: two addresses of one page give one table. Idempotent: the canonical
        address given back as `url` is itself."""
        ...

    async def read(self, url: str, ctx: ReadContext) -> TableDraft:
        """Read the whole page, all its players, into one table; refuse with `SiteError`, never return a part."""
        ...


def on_domain(url: str, domains: Iterable[str]) -> bool:
    """Whether the host of the address is one of the domains or a subdomain of one."""
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain.lower() or host.endswith("." + domain.lower()) for domain in domains)


class SiteRegistry:
    """The sites of one module instance; found by domain, then by the site's own reading of the address."""

    def __init__(self, sites: Iterable[Site] = ()) -> None:
        self._sites: list[Site] = []
        for site in sites:
            self.register(site)

    def register(self, site: Site) -> None:
        if not site.name or not site.domains:
            raise ValueError("a site needs a name and at least one domain")
        taken = {domain.lower() for known in self._sites for domain in known.domains}
        clash = taken & {domain.lower() for domain in site.domains}
        if any(known.name == site.name for known in self._sites) or clash:
            raise ValueError(f"site {site.name!r} repeats a registered name or domain: {sorted(clash)}")
        self._sites.append(site)

    def __iter__(self):
        return iter(self._sites)

    def find(self, url: str) -> Site | None:
        return next((site for site in self._sites if on_domain(url, site.domains) and site.match_url(url)), None)

    def configure(self, **wiring) -> None:
        """Late wiring (browser pool, cache paths) passed to the sites that ask for it."""
        for site in self._sites:
            configure = getattr(site, "configure", None)
            if callable(configure):
                configure(**wiring)
