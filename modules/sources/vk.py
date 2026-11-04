"""VK video facts and channel-search responses, without browser automation.

Channel search uses VK Video's catalog API; listing the whole channel uses
video.get. Both kinds of credentials remain inside trackerauth.

Pure parsing lives here; network orchestration lives in module.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

API_BASE = "https://api.vk.com/method/"
PAGE_SIZE = 200


class VkScanError(RuntimeError):
    def __init__(self, message: str, *, error_code: int | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


def screen_name_from_url(channel_url: str) -> str:
    """'https://vkvideo.ru/@kinokong/...' -> 'kinokong'."""
    path_parts = urlparse(channel_url).path.strip("/").split("/")
    part = next((p for p in path_parts if p.startswith("@")), None)
    if not part:
        raise VkScanError(
            f"в URL канала нет имени (@...): {channel_url!r}")
    return part[1:]


def owner_id_from_resolve(response: dict) -> int:
    """Ответ utils.resolveScreenName -> owner_id (группы — с минусом)."""
    obj = response.get("response")
    if not isinstance(obj, dict) or "object_id" not in obj:
        raise VkScanError(f"неожиданный ответ resolveScreenName: {response}")
    object_id = obj["object_id"]
    return -object_id if obj.get("type") == "group" else object_id


def video_to_fact(video: dict) -> dict | None:
    """Сырое видео API -> факт для сканера; None — внешнее видео (YouTube)."""
    files = video.get("files") or {}
    if video.get("platform") == "YouTube" or "external" in files:
        return None

    max_resolution = 0
    for key in files:
        if key.startswith("mp4_"):
            try:
                max_resolution = max(max_resolution, int(key.split("_")[1]))
            except (ValueError, IndexError):
                continue

    publication = datetime.fromtimestamp(video.get("date", 0), tz=timezone.utc)
    return {
        "title": video.get("title", "Без названия"),
        "url": f"https://vk.com/video{video.get('owner_id')}_{video.get('id')}",
        # ISO в UTC; формула unique_id (констрейнт данных, см. Р-10)
        # применяется в scan из этой даты
        "publication_date": publication.isoformat().replace("+00:00", "Z"),
        "resolution": max_resolution or None,
    }


def filter_by_terms(videos: list[dict], terms: list[str]) -> list[dict]:
    """Локальный поиск режима get_all: вхождение любого запроса в название."""
    if not terms:
        return videos
    lowered = [t.lower() for t in terms]
    return [v for v in videos
            if any(t in v.get("title", "").lower() for t in lowered)]


def dedupe_by_id(videos: list[dict]) -> list[dict]:
    seen, result = set(), []
    for v in videos:
        key = (v.get("owner_id"), v.get("id"))
        if key not in seen:
            seen.add(key)
            result.append(v)
    return result


def search_block(response: dict, owner_id: int) -> dict | None:
    """Only the channel's video block; catalog_videos contains other creators."""
    catalog = response.get("catalog")
    if not isinstance(catalog, dict) or not isinstance(catalog.get("sections"), list):
        raise VkScanError("VK: неожиданный ответ поиска по каналу (нет каталога)")
    for section in catalog["sections"]:
        for block in section.get("blocks", []):
            if (block.get("data_type") == "videos"
                    and block.get("layout", {}).get("owner_id") == owner_id):
                return block
    if response.get("videos"):
        raise VkScanError("VK: в ответе поиска нет блока видео выбранного канала")
    return None


def search_page(response: dict, block: dict) -> list[dict]:
    """The response may contain recommendations too; membership is given by videos_ids."""
    video_ids, videos = block.get("videos_ids"), response.get("videos", [])
    if not isinstance(video_ids, list) or not isinstance(videos, list):
        raise VkScanError("VK: неожиданный формат страницы поиска")
    wanted = set(video_ids)
    items = [v for v in videos if f"{v.get('owner_id')}_{v.get('id')}" in wanted]
    if len({f"{v.get('owner_id')}_{v.get('id')}" for v in items}) != len(wanted):
        raise VkScanError("VK: поиск вернул неполную страницу видео")
    return items
