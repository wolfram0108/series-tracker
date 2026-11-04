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
        self.responses: dict[str, list[dict]] = {}
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
        if method in self.responses:
            body = self.responses[method].pop(0)
        elif method == "utils.resolveScreenName":
            body = {"response": {"object_id": 42, "type": "group"}}
        else:
            offset = int(p["params"].get("offset", 0))
            page = offset // vk.PAGE_SIZE
            pages = self._pages.get(method, [])
            items = pages[page] if page < len(pages) else []
            total = (len(pages) - 1) * vk.PAGE_SIZE + len(pages[-1]) if pages else 0
            body = {"response": {"count": total, "items": items}}
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
    # All positions advertised by VK have been read, including sparse pages.
    offsets = [c["params"]["offset"] for c in fake.calls
               if c["method"] == "video.get"]
    assert offsets == [0, vk.PAGE_SIZE]


@pytest.mark.asyncio
async def test_vk_scan_api_error_is_loud(vk_system):
    fake, probe = vk_system
    fake.error_mode = True
    with pytest.raises(BusRequestError, match="authorization failed"):
        await probe.request("sources.vk.scan", {
            "channel_url": "https://vkvideo.ru/@chan",
            "query": "x", "search_mode": "get_all"}, timeout=10)


def test_vk_provider_no_browser_ua():
    """Находка 44: на браузерный UA VK API вырезает поле files (mp4-
    ссылки/разрешения), и resolution не вычислить. Провайдер VK обязан
    НЕ слать браузерный UA — как легаси (дефолтный requests-UA)."""
    from modules.trackerauth.providers import VkProvider, UA
    headers = VkProvider().request_headers("https://api.vk.com/method/video.get")
    assert headers.get("User-Agent") != UA
    assert "User-Agent" not in headers  # отдаём на откуп requests-дефолту


def _search_block(ids, cursor=None):
    block = {"id": "channel-search", "data_type": "videos",
             "layout": {"owner_id": -42}, "videos_ids": ids}
    if cursor:
        block["next_from"] = cursor
    return block


@pytest.mark.asyncio
async def test_fast_search_pages_only_channel_results_and_deduplicates_queries(vk_system):
    fake, probe = vk_system
    first, second = _video(1, "Тайтл 1"), _video(2, "Тайтл 2")
    foreign = {**_video(1, "Тайтл другого автора"), "owner_id": -99}
    fake.responses = {
        "catalog.getOwnerVideosSearch": [
            {"response": {"catalog": {"sections": [{"blocks": [
                _search_block(["-42_1"], "page-two"),
                {"id": "recommendations", "data_type": "catalog_videos",
                 "videos_ids": ["-99_1"], "next_from": "foreign-page"}]}]},
                "videos": [first, foreign], "catalog_videos": [foreign]}},
            {"response": {"catalog": {"sections": [{"blocks": [
                _search_block(["-42_2"])]}]}, "videos": [second]}},
        ],
        "catalog.getBlockItems": [{"response": {
            "block": _search_block(["-42_2"]), "videos": [second]}}],
    }
    reply = await probe.request("sources.vk.scan", {
        "channel_url": "https://vkvideo.ru/@chan", "query": "тайтл / title",
        "search_mode": "search"}, timeout=10)
    assert {v["url"] for v in reply["videos"]} == {
        "https://vk.com/video-42_1", "https://vk.com/video-42_2"}
    assert len(reply["videos"]) == 2
    assert all(v["resolution"] == 720 for v in reply["videos"])
    assert [c["method"] for c in fake.calls] == [
        "utils.resolveScreenName", "catalog.getOwnerVideosSearch",
        "catalog.getBlockItems", "catalog.getOwnerVideosSearch"]
    assert fake.calls[2]["params"] == {
        "block_id": "channel-search", "start_from": "page-two"}


@pytest.mark.asyncio
async def test_fast_search_empty_does_not_take_recommendations(vk_system):
    fake, probe = vk_system
    fake.responses["catalog.getOwnerVideosSearch"] = [{"response": {
        "catalog": {"sections": [{"blocks": [{"data_type": "catalog_videos"}]}]},
        "catalog_videos": [_video(8, "Рекомендация")]}}]
    reply = await probe.request("sources.vk.scan", {
        "channel_url": "https://vkvideo.ru/@chan", "query": "не существует",
        "search_mode": "search"}, timeout=10)
    assert reply["videos"] == []


@pytest.mark.asyncio
async def test_fast_search_repeated_cursor_fails_instead_of_returning_partial_scan(vk_system):
    fake, probe = vk_system
    block = _search_block(["-42_1"], "same-page")
    fake.responses = {
        "catalog.getOwnerVideosSearch": [{"response": {
            "catalog": {"sections": [{"blocks": [block]}]}, "videos": [_video(1, "Тайтл")]}}],
        "catalog.getBlockItems": [{"response": {"block": block, "videos": [_video(1, "Тайтл")]}}],
    }
    with pytest.raises(BusRequestError, match="повторилась страница"):
        await probe.request("sources.vk.scan", {
            "channel_url": "https://vkvideo.ru/@chan", "query": "тайтл",
            "search_mode": "search"}, timeout=10)


def test_search_does_not_turn_invalid_or_incomplete_responses_into_empty_success():
    with pytest.raises(vk.VkScanError, match="нет каталога"):
        vk.search_block({}, -42)
    with pytest.raises(vk.VkScanError, match="неполную страницу"):
        vk.search_page({"videos": []}, _search_block(["-42_1"]))


def test_video_identity_includes_owner():
    videos = [_video(1, "A"), {**_video(1, "B"), "owner_id": -99}, _video(1, "A")]
    assert vk.dedupe_by_id(videos) == videos[:2]


@pytest.mark.asyncio
@pytest.mark.parametrize("recovers", [True, False])
async def test_fast_search_retries_missing_continuation_and_keeps_found_matches(vk_system, recovers, caplog):
    fake, probe = vk_system
    error = {"error": {"error_code": 104, "error_msg": "Not found"}}
    last = {"response": {"block": _search_block(["-42_2"]), "videos": [_video(2, "Тайтл 2")]}}
    fake.responses = {
        "catalog.getOwnerVideosSearch": [{"response": {
            "catalog": {"sections": [{"blocks": [_search_block(["-42_1"], "next")]}]},
            "videos": [_video(1, "Тайтл 1")]}}],
        "catalog.getBlockItems": [error, last if recovers else error],
    }
    request = probe.request("sources.vk.scan", {
        "channel_url": "https://vkvideo.ru/@chan", "query": "Тайтл",
        "search_mode": "search"}, timeout=10)
    if recovers:
        assert len((await request)["videos"]) == 2
    else:
        reply = await request
        assert len(reply["videos"]) == 1
        assert reply["complete"] is False
        assert "продолжение выдачи недоступно" in caplog.text
    calls = [c for c in fake.calls if c["method"] == "catalog.getBlockItems"]
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [5, 9, 104])
async def test_fast_search_initial_error_is_not_an_empty_result(vk_system, code):
    fake, probe = vk_system
    fake.responses["catalog.getOwnerVideosSearch"] = [{"error": {
        "error_code": code, "error_msg": "Search failed"}}]
    with pytest.raises(BusRequestError, match="Search failed"):
        await probe.request("sources.vk.scan", {
            "channel_url": "https://vkvideo.ru/@chan", "query": "Тайтл",
            "search_mode": "search"}, timeout=10)


@pytest.mark.asyncio
async def test_channel_listing_continues_through_empty_page_and_past_old_limit():
    sources = SourcesModule(Bus(), Database(":memory:"), vk_page_interval=0)
    offsets = []

    async def call(method, params):
        offset = params["offset"]
        offsets.append(offset)
        # More than the former 50-page limit, and a page with no accessible videos.
        items = [] if offset == vk.PAGE_SIZE else [_video(offset + 1, "Тайтл")]
        return {"response": {"count": 51 * vk.PAGE_SIZE + 1, "items": items}}

    sources._vk_call = call
    videos = await sources._vk_paginate("video.get", {})
    assert offsets == list(range(0, 52 * vk.PAGE_SIZE, vk.PAGE_SIZE))
    assert len(videos) == 51


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["limit", "repeat", "malformed"])
async def test_channel_listing_never_returns_success_after_truncation(failure):
    sources = SourcesModule(Bus(), Database(":memory:"), vk_page_interval=0)

    async def call(method, params):
        if failure == "malformed":
            return {"response": {"count": 600}}
        video_id = 1 if failure == "repeat" else params["offset"] + 1
        return {"response": {"count": 600, "items": [_video(video_id, "Тайтл")]}}

    sources._vk_call = call
    with pytest.raises(vk.VkScanError):
        await sources._vk_paginate("video.get", {}, max_pages=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("recovers", [True, False])
async def test_channel_listing_retries_server_error_on_same_page_with_a_limit(recovers):
    sources = SourcesModule(Bus(), Database(":memory:"), vk_page_interval=0)
    offsets = []

    async def call(method, params):
        offsets.append(params["offset"])
        if len(offsets) == 1 or not recovers:
            raise vk.VkScanError("Internal server error", error_code=10)
        return {"response": {"count": 1, "items": [_video(1, "Тайтл")]}}

    sources._vk_call = call
    if recovers:
        assert len(await sources._vk_paginate("video.get", {})) == 1
        assert offsets == [0, 0]
    else:
        with pytest.raises(vk.VkScanError, match="Internal server error"):
            await sources._vk_paginate("video.get", {})
        assert offsets == [0, 0, 0]
