"""The pages of the torrent trackers as sites (stage 15): the address of a page and its one spelling, the table of the page
made of the tracks `unpack` read (asked over the bus, read again when `unpack` says they changed), and the releases the page
lists, read from the tracker, each with its id on the page."""
import asyncio
import os

import pytest

from core import BaseModule, Bus
from core.contracts import TableGetRequest
from core.contracts.torrent import parse_link, player_of, track_link
from core.db import Database
from modules.sources import parsers
from modules.sources.module import SourcesModule
from modules.sources.releases import page_release
from modules.sources.sites.trackers import tracker_sites
from schema_db import migrated

FIX = os.path.join(os.path.dirname(__file__), "samples", "trackers")
PAGE = "https://kinozal.tv/details.php?id=2034617"


def site(name: str):
    return next(each for each in tracker_sites() if each.name == name)


@pytest.mark.parametrize("name, written, canonical", [
    ("kinozal", "https://kinozal.me/details.php?id=2034617&s=1", PAGE),
    ("kinozal", "http://kinozal.guru/details.php?id=2034617", PAGE),
    ("rutracker", "https://rutracker.org/forum/viewtopic.php?t=6543210", "https://rutracker.org/forum/viewtopic.php?t=6543210"),
    ("anilibria", "https://aniliberty.top/anime/releases/release/kusuriya/", "https://anilibria.top/anime/releases/release/kusuriya"),
    ("anilibria", "https://anilibria.top/release/kusuriya", "https://anilibria.top/anime/releases/release/kusuriya"),
    ("anilibria", "https://www.aniliberty.top/anime/releases/release/kusuriya/torrents",
     "https://anilibria.top/anime/releases/release/kusuriya"),
    ("anilibria_tv", "https://anilibria.tv/release/kusuriya.html", "https://anilibria.tv/release/kusuriya.html"),
    ("astar", "https://v13.astar.bz/anime/1234-kusuriya.html", "https://astar.bz/anime/1234-kusuriya.html"),
])
def test_a_page_of_a_tracker_has_one_spelling_on_the_trackers_own_domain(name, written, canonical):
    tracker = site(name)
    tracker.configure(tracker_mirrors={"kinozal": ["kinozal.me", "kinozal.guru"], "anilibria": ["aniliberty.top"],
                                       "astar": ["v13.astar.bz"]})
    assert tracker.match_url(written)
    assert tracker.canonical_url(written) == canonical
    assert tracker.canonical_url(canonical) == canonical
    assert tracker.source_type == "torrent"


def test_an_address_that_names_no_page_or_is_on_no_mirror_is_not_the_trackers():
    kinozal = site("kinozal")
    assert not kinozal.match_url("https://kinozal.tv/browse.php?s=x")
    assert not kinozal.match_url("https://kinozal.tv/details.php?id=abc")
    assert not kinozal.match_url("https://kinozal.new/details.php?id=1")  # a mirror the settings do not name
    kinozal.configure(tracker_mirrors={"kinozal": ["kinozal.new"]})
    assert kinozal.match_url("https://kinozal.new/details.php?id=1")


def test_a_track_link_names_the_video_file_and_the_stream_whatever_file_the_track_lies_in():
    inner = track_link("page", "Show/S01/E01 [x].mkv", 2)
    outer = track_link("page", "Show/S01/E01 [x].mkv", 0, file="Show/Sound/Студия #1/E01.mka")
    assert parse_link(inner) == ("page", "Show/S01/E01 [x].mkv", "Show/S01/E01 [x].mkv", 2)
    assert parse_link(outer) == ("page", "Show/S01/E01 [x].mkv", "Show/Sound/Студия #1/E01.mka", 0)
    assert parse_link("https://kodik.info/x") is None


@pytest.mark.parametrize("service, raw, release", [
    ("kinozal", {"link": "L", "date_marker": "05.05.2026 10:15:00"}, "page"),
    ("anilibria", {"magnet": "m", "quality": "1080p • HEVC", "episodes": "1-17", "hash": "ABC"}, "1080p • HEVC"),
    ("astar", {"link": "L", "episodes": "5", "quality": "one"}, "5 one"),
])
def test_the_id_of_a_release_on_its_page_is_what_stays_from_version_to_version(service, raw, release):
    got = page_release(service, raw)
    assert got.release == release
    assert page_release(service, {**raw, "link": "L2", "date_marker": "06.05.2026 10:00:00", "episodes": raw.get("episodes")}
                        ).release == release
    if service == "anilibria":
        assert got.infohash == "abc"


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


async def test_the_table_of_a_trackers_page_is_the_tracks_unpack_read_and_is_read_again_when_they_change(stand):
    sources, unpack, bus = stand
    unpack.rows = [track("Show/E01.mkv", 1, "Студия"), track("Show/E01.mkv", 2, "")]
    await unpack.start()
    resolved = await unpack.request("sources.site.resolve", {"url": "https://kinozal.me/details.php?id=2034617"})
    assert (resolved["site"], resolved["canonical_url"], resolved["source_type"]) == ("kinozal", PAGE, "torrent")

    ready = bus.subscribe("sources.table.ready")
    first = await unpack.request("sources.table.get", {"url": PAGE})
    assert first["state"] == "reading"
    assert (await asyncio.wait_for(ready.queue.get(), 5)).payload["url"] == PAGE
    table = (await unpack.request("sources.table.get", {"url": PAGE, "refresh": False}))["table"]
    assert [(row["key"], row["video"], row["voice"]["name"]) for row in table["rows"]] == [
        ("Show/E01.mkv#1", "page/Show/E01.mkv", "Студия"), ("Show/E01.mkv#2", "page/Show/E01.mkv", "")]

    unpack.rows = [track("Show/E01.mkv", 1, "Студия")]
    unpack.changed(PAGE)
    assert (await asyncio.wait_for(ready.queue.get(), 5)).payload["url"] == PAGE
    table = (await unpack.request("sources.table.get", {"url": PAGE, "refresh": False}))["table"]
    assert [row["key"] for row in table["rows"]] == ["Show/E01.mkv#1"]


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
