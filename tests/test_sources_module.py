"""The module `sources` on the bus: the queries about the sites it reads, the stream of a candidate, and the class guards of its code (phases worded, no blocking disk call in async code)."""
import asyncio

import pytest

from core.bus import Bus
from core.module import BaseModule
from core.contracts import ReleaseTable, RowState, TableRow
from core.db import Database
from provider_mock import MockProvider, serving
from modules.sources.module import SourcesModule
from modules.sources.provider import ProviderClient
from schema_db import migrated
from sources_support import OWNED, STEP_LIMIT_S, host_that

DOLLS = "https://dunhuatv.ru/28-praktikuja-ci-sto-tysjach-let-smotret-onlajn.html"


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
async def stand(tmp_path):
    path = migrated(tmp_path / "sources.db")
    bus = Bus()
    sources, probe = SourcesModule(bus, Database(str(path)), torrent_cache_dir=str(tmp_path / "cache")), Probe(bus)
    await sources.start()
    await probe.start()
    yield sources, probe
    await probe.stop()
    await sources.stop()


# --- the sites a module reads when it is given no others ---------------------------------------------------------------------

# --- where each player of the order of sources is ----------------------------------------------------------------------------

def page(url: str, site: str, *rows: tuple[str, str, list[int]]) -> ReleaseTable:
    return ReleaseTable(url=url, site=site, names=["Название"], read_at="2026-11-12T10:00:00Z", complete=True,
                        rows=[TableRow(key=key, player=player, text=key, heights=heights,
                                       state=RowState.PLAYABLE if heights else RowState.ABSENT)
                              for key, player, heights in rows])


@pytest.fixture
async def with_lines(tmp_path):
    """A module with a provider of the socket whose one application has two lines: they are the players its manifest names."""
    mock = MockProvider()
    mock.add_site("banri", "半日 (Banri)")
    mock.title("banri", 7, ["万界独尊"], [("1", "NBY", "n1", [1080]), ("2", "CO4K", "c1", [720])])
    async with serving(mock) as server:
        bus = Bus()
        sources = SourcesModule(bus, Database(str(migrated(tmp_path / "sources.db"))), torrent_cache_dir=str(tmp_path / "cache"),
                                provider=ProviderClient(server.url), provider_reconnect=(0.05, 0.2))
        probe, states = Probe(bus), bus.subscribe("sources.provider.state")
        await sources.start()
        await probe.start()
        while (await asyncio.wait_for(states.queue.get(), STEP_LIMIT_S)).payload["state"] != "up":
            pass
        yield sources, probe
        await probe.stop()
        await sources.stop()


async def test_every_player_says_where_it_is_among_the_pages_asked_about_and_the_best_height_of_its_rows(with_lines):
    sources, probe = with_lines
    ruchime = "https://ruchime.ru/watch/1"
    await sources.repo.save_table(page(DOLLS, "dunhuatv", ("k1", "kodik", [480, 720]), ("c1", "cvh", [1080]),
                                       ("c2", "cvh", [])))
    await sources.repo.save_table(page(ruchime, "ruchime", ("k1", "kodik", [720])))
    await sources.repo.save_table(page("app://banri/7", "banri", ("n1", "line:NBY", [1080]), ("n2", "line:NBY", [2160])))
    await sources.repo.save_table(page("https://dunhuatv.ru/other.html", "dunhuatv", ("k1", "kodik", [2160])))

    asked = await probe.request("sources.players.list", {"urls": [DOLLS, ruchime, "app://banri/7"]}, timeout=5)
    facts = {each["name"]: each for each in asked["players"]}
    # A page not asked about says nothing, whatever it holds.
    assert facts["kodik"]["kind"] == "site" and facts["kodik"]["height"] == 720
    assert [(each["label"], each["sources"]) for each in facts["kodik"]["places"]] == [("dunhuatv", 1), ("ruchime", 1)]
    assert facts["cvh"]["height"] == 1080 and [each["name"] for each in facts["cvh"]["places"]] == ["dunhuatv"]
    assert facts["alloha"]["places"] == [] and facts["alloha"]["height"] is None
    # A line is in every application that admits it, with none of the pages when no page asked about is its.
    assert facts["line:NBY"]["kind"] == "line" and facts["line:NBY"]["height"] == 2160
    assert facts["line:NBY"]["places"] == [{"name": "banri", "label": "半日 (Banri)", "sources": 1}]
    assert facts["line:CO4K"]["places"] == [{"name": "banri", "label": "半日 (Banri)", "sources": 0}]

    bare = {each["name"]: each for each in (await probe.request("sources.players.list", {}, timeout=5))["players"]}
    assert set(bare) == set(facts) and bare["kodik"]["places"] == [] and bare["line:NBY"]["places"][0]["sources"] == 0


# --- the stream of a candidate ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["vk", "allvideo", "vibix", "cvh", "kodik"])
async def test_a_link_a_player_owns_that_it_cannot_resolve_is_an_error_and_never_a_page_for_a_generic_downloader(stand, name):
    sources, probe = stand
    await sources._http.aclose()
    sources._http = host_that("busy").client()
    # No browser pool, so the catalog of Vibix cannot be captured (and no test opens a real browser).
    sources.players.get("vibix")._browser_getter = None

    reply = await probe.request("sources.video.stream", {"url": OWNED[name]}, timeout=STEP_LIMIT_S)

    assert reply["handled"] is True and reply["transient"] is True and reply["error"]


# --- the class guards ---------------------------------------------------------------------------------------------------------
