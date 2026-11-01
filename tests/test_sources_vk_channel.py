"""The site "channel of VK" read into the table of the page (M-9), on the snapshots of the VK API in `raw/run1`, `raw/run2_vk`,
and through the bus with the sessions of the API played by a stand-in of `trackerauth`."""
import asyncio
import json
import math
import re

import pytest

from core import BaseModule, Bus
from core.contracts import RowState
from core.db import Database
from core.reasons import parse, reason
from modules.sources.module import SourcesModule
from modules.sources.players.registry import PlayerRegistry
from modules.sources.sites import PHASE_PLAYERS, PHASE_VIDEOS, ReadContext, SiteError
from modules.sources.sites.vk_channel import VkChannelSite
from modules.sources.sites.vk_channel.api import PAGE_SIZE, dedupe, heights_of, is_external, keep_by_terms, parse_address
from sources_support import READ_LIMIT_S, Web, json_of, migrate, text_of, within

OWNER = -170515668
CHANNEL = "https://vkvideo.ru/@ruchime"
SEARCH_BY_OWNER = "run1/vk__52__video.search.json"
SEARCH = "run2_vk/vk__52__video.search_noowner.json"
LISTING = "run2_vk/vk__52__video.get.json"
EMBED_CLOSED = "run1/ruchime__episode-390__vk_embed.html"
OPEN_EMBED = '<script>{"mp4_720":"a","mp4_1080":"b"}</script>'


class Api:
    """The VK API as the snapshots show it: `video.search` with an owner finds nothing, without one it finds the videos of
    every channel with their files, `video.get` lists one channel and gives no files. Only the first page is the snapshot."""

    def __init__(self, error: SiteError | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.error = error

    async def __call__(self, method: str, params: dict) -> dict:
        self.calls.append((method, dict(params)))
        if self.error is not None:
            raise self.error
        if method == "utils.resolveScreenName":
            return json_of("run1/vk__52__resolve.json")
        if method == "video.search" and "owner_id" in params:
            return json_of(SEARCH_BY_OWNER)
        name = SEARCH if method == "video.search" else LISTING
        first = json_of(name)
        if params["offset"] == 0:
            return first
        return {"response": {"count": first["response"]["count"], "items": []}}

    def pages(self, method: str) -> list[int]:
        return [params["offset"] for called, params in self.calls if called == method]


class Run:
    def __init__(self, api: Api, web: Web | None = None) -> None:
        self.api, self.web, self.progress = api, web or Web(), []

    async def read(self, address: str):
        site = VkChannelSite(PlayerRegistry.standard(), self.api)
        async with self.web.client() as http:
            return await within(site.read(address, ReadContext(http, lambda *step: self.progress.append(step))),
                                READ_LIMIT_S, "the read of the channel")


# --- the address of a channel ------------------------------------------------------------------------------------------------

def test_the_channel_the_query_and_the_mode_are_one_address_that_reads_back_as_itself():
    site = VkChannelSite()
    canonical = site.canonical_url("https://vk.com/@ruchime/videos|Практикуя ци/ сто тысяч лет |get_all")

    assert canonical == "https://vkvideo.ru/@ruchime|Практикуя ци/сто тысяч лет|get_all"
    assert site.canonical_url(canonical) == canonical
    assert parse_address(canonical, {}).terms == ("Практикуя ци", "сто тысяч лет")


def test_the_mode_is_the_choice_of_the_source_then_the_one_in_the_address_then_search():
    site = VkChannelSite()

    assert site.canonical_url(f"{CHANNEL}|x") == f"{CHANNEL}|x|search"
    assert site.canonical_url(f"{CHANNEL}|x|get_all") == f"{CHANNEL}|x|get_all"
    assert site.canonical_url(f"{CHANNEL}|x|get_all", {"vk_search_mode": "search"}) == f"{CHANNEL}|x|search"
    assert site.canonical_url(f"{CHANNEL}|x|nonsense", {"vk_search_mode": "also nonsense"}) == f"{CHANNEL}|x|search"


@pytest.mark.parametrize("url, found", [
    (CHANNEL, True), (f"{CHANNEL}|query", True), ("https://vk.com/@ruchime", True), ("https://vk.ru/@x/videos", True),
    ("https://vkvideo.ru/video-1_2", False), ("https://vk.com/club1", False), ("https://vkvideo.ru/@", False)])
def test_only_an_address_with_the_name_of_a_channel_is_a_channel(url, found):
    assert VkChannelSite().match_url(url) is found


def test_the_pure_reading_of_a_video_of_the_api():
    assert heights_of({"files": {"mp4_1080": "u", "mp4_144": "u", "hls": "u"}}) == [144, 1080]
    assert heights_of({"files": {"hls": "u"}}) == []
    assert heights_of({}) is None and heights_of({"files": {}}) is None
    assert is_external({"platform": "YouTube"}) and is_external({"files": {"external": "u"}})
    assert not is_external({"files": {"mp4_720": "u"}})
    videos = [{"owner_id": 1, "id": 1, "title": "Магическая битва 1"}, {"owner_id": 1, "id": 1, "title": "same"},
              {"owner_id": 1, "id": 2, "title": "Другое аниме"}]
    assert len(dedupe(videos)) == 2
    assert keep_by_terms(dedupe(videos), ("магическая",)) == [videos[0]]
    assert keep_by_terms(dedupe(videos), ()) == dedupe(videos)


# --- the mode "search" (the snapshot of the search without an owner) -----------------------------------------------------

async def test_the_search_asks_vk_without_the_owner_and_keeps_the_videos_of_the_channel_with_their_files():
    api, listed = Api(), json_of(SEARCH)["response"]["items"]
    ours = [video for video in listed if video["owner_id"] == OWNER]

    draft = await Run(api).read(f"{CHANNEL}|Непревзойденный на пути алхимии|search")

    assert len(ours) == 9 and 0 < len(ours) < len(listed)
    assert [row.key for row in draft.rows] == [f"{OWNER}_{video['id']}" for video in ours]
    # No call carries `owner_id` to the search, and the pages go on to the count VK reports.
    searches = [params for called, params in api.calls if called == "video.search"]
    assert all("owner_id" not in params and params["q"] == "Непревзойденный на пути алхимии" for params in searches)
    assert api.pages("video.search") == [PAGE_SIZE * page for page in range(math.ceil(27720 / PAGE_SIZE))]


async def test_a_row_of_the_search_is_the_title_the_steps_of_the_files_and_the_date_of_the_video():
    draft = await Run(Api()).read(f"{CHANNEL}|Непревзойденный на пути алхимии|search")
    video = next(video for video in json_of(SEARCH)["response"]["items"] if video["owner_id"] == OWNER)
    row = draft.rows[0]

    assert (row.text, row.player, row.player_label) == (video["title"], "vk", "VK")
    assert row.voice.name == "" and row.group == "" and draft.names == [] and draft.site_voice == ""
    assert row.state is RowState.PLAYABLE and row.heights == sorted(
        int(name[4:]) for name in video["files"] if name.startswith("mp4_"))
    assert row.link == f"https://vk.com/video{OWNER}_{video['id']}" and row.duration_s == video["duration"]
    assert row.published.endswith("Z") and row.published[:4].isdigit()
    assert row.site_fields == {"owner_id": OWNER, "id": video["id"]}


async def test_a_link_is_the_page_of_the_video_and_the_same_in_every_read_of_the_same_answers():
    first = await Run(Api()).read(f"{CHANNEL}|Непревзойденный на пути алхимии|search")
    second = await Run(Api()).read(f"{CHANNEL}|Непревзойденный на пути алхимии|search")

    assert [row.link for row in first.rows] == [row.link for row in second.rows]
    assert all(re.fullmatch(rf"https://vk\.com/video{OWNER}_\d+", row.link) for row in first.rows)


async def test_each_term_of_the_query_is_searched_and_a_video_found_twice_is_one_row():
    api = Api()

    draft = await Run(api).read(f"{CHANNEL}|Непревзойденный на пути алхимии/алхимии|search")

    assert len(draft.rows) == len({row.key for row in draft.rows}) == 9
    assert {params["q"] for called, params in api.calls if called == "video.search"} == {
        "Непревзойденный на пути алхимии", "алхимии"}


async def test_the_read_reports_the_pages_of_the_listing_to_the_count():
    run = Run(Api())

    await run.read(f"{CHANNEL}|Непревзойденный на пути алхимии|search")

    assert run.progress[0] == (PHASE_VIDEOS, 0, 0) and run.progress[-1] == (PHASE_VIDEOS, 27720, 27720)


async def test_a_search_without_terms_finds_nothing_and_asks_nothing():
    api = Api()

    draft = await Run(api).read(f"{CHANNEL}||search")

    assert draft.rows == [] and [called for called, _params in api.calls] == ["utils.resolveScreenName"]


# --- the mode "get_all" (the snapshot of the listing of the channel, which gives no files) ---------------------------------

async def test_the_listing_of_a_channel_is_read_to_its_count_and_the_titles_with_a_term_are_kept():
    api = Api()
    web = Web().route("video_ext.php", text_of(EMBED_CLOSED))
    listed = json_of(LISTING)["response"]["items"]
    kept = [video for video in listed if "противостояние" in video["title"].lower()]

    draft = await Run(api, web).read(f"{CHANNEL}|противостояние|get_all")

    assert 0 < len(kept) < len(listed)
    assert [row.key for row in draft.rows] == [f"{OWNER}_{video['id']}" for video in kept]
    assert api.pages("video.get") == [PAGE_SIZE * page for page in range(math.ceil(12025 / PAGE_SIZE))]
    assert all("owner_id" in params for called, params in api.calls if called == "video.get")


async def test_a_video_the_listing_gives_without_files_is_asked_of_the_player_by_the_key_of_its_link():
    listed = json_of(LISTING)["response"]["items"]
    kept = [video for video in listed if "противостояние" in video["title"].lower()]
    open_one = kept[0]["id"]
    assert len(kept) >= 2

    def embed(request):
        return OPEN_EMBED if f"id={open_one}&" in str(request.url) else text_of(EMBED_CLOSED)

    web = Web().route("video_ext.php", embed)
    draft = await Run(Api(), web).read(f"{CHANNEL}|противостояние|get_all")

    assert draft.rows[0].state is RowState.PLAYABLE and draft.rows[0].heights == [720, 1080]
    assert {row.state for row in draft.rows[1:]} == {RowState.ABSENT}
    assert {parse(row.reason)["code"] for row in draft.rows[1:]} == {"video_private"}
    # The key (`hash`) of the link of the video is what the player is asked with.
    links = [url for _method, url, _body in web.asked if "video_ext.php" in url]
    assert links and all("hash=" in url and "api_hash" not in url for url in links)


async def test_a_listing_with_no_term_keeps_every_video_of_the_channel():
    web = Web().route("video_ext.php", text_of(EMBED_CLOSED))
    draft = await Run(Api(), web).read(f"{CHANNEL}||get_all")

    assert len(draft.rows) == len(json_of(LISTING)["response"]["items"])


async def test_a_video_without_files_and_without_a_key_is_not_obtained_not_absent():
    class Bare(Api):
        async def __call__(self, method, params):
            if method == "video.get":
                return {"response": {"count": 1, "items": [{"owner_id": OWNER, "id": 7, "title": "Серия", "date": 1}]}}
            return await super().__call__(method, params)

    draft = await Run(Bare()).read(f"{CHANNEL}||get_all")

    assert draft.rows[0].state is RowState.NOT_OBTAINED and parse(draft.rows[0].reason)["code"] == "no_data"


async def test_a_video_that_vk_only_links_to_has_no_row():
    class Linked(Api):
        async def __call__(self, method, params):
            if method == "video.search" and params["offset"] == 0:
                return {"response": {"count": 2, "items": [
                    {"owner_id": OWNER, "id": 1, "title": "tube", "platform": "YouTube", "files": {"external": "u"}},
                    {"owner_id": OWNER, "id": 2, "title": "own", "files": {"mp4_720": "u"}}]}}
            return await super().__call__(method, params)

    draft = await Run(Linked()).read(f"{CHANNEL}|x|search")

    assert [row.text for row in draft.rows] == ["own"]


# --- a channel the read cannot give -------------------------------------------------------------------------------------------

async def test_a_channel_vk_does_not_know_is_refused_with_a_reason():
    class Unknown(Api):
        async def __call__(self, method, params):
            return {"response": []} if method == "utils.resolveScreenName" else await super().__call__(method, params)

    with pytest.raises(SiteError) as refused:
        await Run(Unknown()).read(f"{CHANNEL}|x|search")

    assert parse(refused.value.reason) == {"code": "vk_channel_unknown", "params": {"name": "ruchime"}}


async def test_an_error_of_the_api_ends_the_read_with_its_reason():
    failure = SiteError(reason("vk_api_error", method="video.search", message="User authorization failed", error_code=5))

    with pytest.raises(SiteError) as refused:
        await Run(Api(error=failure)).read(f"{CHANNEL}|x|search")

    assert refused.value is failure


async def test_an_address_without_a_channel_is_refused():
    with pytest.raises(SiteError) as refused:
        await Run(Api()).read("https://vkvideo.ru/video-1_2")

    assert parse(refused.value.reason)["code"] == "vk_bad_address"


# --- through the bus: the sessions of the API are the module's --------------------------------------------------------------

class FakeTrackerauth(BaseModule):
    """Stands in for trackerauth: answers the calls of the VK API with pages."""
    name = "trackerauth"

    def __init__(self, bus, pages: dict, error: bool = False):
        self._pages, self.error, self.calls = pages, error, []
        super().__init__(bus)

    def register(self):
        self.handle("trackerauth.fetch", self.on_fetch)

    async def on_fetch(self, env):
        payload = env.payload
        if self.error:
            body = {"error": {"error_code": 5, "error_msg": "User authorization failed"}}
        else:
            method = payload["url"].rsplit("/", 1)[-1]
            self.calls.append({"method": method, "params": payload.get("params")})
            if method == "utils.resolveScreenName":
                body = {"response": {"object_id": 42, "type": "group"}}
            else:
                page = int(payload["params"].get("offset", 0)) // int(payload["params"]["count"])
                pages = self._pages.get(method, [])
                body = {"response": {"count": sum(len(items) for items in pages),
                                     "items": pages[page] if page < len(pages) else []}}
        return {"status": 200, "text": json.dumps(body), "final_url": payload["url"], "content_type": "application/json"}


def video(number: int, title: str) -> dict:
    return {"id": number, "owner_id": -42, "title": title, "date": 1750000000, "files": {"mp4_720": "u"}}


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
async def stand(tmp_path, monkeypatch):
    # Two videos to a page, so that a listing of three has two pages.
    monkeypatch.setattr("modules.sources.sites.vk_channel.site.PAGE_SIZE", 2)
    path = tmp_path / "sources.db"
    migrate(path)
    bus = Bus()
    pages = {"video.get": [[video(1, "Тайтл 1 серия"), video(2, "Трейлер другого")], [video(3, "Тайтл 2 серия")]]}
    trackerauth, probe = FakeTrackerauth(bus, pages), Probe(bus)
    sources = SourcesModule(bus, Database(str(path)), torrent_cache_dir=str(tmp_path / "cache"))
    modules = [trackerauth, sources, probe]
    for module in modules:
        await module.start()
    yield bus, trackerauth, probe, sources
    for module in reversed(modules):
        await module.stop()


async def test_the_module_reads_a_channel_of_vk_through_the_session_of_the_api(stand):
    bus, trackerauth, probe, _sources = stand
    ready = bus.subscribe("sources.table.ready")
    request = {"url": "https://vkvideo.ru/@chan|тайтл", "options": {"vk_search_mode": "get_all"}}

    first = await probe.request("sources.table.get", request, timeout=5)
    done = (await asyncio.wait_for(ready.queue.get(), 5)).payload
    again = await probe.request("sources.table.get", request, timeout=5)

    assert first["state"] == "reading" and first["url"] == "https://vkvideo.ru/@chan|тайтл|get_all"
    assert done["url"] == first["url"]
    assert again["state"] == "ready" and [row["text"] for row in again["table"]["rows"]] == [
        "Тайтл 1 серия", "Тайтл 2 серия"]
    assert [call["params"]["offset"] for call in trackerauth.calls if call["method"] == "video.get"] == [0, 2]


async def test_an_error_of_the_api_reaches_the_consumers_as_the_failure_of_the_read(stand):
    bus, trackerauth, probe, _sources = stand
    failed = bus.subscribe("sources.table.failed")
    trackerauth.error = True

    await probe.request("sources.table.get", {"url": "https://vkvideo.ru/@chan|x"}, timeout=5)
    event = (await asyncio.wait_for(failed.queue.get(), 5)).payload

    cause = parse(event["reason"])
    assert event["url"] == "https://vkvideo.ru/@chan|x|search" and cause["code"] == "vk_api_error"
    assert cause["params"]["message"] == "User authorization failed" and cause["params"]["error_code"] == 5


async def test_the_module_tells_the_site_and_the_canonical_address_of_a_channel_and_of_a_release(stand):
    _bus, _trackerauth, probe, _sources = stand

    channel = await probe.request("sources.site.resolve", {"url": "https://vk.com/@chan|q", "options": {
        "vk_search_mode": "get_all"}}, timeout=5)
    release = await probe.request("sources.site.resolve", {"url": "https://1.ruchime.org/release/x/episode-3?off=vk"},
                                  timeout=5)

    assert channel == {"site": "vk_channel", "canonical_url": "https://vkvideo.ru/@chan|q|get_all"}
    assert release == {"site": "ruchime", "canonical_url": "https://1.ruchime.org/release/x"}


def test_the_api_of_vk_is_not_asked_with_the_user_agent_of_a_browser():
    """On the agent of a browser the API cuts the field `files` (the steps of the video) from its answers (finding 44)."""
    from modules.trackerauth.providers import UA, VkProvider
    headers = VkProvider().request_headers("https://api.vk.com/method/video.get")

    assert headers.get("User-Agent") != UA and "User-Agent" not in headers
