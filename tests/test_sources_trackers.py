"""The pages of the torrent trackers as sites (stage 15): the address of a page and its one spelling, the table of the page
made of the tracks `unpack` read (asked over the bus, read again when `unpack` says they changed), and the releases the page
lists, read from the tracker, each with its id on the page."""
import asyncio
import os

import pytest

from core.bus import Bus
from core.module import BaseModule
from core.contracts import TableGetRequest
from core.contracts.torrent import player_of, track_link
from core.db import Database
from modules.sources import parsers
from modules.sources.module import SourcesModule
from modules.sources.sites.trackers import tracker_sites
from schema_db import migrated

FIX = os.path.join(os.path.dirname(__file__), "samples", "trackers")
PAGE = "https://kinozal.tv/details.php?id=2034617"


def site(name: str):
    return next(each for each in tracker_sites() if each.name == name)


class Unpack(BaseModule):
    """The module that reads the releases, as `sources` meets it: the tracks of a page, and the word that they changed."""
    name = "unpack"

    def __init__(self, bus) -> None:
        super().__init__(bus)
        self.rows: list[dict] = []

    def register(self) -> None:
        self.handle("unpack.table.get", self.on_table)

    async def on_table(self, env):
        return {"names": ["Тайтл"], "rows": self.rows}

    def changed(self, url: str) -> None:
        self.publish_event("unpack.table.changed", {"url": url})


def track(path: str, stream: int, voice: str) -> dict:
    return {"key": f"{path}#{stream}", "player": player_of("page"), "text": f"S01E01 | {path}", "state": "playable",
            "voice": {"name": voice, "kind": "audio"}, "heights": [1080], "link": track_link("page", path, stream)}


@pytest.fixture
async def stand(tmp_path):
    bus = Bus()
    db = Database(str(migrated(tmp_path / "sources.db")))
    sources, unpack = SourcesModule(bus, db, torrent_cache_dir=str(tmp_path / "cache")), Unpack(bus)
    await sources.start()
    yield sources, unpack, bus
    await unpack.stop()
    await sources.stop()


async def test_a_trackers_page_without_unpack_running_fails_with_that_reason(stand):
    sources, unpack, bus = stand
    failed = bus.subscribe("sources.table.failed")
    await sources.tables.read(TableGetRequest(url=PAGE))
    assert "unpack_not_running" in (await asyncio.wait_for(failed.queue.get(), 5)).payload["reason"]


async def test_the_releases_of_a_page_are_read_from_the_tracker_with_their_ids(stand, monkeypatch):
    sources, unpack, bus = stand
    html = open(os.path.join(FIX, "kinozal_page.html"), encoding="utf-8").read()

    async def delivered(service, url, mirrors):
        return {**parsers.kinozal_parse(html, url), "url": url}

    monkeypatch.setattr(sources, "_parse_by_service", delivered)
    await unpack.start()
    page = await unpack.request("sources.releases.get", {"url": "https://kinozal.me/details.php?id=2034617"})
    assert (page["url"], page["site"], page["title"]) == (PAGE, "kinozal", "Тайтл / Title / 2026 / ПМ / WEB-DL (1080p)")
    assert [(each["release"], each["link"], each["published"]) for each in page["releases"]] == [
        ("page", "https://dl.kinozal.me/download.php?id=2034617", "05.05.2026 10:15:00")]


class Torrents(BaseModule):
    """The module that keeps the releases, as a tracker's page meets it: the check of the releases of a page."""
    name = "torrents"

    def __init__(self, bus) -> None:
        super().__init__(bus)
        self.checked: list[str] = []

    def register(self) -> None:
        self.handle("torrents.release.check", self.on_check)

    async def on_check(self, env):
        self.checked.append(env.payload["url"])
        return {"releases": []}
