"""The screens of the sources made of what the module holds: the settings of the readers of pages, the picture of the provider, a stream found."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Sequence

from core.contracts import (ParsedDomains, ProviderSite, ProviderView, SourcesSettingsView, StreamReply, Tracker, TrackerMirrors)
from core.contracts.provider import Manifest


@dataclass(frozen=True)
class Reader:
    """A site of the registry as the settings list it: its name, the domains it reads and whether it reads trackers' pages (it is then listed
    as its tracker, once)."""

    name: str
    domains: tuple[str, ...]
    of_tracker: bool


def sources_settings(trackers: Sequence[Tracker], sites: Sequence[Reader]) -> SourcesSettingsView:
    """The settings screen of the sources whole: the trackers with the mirrors their pages are read on, then the sites of the registry with
    their own domains. A tracker is listed once, as a tracker, though its page is read by a site of the registry too."""
    shown = tuple(TrackerMirrors(id=each.id, display_name=each.display_name, mirrors=each.mirrors) for each in trackers)
    readers = tuple(ParsedDomains(name=each.display_name, kind="tracker", domains=each.mirrors) for each in shown)
    readers += tuple(ParsedDomains(name=site.name, kind="site", domains=site.domains) for site in sites if not site.of_tracker)
    return SourcesSettingsView(trackers=shown, readers=readers)


def provider_view(*, url: str, has_token: bool, state: str, why: str | None, manifest: Manifest | None, sources: Mapping[str, int],
                  downloads_dir: str, seq: int) -> ProviderView:
    """Everything the tab of the provider shows, every value stated: the socket (`state`, and `why` it is not up), the facts of the last
    manifest and its sites with how many sources of the series are the titles of each and how many lines of the order of sources are its."""
    sites: tuple[ProviderSite, ...] = ()
    if manifest is not None:
        lines = Counter(place.name for each in manifest.players if each.kind == "line" for place in each.places)
        sites = tuple(ProviderSite(name=each.name, label=each.label or each.name, sources=sources.get(each.name, 0),
                                   lines=lines.get(each.name, 0)) for each in manifest.sites)
    return ProviderView(
        url=url, has_token=has_token, state=state, reason=why or None,
        provider=manifest.provider.id if manifest else None, version=manifest.provider.version if manifest else None,
        protocol=manifest.protocol if manifest else None, revision=manifest.revision if manifest else None,
        sites=sites, downloads_dir=downloads_dir, seq=seq)


def stream_found(info: Mapping) -> StreamReply:
    """A stream a player resolved: its address and headers, and the way it is taken (see `StreamReply`)."""
    return StreamReply(handled=True, stream_url=info["stream_url"], headers=info.get("headers", {}),
                       progressive=bool(info.get("progressive")), playlist=bool(info.get("playlist")),
                       player_fetch=bool(info.get("player_fetch")), fetch_by=info.get("fetch_by") or None)
