"""Pure reading of the address of a VK channel and of what the VK API says of a video; nothing here touches the network."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

from core.reasons import parse, reason

from .. import SiteError

CHANNEL_HOST = "https://vkvideo.ru"
SEARCH, GET_ALL = "search", "get_all"
MODES = (SEARCH, GET_ALL)
PAGE_SIZE = 200


@dataclass(frozen=True)
class Address:
    """A channel, what is looked for in it and how: `search` asks VK for each term, `get_all` lists the channel and keeps
    the videos whose title has a term (no terms keep all)."""
    name: str
    terms: tuple[str, ...]
    mode: str

    @property
    def channel_url(self) -> str:
        return f"{CHANNEL_HOST}/@{self.name}"

    @property
    def canonical(self) -> str:
        """Channel, query and mode in one string, so that the table is read from the address alone."""
        return f"{self.channel_url}|{'/'.join(self.terms)}|{self.mode}"


def channel_name(channel_url: str) -> str | None:
    """'https://vkvideo.ru/@kinokong/...' -> 'kinokong'."""
    path = urlparse(channel_url).path.strip("/").split("/")
    part = next((segment for segment in path if segment.startswith("@") and len(segment) > 1), None)
    return part[1:] if part else None


def parse_address(url: str, options: Mapping[str, str]) -> Address | None:
    """`channel`, `channel|query` or the canonical `channel|query|mode`. The mode is the source's choice when it is given
    (`vk_search_mode`), else the one the address carries, else `search`."""
    channel, _, rest = url.partition("|")
    query, _, carried = rest.partition("|")
    name = channel_name(channel)
    if name is None:
        return None
    mode = next((value for value in (options.get("vk_search_mode"), carried) if value in MODES), SEARCH)
    terms = tuple(term for term in (part.strip() for part in query.split("/")) if term)
    return Address(name, terms, mode)


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
    """The local search of `get_all`: a term in the title, whatever the case; no terms keep every video."""
    if not terms:
        return videos
    lowered = [term.lower() for term in terms]
    return [video for video in videos if any(term in str(video.get("title", "")).lower() for term in lowered)]


def error_code_of(failure: SiteError) -> int | None:
    """The code VK gave in an error of the API, taken from the reason the call put in the `SiteError`."""
    code = ((parse(failure.reason) or {}).get("params") or {}).get("error_code")
    return code if isinstance(code, int) else None


def search_block(response: Mapping[str, Any], owner_id: int) -> dict | None:
    """The block of the videos of the channel in an answer of the catalog search; the recommendations hold other creators.
    None when the channel has none that match. An answer that is not a catalog is not an empty result."""
    catalog = response.get("catalog")
    if not isinstance(catalog, dict) or not isinstance(catalog.get("sections"), list):
        raise SiteError(reason("vk_reply_unexpected", method="catalog.getOwnerVideosSearch"))
    for section in catalog["sections"]:
        for block in section.get("blocks", []):
            if block.get("data_type") == "videos" and block.get("layout", {}).get("owner_id") == owner_id:
                return block
    if response.get("videos"):
        raise SiteError(reason("vk_reply_unexpected", method="catalog.getOwnerVideosSearch"))
    return None


def search_page(response: Mapping[str, Any], block: Mapping[str, Any]) -> list[dict]:
    """The videos of one page of the block: the answer may carry recommendations too, the membership is `videos_ids`."""
    video_ids, videos = block.get("videos_ids"), response.get("videos", [])
    if not isinstance(video_ids, list) or not isinstance(videos, list):
        raise SiteError(reason("vk_reply_unexpected", method="catalog.getBlockItems"))
    wanted = set(video_ids)
    items = [video for video in videos if key_of(video) in wanted]
    if len({key_of(video) for video in items}) != len(wanted):
        raise SiteError(reason("vk_reply_unexpected", method="catalog.getBlockItems"))
    return items


def dedupe(videos: list[dict]) -> list[dict]:
    seen: set[str] = set()
    kept = []
    for video in videos:
        if key_of(video) not in seen:
            seen.add(key_of(video))
            kept.append(video)
    return kept
