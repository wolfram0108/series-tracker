"""A release of a tracker page in the one form of the torrent source (`PageRelease`), from what a parser read.

The id of a release on its page (`release`) is what stays the same from version to version: a new version of a release
comes with a new link, date and infohash, and must be known as the same release. A page of one release (kinozal,
rutracker) has one; a catalog keeps a release per quality (anilibria, anilibria_tv: the range of episodes grows in it) or
a release per episodes and quality (astar).
"""
from __future__ import annotations

from core.contracts.torrent import PageRelease

PAGE = "page"


def release_id(service: str, raw: dict) -> str:
    if service in ("kinozal", "rutracker"):
        return PAGE
    if service in ("anilibria", "anilibria_tv"):
        return raw.get("quality") or raw.get("label") or raw.get("episodes") or PAGE
    return " ".join(part for part in (raw.get("episodes"), raw.get("quality")) if part) or PAGE


def page_release(service: str, raw: dict) -> PageRelease:
    """`raw` is a release of `parsers` / `anilibria` ({link, magnet, date_marker, episodes, quality, hash?, label?})."""
    words = " ".join(str(part) for part in (raw.get("episodes"), raw.get("quality")) if part)
    return PageRelease(release=release_id(service, raw), title=words, link=raw.get("link"), magnet=raw.get("magnet"),
                       published=raw.get("date_marker"), episodes=raw.get("episodes"), quality=raw.get("quality"),
                       infohash=(raw.get("hash") or "").lower() or None)
