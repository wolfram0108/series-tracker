"""Тесты VK-источника: чистые функции + сквозной скан через шину
(trackerauth подменён фиктивным модулем — токен и сеть не нужны).
"""
import json

import pytest

from core import BaseModule, Bus, BusRequestError, Runner
from core.db import Database
from modules.sources import vk
from modules.sources.module import SourcesModule


# --- чистые функции ---------------------------------------------------------------

def test_screen_name_from_url():
    assert vk.screen_name_from_url(
        "https://vkvideo.ru/@anime_channel/playlists") == "anime_channel"
    with pytest.raises(vk.VkScanError):
        vk.screen_name_from_url("https://vk.com/без/собаки")


def test_owner_id_group_negated():
    assert vk.owner_id_from_resolve(
        {"response": {"object_id": 123, "type": "group"}}) == -123
    assert vk.owner_id_from_resolve(
        {"response": {"object_id": 55, "type": "user"}}) == 55


def test_video_to_fact_resolution_and_url():
    fact = vk.video_to_fact({
        "id": 7, "owner_id": -123, "title": "Серия 1", "date": 1750000000,
        "files": {"mp4_360": "u", "mp4_1080": "u", "mp4_720": "u"}})
    assert fact["resolution"] == 1080
    assert fact["url"] == "https://vk.com/video-123_7"
    assert fact["publication_date"].endswith("Z")


def test_video_to_fact_skips_external():
    assert vk.video_to_fact({"platform": "YouTube", "files": {}}) is None
    assert vk.video_to_fact({"files": {"external": "yt"}}) is None


def test_filter_and_dedupe():
    videos = [{"id": 1, "title": "Магическая битва 1"},
              {"id": 1, "title": "Магическая битва 1"},
              {"id": 2, "title": "Другое аниме"}]
    deduped = vk.dedupe_by_id(videos)
    assert len(deduped) == 2
    assert vk.filter_by_terms(deduped, ["магическая"]) == [deduped[0]]


# --- сквозной скан через шину --------------------------------------------------------

class FakeTrackerauth(BaseModule):
    """Имитация trackerauth: отвечает страницами VK API."""
    name = "trackerauth"

    def __init__(self, bus, pages: dict):
        self._pages = pages  # method -> [items страницы 1, страницы 2, ...]
        self.calls: list[dict] = []
        self.error_mode = False
        super().__init__(bus)

    def register(self):
        self.handle("trackerauth.fetch", self.on_fetch)

    async def on_fetch(self, env):
        p = env.payload
        if self.error_mode:
            return {"status": 200, "text": json.dumps({"error": {
                "error_code": 5, "error_msg": "User authorization failed"}}),
                "final_url": p["url"], "content_type": "application/json"}
        method = p["url"].rsplit("/", 1)[-1]
        self.calls.append({"method": method, "params": p.get("params")})
        if method == "utils.resolveScreenName":
            body = {"response": {"object_id": 42, "type": "group"}}
        else:
            offset = int(p["params"].get("offset", 0))
            page = offset // vk.PAGE_SIZE
            pages = self._pages.get(method, [])
            items = pages[page] if page < len(pages) else []
            body = {"response": {"items": items}}
        return {"status": 200, "text": json.dumps(body),
                "final_url": p["url"], "content_type": "application/json"}


def _video(vid, title, date=1750000000):
    return {"id": vid, "owner_id": -42, "title": title, "date": date,
            "files": {"mp4_720": "u"}}


@pytest.fixture
async def vk_system(tmp_path):
    bus = Bus()
    pages = {"video.get": [
        [_video(1, "Тайтл 1 серия"), _video(2, "Трейлер другого")],
        [_video(3, "Тайтл 2 серия")],
    ]}
    fake = FakeTrackerauth(bus, pages)
    sources = SourcesModule(bus, Database(str(tmp_path / "x.db")),
                            vk_page_interval=0)

    class Probe(BaseModule):
        name = "probe"

    probe = Probe(bus)
    runner = Runner(bus, [fake, sources, probe])
    await runner.start()
    yield fake, probe
    await runner.stop()


@pytest.mark.asyncio
async def test_vk_scan_get_all_with_local_filter(vk_system):
    fake, probe = vk_system
    reply = await probe.request("sources.vk.scan", {
        "channel_url": "https://vkvideo.ru/@chan",
        "query": "тайтл", "search_mode": "get_all"}, timeout=10)
    titles = [v["title"] for v in reply["videos"]]
    assert sorted(titles) == ["Тайтл 1 серия", "Тайтл 2 серия"]
    # пагинация прошла все страницы до пустой
    offsets = [c["params"]["offset"] for c in fake.calls
               if c["method"] == "video.get"]
    assert offsets == [0, vk.PAGE_SIZE, 2 * vk.PAGE_SIZE]


@pytest.mark.asyncio
async def test_vk_scan_api_error_is_loud(vk_system):
    fake, probe = vk_system
    fake.error_mode = True
    with pytest.raises(BusRequestError, match="authorization failed"):
        await probe.request("sources.vk.scan", {
            "channel_url": "https://vkvideo.ru/@chan",
            "query": "x", "search_mode": "get_all"}, timeout=10)
