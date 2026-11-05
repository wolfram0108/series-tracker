"""Pure reading of the address of a VK channel and of what the VK API says of a video; nothing here touches the network."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

from core.reasons import parse, reason

from .. import SiteError

CHANNEL_HOST = "https://vkvideo.ru"
# VK gives at most 100 videos a page whatever `count` asks (measured 2026-11-05 with both tokens): the step of the
# offset is the page, or the positions past it are never read.
PAGE_SIZE = 100


@dataclass(frozen=True)
class Address:
    """A channel and what is looked for in it: the videos whose title has one of the terms."""
    name: str
    terms: tuple[str, ...]

    @property
    def channel_url(self) -> str:
        return f"{CHANNEL_HOST}/@{self.name}"

    @property
    def canonical(self) -> str:
        """Channel and query in one string, so that the table is read from the address alone."""
        return f"{self.channel_url}|{'/'.join(self.terms)}"


def channel_name(channel_url: str) -> str | None:
    """'https://vkvideo.ru/@kinokong/...' -> 'kinokong'."""
    path = urlparse(channel_url).path.strip("/").split("/")
    part = next((segment for segment in path if segment.startswith("@") and len(segment) > 1), None)
    return part[1:] if part else None


def parse_address(url: str) -> Address | None:
    """`channel` or `channel|query`, the terms of the query parted by `/`. A channel with no query is an address of the
    site (a window knows it for a channel while the query is still to be typed), but it is not read."""
    channel, _, query = url.partition("|")
    name = channel_name(channel)
    if name is None or "|" in query:
        return None
    terms = tuple(term for term in (part.strip() for part in query.split("/")) if term)
    return Address(name, terms)


def owner_id_from_resolve(response: Mapping[str, Any], name: str) -> int:
    """The answer of `utils.resolveScreenName` -> owner_id (a group is negative)."""
    found = response.get("response")
    if not isinstance(found, dict) or "object_id" not in found:
        raise SiteError(reason("vk_channel_unknown", name=name))
    return -found["object_id"] if found.get("type") == "group" else found["object_id"]


def is_external(video: Mapping[str, Any]) -> bool:
    """A video that VK only links to (YouTube): it has no file of VK to take."""
    return video.get("platform") == "YouTube" or "external" in (video.get("files") or {})


def heights_of(video: Mapping[str, Any]) -> list[int] | None:
    """The steps of the files the API lists (`mp4_720`); None when the answer lists no files at all, which says nothing
    of the video (the listing of a channel does not give them)."""
    files = video.get("files")
    if not isinstance(files, dict) or not files:
        return None
    steps = set()
    for key in files:
        if key.startswith("mp4_") and key[4:].isdigit():
            steps.add(int(key[4:]))
    return sorted(steps)


def published_of(video: Mapping[str, Any]) -> str | None:
    stamp = video.get("date")
    if not isinstance(stamp, (int, float)) or stamp <= 0:
        return None
    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def link_of(video: Mapping[str, Any]) -> str:
    return f"https://vk.com/video{video.get('owner_id')}_{video.get('id')}"


def key_of(video: Mapping[str, Any]) -> str:
    return f"{video.get('owner_id')}_{video.get('id')}"


def keep_by_terms(videos: list[dict], terms: tuple[str, ...]) -> list[dict]:
    """The search of a channel, made over its whole listing: a term in the title, whatever the case."""
    lowered = [term.lower() for term in terms]
    return [video for video in videos if any(term in str(video.get("title", "")).lower() for term in lowered)]


def error_code_of(failure: SiteError) -> int | None:
    """The code VK gave in an error of the API, taken from the reason the call put in the `SiteError`."""
    code = ((parse(failure.reason) or {}).get("params") or {}).get("error_code")
    return code if isinstance(code, int) else None


def dedupe(videos: list[dict]) -> list[dict]:
    seen: set[str] = set()
    kept = []
    for video in videos:
        if key_of(video) not in seen:
            seen.add(key_of(video))
            kept.append(video)
    return kept
