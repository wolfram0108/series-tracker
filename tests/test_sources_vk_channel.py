"""The site "channel of VK" read into the table of the page: the whole listing of the channel, then the query over the titles;
one listing serves the tables of a channel for its term. Through the bus the sessions of the API are played by a stand-in of
`trackerauth`."""
import asyncio
import json
import re

import pytest

from core import BaseModule, Bus
from core.contracts import RowState
from core.db import Database
from core.reasons import parse, reason
from modules.sources.module import SourcesModule
from modules.sources.players.registry import PlayerRegistry
from modules.sources.sites import PHASE_VIDEOS, ReadContext, SiteError
from modules.sources.sites.vk_channel import VkChannelSite
from modules.sources.sites.vk_channel import site as channel_site
from modules.sources.sites.vk_channel.api import (PAGE_SIZE, dedupe, error_code_of, heights_of, is_external,
                                                   keep_by_terms, parse_address)
from schema_db import migrated
from sources_support import READ_LIMIT_S, Web, within

OWNER = -170515668
CHANNEL = "https://vkvideo.ru/@ruchime"
OPEN_EMBED = '<script>{"mp4_720":"a","mp4_1080":"b"}</script>'
CLOSED_EMBED = "<title>Video embed</title><div>Видео скрыто настройками приватности</div>"


def clip(number: int, title: str, *, files: bool = True, hashed: bool = True) -> dict:
    video = {"owner_id": OWNER, "id": number, "title": title, "date": 1750000000 + number, "duration": 1200}
    if files:
        video["files"] = {"mp4_720": "u", "mp4_1080": "u", "hls": "u"}
    if hashed:
        video["player"] = f"https://vk.com/video_ext.php?oid={OWNER}&id={number}&hash=h{number}"
    return video


def channel_of(count: int) -> list[dict]:
    """A channel of `count` videos, newest first: every third is an episode of the series looked for."""
    return [clip(number, f"Противостояние {number} серия" if number % 3 == 0 else f"Другое аниме {number}")
            for number in range(count, 0, -1)]


class Api:
    """The VK API as VK answers it: `video.get` gives the page of the channel at the offset, at most `PAGE_SIZE` of it, and the
    count; `answer` replaces that page when a test scripts it."""

    def __init__(self, videos: list[dict] | None = None, answer=None, error: SiteError | None = None) -> None:
        self.videos = channel_of(250) if videos is None else videos
        self.answer, self.error = answer, error
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, method: str, params: dict) -> dict:
        self.calls.append((method, dict(params)))
        if self.error is not None:
            raise self.error
        if method == "utils.resolveScreenName":
            return {"response": {"object_id": -OWNER, "type": "group"}}
        assert method == "video.get", f"the site asked {method}"
        if self.answer is not None:
            return self.answer(params, len(self.asked("video.get")))
        offset = params["offset"]
        return {"response": {"count": len(self.videos), "items": self.videos[offset:offset + min(params["count"], 100)]}}

    def pages(self) -> list[int]:
        return [params["offset"] for params in self.asked("video.get")]

    def asked(self, method: str) -> list[dict]:
        return [params for called, params in self.calls if called == method]


class Run:
    """Reads of one site, as the module keeps one: the listing of a channel is shared between them."""

    def __init__(self, api: Api, web: Web | None = None, clock=None) -> None:
        self.api, self.web, self.progress = api, web or Web(), []
        self.site = VkChannelSite(PlayerRegistry.standard(), self.api, **({"clock": clock} if clock else {}))

    async def read(self, address: str, *, fresh: bool = False, progress=None):
        report = progress if progress is not None else (lambda *step: self.progress.append(step))
        async with self.web.client() as http:
            return await within(self.site.read(address, ReadContext(http, report, fresh=fresh)),
                                READ_LIMIT_S, "the read of the channel")


@pytest.fixture(autouse=True)
def no_gap(monkeypatch):
    monkeypatch.setattr(channel_site, "VK_PAGE_INTERVAL_S", 0)


def episodes(videos: list[dict], term: str = "противостояние") -> list[str]:
    return [f"{OWNER}_{video['id']}" for video in videos if term in video["title"].lower()]


# --- the address of a channel ------------------------------------------------------------------------------------------------

def test_the_channel_and_the_query_are_one_address_that_reads_back_as_itself():
    site = VkChannelSite()
    canonical = site.canonical_url("https://vk.com/@ruchime/videos|Практикуя ци/ сто тысяч лет ")

    assert canonical == "https://vkvideo.ru/@ruchime|Практикуя ци/сто тысяч лет"
    assert site.canonical_url(canonical) == canonical
    assert parse_address(canonical).terms == ("Практикуя ци", "сто тысяч лет")


@pytest.mark.parametrize("url, found", [
    (CHANNEL, True), (f"{CHANNEL}|query", True), ("https://vk.com/@ruchime", True), ("https://vk.ru/@x/videos", True),
    ("https://vkvideo.ru/video-1_2", False), ("https://vk.com/club1", False), ("https://vkvideo.ru/@", False),
    (f"{CHANNEL}|x|search", False)])
def test_only_an_address_with_the_name_of_a_channel_and_at_most_a_query_is_a_channel(url, found):
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


def test_the_code_of_an_error_of_the_api_is_read_from_the_reason_of_the_call():
    assert error_code_of(SiteError(reason("vk_api_error", method="m", message="Not found", error_code=104))) == 104
    assert error_code_of(SiteError(reason("vk_call_failed", method="m", cause="timeout"))) is None


# --- one method: the whole listing, then the query over the titles -----------------------------------------------------------

async def test_a_channel_with_no_query_is_not_read_and_vk_is_not_asked():
    api = Api()

    for address in (CHANNEL, f"{CHANNEL}|", f"{CHANNEL}| / "):
        with pytest.raises(SiteError) as refused:
            await Run(api).read(address)
        assert parse(refused.value.reason)["code"] == "vk_query_missing"
    assert api.calls == []


async def test_the_listing_is_read_by_pages_of_a_hundred_to_the_count_and_the_titles_with_a_term_are_kept():
    api = Api()

    draft = await Run(api).read(f"{CHANNEL}|противостояние")

    assert PAGE_SIZE == 100
    assert api.pages() == [0, 100, 200]
    assert all(params == {"owner_id": OWNER, "offset": params["offset"], "count": 100} for params in api.asked("video.get"))
    assert [row.key for row in draft.rows] == episodes(api.videos) and len(draft.rows) == 83


async def test_each_term_of_the_query_keeps_its_titles_and_a_video_kept_twice_is_one_row():
    api = Api()

    draft = await Run(api).read(f"{CHANNEL}|противостояние/3 серия")

    assert len(draft.rows) == len({row.key for row in draft.rows}) == 83
    assert len(api.asked("video.get")) == 3  # the terms are looked for in one listing


async def test_a_row_is_the_title_the_steps_of_the_files_and_the_date_of_the_video():
    api = Api()
    draft = await Run(api).read(f"{CHANNEL}|противостояние")
    video = next(video for video in api.videos if "противостояние" in video["title"].lower())
    row = draft.rows[0]

    assert (row.text, row.player, row.player_label) == (video["title"], "vk", "VK")
    assert row.voice.name == "" and row.group == "" and draft.names == [] and draft.site_voice == ""
    assert draft.channel == "ruchime"  # the line of the constructor tells the channel apart from another of VK
    assert row.state is RowState.PLAYABLE and row.heights == [720, 1080]
    assert row.link == f"https://vk.com/video{OWNER}_{video['id']}" and row.duration_s == video["duration"]
    assert row.published.endswith("Z") and row.published[:4].isdigit()
    assert row.site_fields == {"owner_id": OWNER, "id": video["id"]}
    assert all(re.fullmatch(rf"https://vk\.com/video{OWNER}_\d+", each.link) for each in draft.rows)


async def test_the_read_reports_the_videos_of_the_channel_as_the_pages_come():
    run = Run(Api())

    await run.read(f"{CHANNEL}|противостояние")

    steps = [step for step in run.progress if step[0] == PHASE_VIDEOS]
    assert steps == [(PHASE_VIDEOS, 0, 0), (PHASE_VIDEOS, 100, 250), (PHASE_VIDEOS, 200, 250), (PHASE_VIDEOS, 250, 250)]


async def test_a_video_the_listing_gives_without_files_is_asked_of_the_player_by_the_key_of_its_link():
    videos = [clip(3, "Противостояние 3 серия", files=False), clip(6, "Противостояние 6 серия", files=False)]

    def embed(request):
        return OPEN_EMBED if "id=3&" in str(request.url) else CLOSED_EMBED

    web = Web().route("video_ext.php", embed)
    draft = await Run(Api(videos), web).read(f"{CHANNEL}|противостояние")

    assert draft.rows[0].state is RowState.PLAYABLE and draft.rows[0].heights == [720, 1080]
    assert draft.rows[1].state is RowState.ABSENT and parse(draft.rows[1].reason)["code"] == "video_private"
    links = [url for _method, url, _body in web.asked if "video_ext.php" in url]
    assert links and all("hash=" in url and "api_hash" not in url for url in links)


async def test_a_video_without_files_and_without_a_key_is_not_obtained_not_absent():
    draft = await Run(Api([clip(7, "Противостояние 7", files=False, hashed=False)])).read(f"{CHANNEL}|противостояние")

    assert draft.rows[0].state is RowState.NOT_OBTAINED and parse(draft.rows[0].reason)["code"] == "no_data"


async def test_a_video_that_vk_only_links_to_has_no_row():
    tube = {**clip(1, "Противостояние tube"), "platform": "YouTube", "files": {"external": "u"}}

    draft = await Run(Api([tube, clip(2, "Противостояние own")])).read(f"{CHANNEL}|противостояние")

    assert [row.text for row in draft.rows] == ["Противостояние own"]


# --- the listing of a channel never gives a part as the whole -----------------------------------------------------------------

async def test_the_listing_goes_on_through_an_empty_page_and_vk_hides_what_it_hides():
    def answer(params, _asked):
        items = [] if params["offset"] == 100 else [clip(params["offset"] + 1, "Противостояние")]
        return {"response": {"count": 301, "items": items}}

    api = Api(answer=answer)
    draft = await Run(api).read(f"{CHANNEL}|противостояние")

    assert api.pages() == [0, 100, 200, 300] and len(draft.rows) == 3


async def test_a_count_that_moved_during_the_listing_reads_it_again():
    def answer(params, asked):
        count = 201 if asked == 2 else 200  # an upload during the first listing
        return {"response": {"count": count, "items": [clip(params["offset"] + 1, "Противостояние")]}}

    api = Api(answer=answer)
    draft = await Run(api).read(f"{CHANNEL}|противостояние")

    assert api.pages() == [0, 100, 200, 0, 100] and len(draft.rows) == 2


async def test_a_count_that_moves_in_every_listing_ends_in_a_reason(monkeypatch):
    monkeypatch.setattr(channel_site, "LISTING_READS", 2)

    def answer(params, asked):
        return {"response": {"count": 150 + asked, "items": [clip(params["offset"] + 1, "Противостояние")]}}

    with pytest.raises(SiteError) as refused:
        await Run(Api(answer=answer)).read(f"{CHANNEL}|противостояние")

    assert parse(refused.value.reason) == {"code": "vk_listing_moving", "params": {"reads": 2}}


@pytest.mark.parametrize("failure", ["limit", "repeat", "malformed", "oversized", "more_than_count"])
async def test_a_listing_that_cannot_be_the_whole_ends_in_a_reason_and_not_in_a_part(monkeypatch, failure):
    monkeypatch.setattr(channel_site, "LISTING_PAGES", 2)

    def answer(params, _asked):
        if failure == "malformed":
            return {"response": {"count": 600}}
        if failure == "oversized":
            return {"response": {"count": 600, "items": [clip(n, "x") for n in range(101)]}}
        if failure == "more_than_count":
            return {"response": {"count": 1, "items": [clip(1, "x"), clip(2, "x")]}}
        first = 1 if failure == "repeat" else params["offset"] + 1
        return {"response": {"count": 600, "items": [clip(first, "x")]}}

    with pytest.raises(SiteError) as refused:
        await Run(Api(answer=answer)).read(f"{CHANNEL}|x")

    assert parse(refused.value.reason)["code"] == ("vk_listing_limit" if failure == "limit" else "vk_reply_unexpected")


@pytest.mark.parametrize("recovers", [True, False])
async def test_a_page_the_api_fails_is_asked_again_up_to_three_asks(recovers):
    failure = SiteError(reason("vk_api_error", method="video.get", message="Internal server error", error_code=10))

    def answer(params, asked):
        if asked == 1 or not recovers:
            raise failure
        return {"response": {"count": 1, "items": [clip(1, "Противостояние")]}}

    api = Api(answer=answer)
    if recovers:
        assert len((await Run(api).read(f"{CHANNEL}|противостояние")).rows) == 1
        assert api.pages() == [0, 0]
    else:
        with pytest.raises(SiteError) as refused:
            await Run(api).read(f"{CHANNEL}|противостояние")
        assert refused.value is failure and api.pages() == [0, 0, 0]


async def test_a_page_the_api_refuses_for_a_reason_that_a_repeat_does_not_mend_is_asked_once():
    refused = SiteError(reason("vk_api_error", method="video.get", message="Access denied", error_code=15))

    def answer(params, _asked):
        raise refused

    api = Api(answer=answer)
    with pytest.raises(SiteError):
        await Run(api).read(f"{CHANNEL}|противостояние")

    assert api.pages() == [0]


# --- one listing serves the tables of a channel for an hour ----------------------------------------------------------------------

class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_the_tables_of_one_channel_share_its_listing_within_the_hour_and_list_it_anew_after():
    api, clock = Api(), Clock()
    run = Run(api, clock=clock)

    first = await run.read(f"{CHANNEL}|противостояние")
    clock.now += channel_site.LISTING_TERM_S - 1
    other = await run.read(f"{CHANNEL}|другое аниме")
    assert len(api.asked("video.get")) == 3 and len(api.asked("utils.resolveScreenName")) == 1
    assert len(first.rows) == 83 and len(other.rows) == 167

    clock.now += 1
    await run.read(f"{CHANNEL}|противостояние")
    assert len(api.asked("video.get")) == 6


async def test_a_read_the_user_asked_for_lists_the_channel_anew_within_the_hour():
    api = Api()
    run = Run(api)

    await run.read(f"{CHANNEL}|противостояние")
    await run.read(f"{CHANNEL}|другое аниме", fresh=True)

    assert len(api.asked("video.get")) == 6


async def test_reads_of_one_channel_at_the_same_time_wait_for_one_listing_and_are_each_told_its_progress():
    gate, listing, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    api = Api()
    plain = api.__call__

    async def held(method, params):
        if method == "video.get" and params["offset"] == 0:
            listing.set()
            await gate.wait()
        return await plain(method, params)

    run = Run(api)
    run.site._call = held
    told: dict[str, list] = {"a": [], "b": []}

    def second_told(*step):
        told["b"].append(step)
        waiting.set()  # the read joins the listing in the same step, before it gives the loop back

    first = asyncio.create_task(run.read(f"{CHANNEL}|противостояние", progress=lambda *step: told["a"].append(step)))
    await listing.wait()
    second = asyncio.create_task(run.read(f"{CHANNEL}|другое аниме", progress=second_told))
    await waiting.wait()
    gate.set()
    a, b = await asyncio.gather(first, second)

    assert len(api.asked("video.get")) == 3 and len(a.rows) == 83 and len(b.rows) == 167
    assert told["a"][-1] == told["b"][-1] == (PHASE_VIDEOS, 250, 250)


async def test_a_listing_that_failed_is_not_kept_and_the_next_read_asks_vk_again():
    failure = SiteError(reason("vk_api_error", method="video.get", message="Access denied", error_code=15))
    calls = {"n": 0}

    def answer(params, _asked):
        calls["n"] += 1
        if calls["n"] == 1:
            raise failure
        return {"response": {"count": 1, "items": [clip(1, "Противостояние")]}}

    run = Run(Api(answer=answer))
    with pytest.raises(SiteError):
        await run.read(f"{CHANNEL}|противостояние")

    assert len((await run.read(f"{CHANNEL}|противостояние")).rows) == 1


# --- a channel the read cannot give -------------------------------------------------------------------------------------------

async def test_a_channel_vk_does_not_know_is_refused_with_a_reason():
    class Unknown(Api):
        async def __call__(self, method, params):
            return {"response": []} if method == "utils.resolveScreenName" else await super().__call__(method, params)

    with pytest.raises(SiteError) as refused:
        await Run(Unknown()).read(f"{CHANNEL}|x")

    assert parse(refused.value.reason) == {"code": "vk_channel_unknown", "params": {"name": "ruchime"}}


async def test_an_error_of_the_api_ends_the_read_with_its_reason():
    failure = SiteError(reason("vk_api_error", method="utils.resolveScreenName", message="User authorization failed", error_code=5))

    with pytest.raises(SiteError) as refused:
        await Run(Api(error=failure)).read(f"{CHANNEL}|x")

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
    monkeypatch.setattr(channel_site, "PAGE_SIZE", 2)
    path = migrated(tmp_path / "sources.db")
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
    request = {"url": "https://vkvideo.ru/@chan|тайтл"}

    first = await probe.request("sources.table.get", request, timeout=5)
    done = (await asyncio.wait_for(ready.queue.get(), 5)).payload
    again = await probe.request("sources.table.get", request, timeout=5)

    assert first["state"] == "reading" and first["url"] == "https://vkvideo.ru/@chan|тайтл"
    assert done["url"] == first["url"]
    assert again["state"] == "ready" and [row["text"] for row in again["table"]["rows"]] == [
        "Тайтл 1 серия", "Тайтл 2 серия"]
    assert [call["params"]["offset"] for call in trackerauth.calls if call["method"] == "video.get"] == [0, 2]


async def test_another_series_of_the_channel_takes_the_listing_and_the_reread_of_the_user_lists_it_again(stand):
    bus, trackerauth, probe, _sources = stand
    ready = bus.subscribe("sources.table.ready")
    listed = lambda: len([call for call in trackerauth.calls if call["method"] == "video.get"])  # noqa: E731

    await probe.request("sources.table.get", {"url": "https://vkvideo.ru/@chan|тайтл"}, timeout=5)
    await asyncio.wait_for(ready.queue.get(), 5)
    await probe.request("sources.table.get", {"url": "https://vkvideo.ru/@chan|трейлер"}, timeout=5)
    other = (await asyncio.wait_for(ready.queue.get(), 5)).payload
    assert other["url"] == "https://vkvideo.ru/@chan|трейлер" and listed() == 2

    probe.send_command("sources.table.read", {"url": "https://vkvideo.ru/@chan|трейлер"})
    await asyncio.wait_for(ready.queue.get(), 5)
    assert listed() == 4


async def test_an_error_of_the_api_reaches_the_consumers_as_the_failure_of_the_read(stand):
    bus, trackerauth, probe, _sources = stand
    failed = bus.subscribe("sources.table.failed")
    trackerauth.error = True

    await probe.request("sources.table.get", {"url": "https://vkvideo.ru/@chan|x"}, timeout=5)
    event = (await asyncio.wait_for(failed.queue.get(), 5)).payload

    cause = parse(event["reason"])
    assert event["url"] == "https://vkvideo.ru/@chan|x" and cause["code"] == "vk_api_error"
    assert cause["params"]["message"] == "User authorization failed" and cause["params"]["error_code"] == 5


async def test_the_module_tells_the_site_and_the_canonical_address_of_a_channel_and_of_a_release(stand):
    _bus, _trackerauth, probe, _sources = stand

    channel = await probe.request("sources.site.resolve", {"url": "https://vk.com/@chan|q"}, timeout=5)
    bare = await probe.request("sources.site.resolve", {"url": "https://vk.com/@chan"}, timeout=5)
    release = await probe.request("sources.site.resolve", {"url": "https://1.ruchime.org/release/x/episode-3?off=vk"},
                                  timeout=5)

    assert channel == {"site": "vk_channel", "canonical_url": "https://vkvideo.ru/@chan|q", "source_type": "vk_video"}
    assert bare == {"site": "vk_channel", "canonical_url": "https://vkvideo.ru/@chan|", "source_type": "vk_video"}
    assert release == {"site": "ruchime", "canonical_url": "https://1.ruchime.org/release/x",
                       "source_type": "web_video"}


def test_the_api_of_vk_is_not_asked_with_the_user_agent_of_a_browser():
    """On the agent of a browser the API cuts the field `files` (the steps of the video) from its answers (finding 44)."""
    from modules.trackerauth.providers import UA, VkProvider
    headers = VkProvider().request_headers("https://api.vk.com/method/video.get")

    assert headers.get("User-Agent") != UA and "User-Agent" not in headers
