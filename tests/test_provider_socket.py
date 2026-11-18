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

from core import BaseModule, Bus, BusRequestError
from core.contracts import NotAllowed
from core.db import Database
from modules.sources.module import SourcesModule
from modules.sources.provider import ProviderClient, RemoteFamily
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


async def settled_down(system) -> None:
    """The provider is gone for good: the feed may have reopened once against the server that was still closing, and the state
    says `down` only after its last drop is counted."""
    while system.sources.connector.state != "down":
        await nxt(system.states)


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

async def test_the_sites_and_the_player_of_the_manifest_join_the_registries_with_no_network_after_that(system):
    sources = system.sources
    found = sources.tables.resolve("title://mock/7/X/")
    assert (found.site, found.canonical_url, found.source_type) == ("mock", CARD, "web_video")
    assert sources.tables.resolve("title://mock/X").site is None and sources.tables.resolve("title://other/7/X").site is None
    player = sources.players.get("line:X")
    assert isinstance(player, RemoteFamily) and "line:X" in sources.players and "other:X" not in sources.players
    # The video of a row is what its link names for the host: the item, decoded.
    assert sources.players.video_of("line:X", LINK) == "tok 1" and player.owns(LINK) and not player.owns("title://other/7#line=X&item=t")
    readers = (await system.probe.request("sources.settings.get", {}))["readers"]
    assert {"name": "mock", "kind": "site", "domains": ["mock"]} in readers
    # The manifest is kept in the store of the module, as it came.
    kept = await sources.repo.provider_manifest(system.server.url)
    assert kept is not None and kept.revision == system.mock.manifest().revision and [each.name for each in kept.sites] == ["mock"]


async def test_the_players_of_the_provider_are_players_of_the_order_of_sources_each_where_its_manifest_says(system):
    await system.sources.repo.save_table(_stored(CARD, "line:Y", [720]))
    asked = await system.probe.request("sources.players.list", {"urls": [CARD]})
    facts = {each["name"]: each for each in asked["players"]}
    assert facts["line:X"]["kind"] == "line" and facts["line:X"]["places"] == [{"name": "mock", "label": "Мок (Mock)", "sources": 0}]
    assert facts["line:Y"]["height"] == 720 and facts["line:Y"]["places"] == [{"name": "mock", "label": "Мок (Mock)", "sources": 1}]


def _stored(url: str, player: str, heights: list[int]):
    from core.contracts import ReleaseTable, RowState, TableRow
    return ReleaseTable(url=url, site="mock", names=["名"], read_at="2026-11-10T10:00:00+00:00", complete=True,
                        rows=[TableRow(key="k", player=player, state=RowState.PLAYABLE, heights=heights, text="1")])


async def test_a_manifest_that_changes_adds_a_site_and_one_that_drops_it_takes_it_out(system):
    system.mock.add_site("second", "Второй (Second)")
    assert (await nxt(system.applied))["revision"] == system.mock.manifest().revision
    assert system.sources.tables.resolve("title://second/1/X").site == "second"
    del system.mock.sites["second"]
    system.mock.changed()
    await nxt(system.applied)
    assert system.sources.tables.resolve("title://second/1/X").site is None and system.sources.tables.resolve(CARD).site == "mock"


async def test_a_site_that_takes_the_name_of_a_site_the_module_reads_is_skipped_and_the_others_join(system, caplog):
    system.mock.add_site("maccms", "Чужой")
    with caplog.at_level("WARNING", logger="sources"):
        await nxt(system.applied)
    assert system.sources.sites.find("title://maccms/1/X") is None and system.sources.tables.resolve(CARD).site == "mock"
    assert any("сайт поставщика maccms пропущен" in record.getMessage() for record in caplog.records)


async def test_a_provider_of_another_protocol_is_no_provider_of_this_module(tmp_path):
    mock = MockProvider(protocol=1)
    mock.add_site("mock", "Мок (Mock)")
    async with serving(mock) as server:
        built = await start(tmp_path, mock, server)
        got = await state_is(built.states, "incompatible")
        assert json.loads(got["reason"])["code"] == "provider_incompatible"
        assert built.sources.tables.resolve(CARD).site is None
        await built.probe.stop()
        await built.sources.stop()


async def test_a_wrong_token_is_a_refusal_the_state_says(tmp_path):
    mock = MockProvider(token="right")
    mock.add_site("mock", "Мок (Mock)")
    async with serving(mock) as server:
        built = await start(tmp_path, mock, server, token="wrong")
        got = await state_is(built.states, "down")
        assert json.loads(got["reason"])["code"] == "provider_refused"
        await built.probe.stop()
        await built.sources.stop()


# --- the read of a card ------------------------------------------------------------------------------------------------------------

async def test_a_card_is_read_into_a_table_from_frames_and_every_row_says_which_video_it_is(system):
    got = await system.read()
    assert got["url"] == CARD
    phases = []
    while not system.progress.queue.empty():
        phases.append((await nxt(system.progress))["phase"])
    assert phases and set(phases) == {"release"}
    table = (await system.table())["table"]
    assert (table["title"], table["names"], table["site"]) == ("名", ["名"], "mock")
    assert [(row["key"], row["player"], row["state"], row["heights"], row["video"]) for row in table["rows"]] == [
        ("X:1", "line:X", "playable", [1080], "tok 1"), ("X:2", "line:X", "playable", [2160], "tok 2")]
    (_path, sent), = [request for request in system.mock.requests if request[0] == "read"]
    assert sent == {"url": CARD, "rows": "all", "recent": None, "fresh": True, "off": [], "last_rows": [], "term_hours": 1}


async def test_a_page_that_waits_for_the_user_is_not_begun_by_a_window_and_the_next_read_carries_what_the_last_table_holds(system):
    # An application card is begun by the user's command, whatever asks for its table (the flag of its site, `first_read_by_user`).
    assert (await system.probe.request("sources.table.get", {"url": CARD}))["state"] == "absent"
    assert system.mock.reads_begun == 0
    await system.read()
    system.sources._set_players_off('["line:Y"]')
    system.probe.send_command("sources.table.read", {"url": CARD, "rows": "-2"})
    await nxt(system.table_end)
    sent = [body for path, body in system.mock.requests if path == "read"][-1]
    assert sent["rows"] == "-2" and sent["off"] == ["line:Y"] and sent["fresh"] is True
    assert [row["key"] for row in sent["last_rows"]] == ["X:1", "X:2"]  # the page is the line X: the rows of Y are another page's


async def test_a_read_the_provider_refuses_fails_with_its_reason_and_the_last_table_still_serves(system):
    await system.read()
    system.mock.read_error = json.dumps({"code": "app_title_absent", "app": "mock"})
    failed = await system.read()
    assert failed == {"url": CARD, "reason": system.mock.read_error}
    again = await system.table()
    assert again["state"] == "failed" and again["reason"] == system.mock.read_error and len(again["table"]["rows"]) == 2


async def test_a_read_that_ends_with_no_outcome_is_a_broken_stream_and_never_a_part_of_a_table(system):
    system.mock.read_breaks = True
    failed = await system.read()
    assert json.loads(failed["reason"])["code"] == "provider_stream_broken"
    assert (await system.table())["table"] is None


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


async def test_a_link_of_the_family_is_taken_by_the_provider_and_no_stream_is_asked_of_it_and_any_other_address_is_not_handled(system):
    got = await system.probe.request("sources.video.stream", {"url": LINK, "max_height": 1080})
    # The provider takes the whole file itself: the module says so with no network (nothing was asked of the provider).
    assert got == {"handled": True, "stream_url": LINK, "headers": {}, "progressive": False, "playlist": False,
                   "player_fetch": True, "fetch_by": "provider"}
    assert [path for path, _body in system.mock.requests if path != "manifest"] == []
    assert (await system.probe.request("sources.video.stream", {"url": "https://example.org/x"})) == {"handled": False}


async def test_the_file_of_a_link_is_asked_of_the_provider_by_link_path_and_cap_with_its_telemetry_told_as_progress(system, tmp_path):
    told = system.bus.subscribe("sources.video.progress")
    target = str(tmp_path / "library" / "e1.mp4")
    got = await fetch(system, target=target, max_height=1080)
    assert got == {"ok": True, "seconds": 0.0, "longest": 0.0, "height": 720} and os.path.isfile(target)
    (_path, sent), = [request for request in system.mock.requests if request[0] == "download"]
    assert sent == {"url": LINK, "path": target, "max_height": 1080, "term_hours": 1}
    figures = [(await nxt(told)) for _ in range(2)]
    assert figures[0] == {"target": target, "phase": "link", "source_seconds": 1420.0}
    assert figures[1]["phase"] == "download" and figures[1]["progress"] == 40 and figures[1]["target"] == target


async def test_a_refusal_of_the_provider_keeps_its_kind_and_its_reason(system, tmp_path):
    for kind in ("final", "broken", "unasked"):
        system.mock.download_of(LINK, {"type": "error", "reason": json.dumps({"code": "app_no_stream", "app": "mock"}), "kind": kind})
        got = await fetch(system, target=str(tmp_path / f"{kind}.mp4"))
        assert got["ok"] is False and got["kind"] == kind and got["final"] is (kind == "final")
        assert json.loads(got["error"])["code"] == "app_no_stream"
    assert not os.path.exists(tmp_path / "final.mp4")


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

async def test_a_provider_that_goes_down_is_a_state_and_a_reason_and_keeps_its_sites_and_comes_back_by_itself(system, tmp_path):
    await system.read()
    await system.server.down()
    assert json.loads((await state_is(system.states, "down"))["reason"])["code"] == "provider_unavailable"
    await settled_down(system)
    # A synchronization asks which pages the provider that is down reads: it opens no sync of a series that has one.
    (blocked,) = (await system.probe.request("sources.pages.check", {"urls": [CARD, "https://example.org/p"]}))["blocked"]
    assert blocked["url"] == CARD and json.loads(blocked["reason"])["state"] == "down"
    # The pages of its sites are still its sites': nothing is forgotten, the last table serves.
    assert system.sources.tables.resolve(CARD).site == "mock"
    # A read the user asks for is not begun: the module says the provider is not connected, and the last table serves.
    failed = await system.read()
    assert json.loads(failed["reason"]) == {"code": "provider_not_connected", "state": "down"}
    assert len((await system.table())["table"]["rows"]) == 2
    fetched = await fetch(system, target=str(tmp_path / "e.mp4"))
    assert fetched["ok"] is False and fetched["kind"] == "unasked" and "provider_unavailable" in fetched["error"]
    with pytest.raises(BusRequestError) as searched:
        await system.probe.request("sources.titles.search", {"query": "名"})
    assert "provider_not_connected" in str(searched.value)
    await system.server.up()
    await state_is(system.states, "up")
    assert system.mock.reads_begun == 1  # the read that failed while the provider was down never reached it
    assert (await system.probe.request("sources.pages.check", {"urls": [CARD]})) == {"blocked": []}
    assert (await system.read())["url"] == CARD and system.mock.reads_begun == 2  # back: the same page is read as it was


async def test_the_saved_manifest_registers_the_sites_before_the_provider_is_reached(tmp_path):
    mock = MockProvider()
    mock.add_site("mock", "Мок (Mock)")
    manifest = mock.manifest()
    path = migrated(tmp_path / "sources.db")
    # Nothing answers at the address of the provider at the start of this module.
    mock_down = MockProvider()
    async with serving(mock_down) as gone:
        address = gone.url
        await gone.down()
        built = await start(tmp_path, mock_down, gone, saved=manifest, path=path)
        try:
            assert built.sources.tables.resolve(CARD).site == "mock"
            assert built.sources.players.video_of("line:X", LINK) == "tok 1"
            assert (await state_is(built.states, "down"))["provider"] == "mock" and address in built.sources.connector.client.url
        finally:
            await built.probe.stop()
            await built.sources.stop()


async def test_a_module_with_no_provider_has_no_socket_and_reads_what_it_has(tmp_path):
    sources = SourcesModule(Bus(), Database(str(migrated(tmp_path / "s.db"))), torrent_cache_dir=str(tmp_path / "cache"))
    assert sources.connector is None
    with pytest.raises(NotAllowed) as refused:  # the search is the provider's: nothing to search, and the user is told so
        await sources.on_titles_search(type("Env", (), {"payload": {"query": "x"}})())
    assert json.loads(str(refused.value)) == {"code": "provider_not_connected", "state": "none"}


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


async def test_a_provider_that_was_never_reached_is_not_connected_either_and_its_page_is_not_read(tmp_path):
    mock = MockProvider()
    async with serving(mock) as gone:
        await gone.down()
        built = await start(tmp_path, mock, gone)
        try:
            await state_is(built.states, "down")
            failed = await built.read()
            reason = json.loads(failed["reason"])
            assert reason["code"] == "provider_not_connected" and reason["state"] == "down"
            assert mock.reads_begun == 0 and (await built.table())["state"] == "failed"
        finally:
            await built.probe.stop()
            await built.sources.stop()


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


async def test_a_page_some_source_lists_keeps_its_table_while_no_site_reads_its_address_and_a_page_none_lists_goes(tmp_path):
    """The provider is not up and was never heard (nothing saved): the sites of its pages are not in the registry, and the
    tables of the pages are all the module has of them. A source that lists a page keeps it; the unlisted one goes."""
    mock = MockProvider()
    async with serving(mock) as gone:
        await gone.down()
        bus = Bus()
        catalog = Catalog(bus, [CARD])
        sources = SourcesModule(bus, Database(str(migrated(tmp_path / "s.db"))), torrent_cache_dir=str(tmp_path / "c"),
                                provider=ProviderClient(gone.url), provider_reconnect=(0.05, 0.2))
        await catalog.start()
        await sources.start()
        try:
            assert sources.tables.resolve(CARD).site is None
            await sources.repo.save_table(_stored(CARD, "line:X", [1080]))
            await sources.repo.save_table(_stored("title://mock/8/X", "line:X", [1080]))
            assert await sources._forget_unlisted_pages() is True
            assert (await sources.repo.stored(CARD)).table is not None
            assert (await sources.repo.stored("title://mock/8/X")).table is None
        finally:
            await sources.stop()
            await catalog.stop()


async def test_the_name_a_site_gives_its_source_is_kept_with_its_table(tmp_path):
    from core.contracts import ReleaseTable, RowState, TableRow

    sources = SourcesModule(Bus(), Database(str(migrated(tmp_path / "s.db"))), torrent_cache_dir=str(tmp_path / "c"))
    table = ReleaseTable(url=CARD, site="mock", names=["万界独尊"], title="半日 · 万界独尊 (2021)",
                         read_at="2026-11-10T10:00:00+00:00", complete=True,
                         rows=[TableRow(key="mock:X:1", player="line:X", state=RowState.PLAYABLE, text="1")])
    await sources.repo.save_table(table)
    assert (await sources.repo.stored(CARD)).table.title == "半日 · 万界独尊 (2021)"


async def test_a_page_no_site_reads_now_still_serves_the_table_it_had_so_a_source_never_loses_its_rows_for_a_missing_reader(tmp_path):
    """The first start after the applications left the tracker, with the provider not yet up and no manifest ever saved: the
    sites of the applications are not in the registry. A sync, a window and the recount of a layout ask for the tables of the
    pages all the same, and they get the last ones, with the reason the page is not read."""
    mock = MockProvider()
    async with serving(mock) as gone:
        await gone.down()
        sources = SourcesModule(Bus(), Database(str(migrated(tmp_path / "s.db"))), torrent_cache_dir=str(tmp_path / "c"),
                                provider=ProviderClient(gone.url), provider_reconnect=(0.05, 0.2))
        await sources.start()
        try:
            assert sources.tables.resolve(CARD).site is None
            await sources.repo.save_table(_stored(CARD, "line:X", [1080]))
            for refresh in (True, False):
                got = await sources.on_table_get(type("Env", (), {"payload": {"url": CARD, "refresh": refresh}})())
                assert got["state"] == "failed" and json.loads(got["reason"])["code"] == "provider_not_connected"
                assert [row["player"] for row in got["table"]["rows"]] == ["line:X"]
            never = await sources.on_table_get(type("Env", (), {"payload": {"url": "title://mock/99/X"}})())
            assert never["state"] == "failed" and never["table"] is None
        finally:
            await sources.stop()


def test_the_schema_file_of_the_socket_is_the_one_the_models_make():
    """The schema is what a writer of a provider reads (contracts/provider_socket.md, appendix A): a document the models
    have left behind would teach him a protocol that is no longer the tracker's."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("provider_schema", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "checks", "provider_schema.py"))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    with open(tool.PATH, encoding="utf-8") as stored:
        assert stored.read() == tool.text(), "contracts/provider_socket.schema.json is stale: run tools/checks/provider_schema.py"
    assert json.loads(tool.text())["protocol"] == 2
