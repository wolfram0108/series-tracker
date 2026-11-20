"""Whether the provider of the socket can be asked for a page now, and the one word the tracker answers with when it cannot.

A page of the provider's world is one its sites read (the sites of the manifest, kept while the provider is down) or one whose
address is no web address at all (`title://…`: no site of the tracker reads it, and no generic reader could): such a page is read
and downloaded by the provider alone. While the provider is not up — none is configured, the feed dropped, its protocol is not
ours — nothing of that world is begun: a read says `provider_not_connected`, a sync of a series that has such a page is not
opened, a download is not handed to a generic downloader. Before the first look at the provider (its first hello, or its first
failure) it is not down, it is not known: whoever asks about such a page waits for the look (`ProviderHost.known`) and is answered
by what it found, so `connecting` is never an answer. A page of any other address is the business of the sites of the tracker,
whatever the provider does.

What an address is and what the state of the socket says is calc/sources/address.py; here is the part that looks at the registry of
the sites and at the connector."""
from __future__ import annotations

from calc.sources import address
from calc.sources.address import is_web_address

from ..sites import SiteRegistry
from .connector import Connector
from .remote import RemoteSite


def belongs_to_provider(sites: SiteRegistry, url: str) -> bool:
    """A page only the provider reads: a site of its manifest reads it, or no site reads it and it is no web address."""
    site = sites.find(url)
    return isinstance(site, RemoteSite) if site is not None else not is_web_address(url)


def not_connected(connector: Connector | None) -> str | None:
    """The reason the provider cannot be asked now (a code of core/reasons); None when it is up."""
    return address.not_connected(None if connector is None else connector.state, "" if connector is None else connector.why)


def block(connector: Connector | None, sites: SiteRegistry, url: str) -> str | None:
    """The reason a page of the provider's world cannot be read or downloaded now; None for any other page and for a
    provider that is up."""
    return not_connected(connector) if belongs_to_provider(sites, url) else None
