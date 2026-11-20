"""The site "channel of VK" read into the table of the page: the whole listing of the channel, then the query over the titles;
one listing serves the tables of a channel for its term. Through the bus the sessions of the API are played by a stand-in of
`trackerauth`."""
import asyncio
import json

import pytest

from core.bus import Bus
from core.module import BaseModule
from core.contracts import RowState
from core.db import Database
from core.reasons import parse, reason
from modules.sources.module import SourcesModule
from modules.sources.players.registry import PlayerRegistry
from modules.sources.sites import ReadContext, SiteError
from modules.sources.sites.vk_channel import VkChannelSite
from modules.sources.sites.vk_channel import site as channel_site
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

# --- one method: the whole listing, then the query over the titles -----------------------------------------------------------

async def test_a_video_the_listing_gives_without_files_is_asked_of_the_player_by_the_key_of_its_link():
    videos = [clip(3, "Противостояние 3 серия", files=False), clip(6, "Противостояние 6 серия", files=False)]

    def embed(request):
        return OPEN_EMBED if "id=3&" in str(request.url) else CLOSED_EMBED

    web = Web().route("video_ext.php", embed)
    draft = await Run(Api(videos), web).read(f"{CHANNEL}|противостояние")

    assert draft.rows[0].state is RowState.PLAYABLE and draft.rows[0].heights == (720, 1080)
    assert draft.rows[1].state is RowState.ABSENT and parse(draft.rows[1].reason)["code"] == "video_private"
    links = [url for _method, url, _body in web.asked if "video_ext.php" in url]
    assert links and all("hash=" in url and "api_hash" not in url for url in links)


# --- the listing of a channel never gives a part as the whole -----------------------------------------------------------------

async def test_a_count_that_moves_in_every_listing_ends_in_a_reason(monkeypatch):
    monkeypatch.setattr(channel_site, "LISTING_READS", 2)

    def answer(params, asked):
        return {"response": {"count": 150 + asked, "items": [clip(params["offset"] + 1, "Противостояние")]}}

    with pytest.raises(SiteError) as refused:
        await Run(Api(answer=answer)).read(f"{CHANNEL}|противостояние")

    assert parse(refused.value.reason) == {"code": "vk_listing_moving", "params": {"reads": 2}}


# --- one listing serves the tables of a channel for an hour ----------------------------------------------------------------------

class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


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


async def test_another_series_of_the_channel_takes_the_listing_and_the_reread_of_the_user_lists_it_again(stand):
    bus, trackerauth, probe, _sources = stand
    ready = bus.subscribe("sources.table.ready")
    listed = lambda: len([call for call in trackerauth.calls if call["method"] == "video.get"])  # noqa: E731

    await probe.request("sources.table.refresh", {"url": "https://vkvideo.ru/@chan|тайтл"}, timeout=5)
    await asyncio.wait_for(ready.queue.get(), 5)
    await probe.request("sources.table.refresh", {"url": "https://vkvideo.ru/@chan|трейлер"}, timeout=5)
    other = (await asyncio.wait_for(ready.queue.get(), 5)).payload
    assert other["url"] == "https://vkvideo.ru/@chan|трейлер" and listed() == 2

    probe.send_command("sources.table.read", {"url": "https://vkvideo.ru/@chan|трейлер"})
    await asyncio.wait_for(ready.queue.get(), 5)
    assert listed() == 4
