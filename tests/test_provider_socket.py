"""The tracker's end of the socket of a provider (contracts/provider_socket.md): the module `sources` with a provider that is a
model of the specification on real HTTP (tests/provider_mock.py). What is checked is what crosses the socket and what the
module does with it: the sites and the player the manifest names join the registries and leave them as it says, a card is read
into a table from frames and a read is stopped by closing the connection, the file of a link is taken by the provider, the
search gives the provider's cards or its reason, and a provider that goes down is a state and a reason, never a lost page."""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from core import BaseModule, Bus
from core.db import Database
from modules.sources.module import SourcesModule
from modules.sources.provider import ProviderClient
from provider_mock import MockProvider, link_of, serving
from schema_db import migrated

CARD = "title://mock/7/X"
LINK = link_of("X", "tok 1")
WAIT_S = 10.0  # the most a test waits for what the code under test ends by itself: a longer wait is a red test, not a hang


class Probe(BaseModule):
    name = "probe"


async def nxt(sub, topic: str | None = None) -> dict:
    """The payload of the next envelope of a subscription (of the topic, when one is named)."""
    while True:
        env = await asyncio.wait_for(sub.queue.get(), WAIT_S)
        if topic is None or env.topic == topic:
            return env.payload


async def state_is(sub, state: str) -> dict:
    """The next change of the state of the socket, until it is the one asked for."""
    while True:
        got = await nxt(sub)
        if got["state"] == state:
            return got


class System:
    def __init__(self, mock, server, bus, sources, probe, db, **subs) -> None:
        self.mock, self.server, self.bus, self.sources, self.probe, self.db = mock, server, bus, sources, probe, db
        self.__dict__.update(subs)

    async def read(self, url: str = CARD) -> dict:
        """The read of a card, begun as the user's command begins it; the outcome is its event."""
        self.probe.send_command("sources.table.read", {"url": url})
        return await nxt(self.table_end)

    async def table(self, url: str = CARD) -> dict:
        return await self.probe.request("sources.table.get", {"url": url, "refresh": False})


async def start(tmp_path, mock: MockProvider, server, *, token: str = "", saved=None, path=None) -> System:
    path = path or migrated(tmp_path / "sources.db")
    db, bus = Database(str(path)), Bus()
    client = ProviderClient(server.url, token)
    sources = SourcesModule(bus, db, torrent_cache_dir=str(tmp_path / "cache"), provider=client, provider_reconnect=(0.05, 0.2))
    probe = Probe(bus)
    subs = dict(states=bus.subscribe("sources.provider.state"), applied=bus.subscribe("sources.provider.manifest"),
                table_end=bus.subscribe("sources.table.ready", "sources.table.failed"),
                progress=bus.subscribe("sources.table.progress"))
    if saved is not None:
        await sources.repo.provider_manifest_save(client.url, saved)
    await sources.start()
    await probe.start()
    return System(mock, server, bus, sources, probe, db, **subs)


@pytest.fixture
async def system(tmp_path):
    mock = MockProvider()
    mock.add_site("mock", "Мок (Mock)")
    mock.title("mock", 7, ["名"], [("1", "X", "tok 1", [1080]), ("2", "X", "tok 2", [2160]), ("3", "Y", "tok 3", [])])
    mock.sites["mock"].found = [{"id": 7, "name": "名"}]
    async with serving(mock) as server:
        built = await start(tmp_path, mock, server)
        await state_is(built.states, "up")
        await nxt(built.applied)  # the first manifest, which the tests that follow a change must not take for it
        yield built
        await built.probe.stop()
        await built.sources.stop()


# --- the manifest: what joins the registries and what leaves them ---------------------------------------------------------

async def test_a_site_that_takes_the_name_of_a_site_the_module_reads_is_skipped_and_the_others_join(system, caplog):
    system.mock.add_site("maccms", "Чужой")
    with caplog.at_level("WARNING", logger="sources"):
        await nxt(system.applied)
    assert system.sources.sites.find("title://maccms/1/X") is None and system.sources.tables.resolve(CARD).site == "mock"
    assert any("сайт поставщика maccms пропущен" in record.getMessage() for record in caplog.records)


# --- the read of a card ------------------------------------------------------------------------------------------------------------

async def test_a_row_of_a_player_the_manifest_does_not_name_is_an_answer_against_the_specification(system):
    system.mock.sites["mock"].titles["7"]["rows"][0]["player"] = "other:X"
    failed = await system.read()
    assert json.loads(failed["reason"])["code"] == "provider_bad_answer" and "other:X" in failed["reason"]


async def test_a_read_that_is_stopped_closes_the_connection_and_the_provider_stops_it(system):
    system.mock.release.clear()
    system.probe.send_command("sources.table.read", {"url": CARD})
    await asyncio.wait_for(system.mock.began.wait(), WAIT_S)
    assert (await system.probe.request("sources.table.cancel", {"url": CARD})) == {"cancelled": True}
    # The provider learned it from the closed connection: nothing else was said to it.
    await asyncio.wait_for(system.mock.cancelled.wait(), WAIT_S)
    failed = await nxt(system.table_end)
    assert json.loads(failed["reason"])["code"] == "read_cancelled" and system.mock.reads_cancelled == 1
    assert [path for path, _body in system.mock.requests if path != "manifest"] == ["read"]


# --- the download of an episode ----------------------------------------------------------------------------------------------------

async def fetch(system, link: str = LINK, target: str = "", **more) -> dict:
    """The file of a link into a path, as the downloads ask for it (`sources.video.fetch`)."""
    return await system.probe.request("sources.video.fetch", {"url": link, "target": target, **more})


async def test_a_download_that_ends_with_no_outcome_is_a_failure_that_says_nothing_of_the_source(system, tmp_path):
    system.mock.download_of(LINK, {"type": "alive"})
    got = await fetch(system, target=str(tmp_path / "e.mp4"))
    assert got["ok"] is False and got["kind"] == "unasked" and json.loads(got["error"])["code"] == "provider_stream_broken"


async def test_a_download_that_is_stopped_closes_the_connection_and_the_provider_stops_it(system, tmp_path):
    system.mock.download_release.clear()
    target = str(tmp_path / "e.mp4")
    work = asyncio.ensure_future(fetch(system, target=target))
    await asyncio.wait_for(system.mock.download_begun.wait(), WAIT_S)
    assert (await system.probe.request("sources.video.fetch.stop", {"target": target})) == {"stopped": True}
    got = await asyncio.wait_for(work, WAIT_S)
    # The provider learned it from the closed connection: nothing else was said to it.
    await asyncio.wait_for(system.mock.download_cancelled.wait(), WAIT_S)
    assert got["ok"] is False and json.loads(got["error"])["code"] == "download_stopped" and not os.path.exists(target)
    assert [path for path, _body in system.mock.requests if path == "download"] == ["download"]


# --- the search ----------------------------------------------------------------------------------------------------------------

async def test_the_search_gives_the_titles_of_the_provider_or_its_reason(system):
    got = await system.probe.request("sources.titles.search", {"query": "名", "page": 1})
    assert [line["url"] for title in got["titles"] for line in title["lines"]] == [CARD, "title://mock/7/Y"]  # a line is the source
    assert got["page"] == 1 and got["reason"] is None and "url" not in got["titles"][0]
    assert got["more"] is None and got["without_lines"] == 0  # a provider that does not say whether a page follows: not said
    system.mock.search_page = {"more": True, "without_lines": 3}  # what the provider left out of the page, and that another follows
    said = await system.probe.request("sources.titles.search", {"query": "名", "page": 1})
    assert said["more"] is True and said["without_lines"] == 3
    system.mock.search_status = 500
    failed = await system.probe.request("sources.titles.search", {"query": "名"})
    assert failed["titles"] == [] and json.loads(failed["reason"])["code"] == "provider_unavailable"


# --- a provider that is down ------------------------------------------------------------------------------------------------------------

async def test_with_no_provider_every_try_of_the_user_at_its_world_says_it_is_not_connected_and_begins_nothing(tmp_path):
    bus = Bus()
    sources = SourcesModule(bus, Database(str(migrated(tmp_path / "s.db"))), torrent_cache_dir=str(tmp_path / "cache"))
    probe = Probe(bus)
    ended = bus.subscribe("sources.table.ready", "sources.table.failed")
    await sources.start()
    await probe.start()
    try:
        # «Перечитать»: the read is not begun, the page says why.
        probe.send_command("sources.table.read", {"url": CARD})
        got = await nxt(ended)
        assert got["url"] == CARD and json.loads(got["reason"]) == {"code": "provider_not_connected", "state": "none"}
        # What a synchronization asks before it opens: the pages of the provider's world, whether or not a site was ever heard of.
        asked = [CARD, "https://example.org/p/1", "title://other/3/X/"]
        assert [each["url"] for each in (await probe.request("sources.pages.check", {"urls": asked}))["blocked"]] == [CARD, "title://other/3/X/"]
        # A link of that world is nobody's failure and never goes to the generic downloader; a web address is the downloader's.
        link = await probe.request("sources.video.stream", {"url": LINK})
        assert link["handled"] and link["unasked"] and link["transient"] and json.loads(link["error"])["code"] == "provider_not_connected"
        assert await probe.request("sources.video.stream", {"url": "https://example.org/x"}) == {"handled": False}
    finally:
        await probe.stop()
        await sources.stop()


# --- the pages no source lists --------------------------------------------------------------------------------------------------------

class Catalog(BaseModule):
    """The catalog as `sources` asks it which pages the sources of the series list."""

    name = "catalog"

    def __init__(self, bus, addresses: list[str]) -> None:
        self.addresses = addresses
        super().__init__(bus)

    def register(self) -> None:
        self.handle("catalog.series.list", self.on_list)

    async def on_list(self, env) -> list[dict]:
        return [{"sources": [{"url": each} for each in self.addresses]}]
