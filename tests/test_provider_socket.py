"""The tracker's end of the socket of a provider (contracts/provider_socket.md): the module `sources` with a provider that is a
model of the specification on real HTTP (tests/provider_mock.py). What is checked is what crosses the socket and what the
module does with it: the sites and the player the manifest names join the registries and leave them as it says, a card is read
into a table from frames and a read is stopped by closing the connection, a stream is asked right before it is wanted, the
search gives the provider's cards or its reason, and a provider that goes down is a state and a reason, never a lost page."""
from __future__ import annotations

import asyncio
import json

import pytest

from core import BaseModule, Bus
from core.db import Database
from core.contracts.provider import StreamReply
from modules.sources.module import SourcesModule
from modules.sources.provider import ProviderClient, RemoteFamily
from provider_mock import MockProvider, link_of, serving
from provider_world import needs_provider
from schema_db import migrated

CARD = "app://mock/7"
LINK = link_of("mock", 7, "X", "tok 1")
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
    mock.add_app("mock", "Мок (Mock)")
    mock.card("mock", 7, ["名"], [("1", "X", "tok 1", [1080]), ("2", "X", "tok 2", [2160]), ("3", "Y", "tok 3", [])])
    mock.apps["mock"].found = [{"id": 7, "name": "名"}]
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
    found = sources.tables.resolve("app://mock/7/")
    assert (found.site, found.canonical_url, found.source_type) == ("mock", CARD, "web_video")
    assert sources.tables.resolve("app://mock/x").site is None and sources.tables.resolve("app://other/7").site is None
    player = sources.players.get("line:X")
    assert isinstance(player, RemoteFamily) and "line:X" in sources.players and "other:X" not in sources.players
    # The video of a row is what its link names for the host: the item, decoded.
    assert sources.players.video_of("line:X", LINK) == "tok 1" and player.owns(LINK) and not player.owns("app://other/7#line=X&item=t")
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
    system.mock.add_app("second", "Второй (Second)")
    assert (await nxt(system.applied))["revision"] == system.mock.manifest().revision
    assert system.sources.tables.resolve("app://second/1").site == "second"
    del system.mock.apps["second"]
    system.mock.changed()
    await nxt(system.applied)
    assert system.sources.tables.resolve("app://second/1").site is None and system.sources.tables.resolve(CARD).site == "mock"


async def test_a_site_that_takes_the_name_of_a_site_the_module_reads_is_skipped_and_the_others_join(system, caplog):
    system.mock.add_app("maccms", "Чужой")
    with caplog.at_level("WARNING", logger="sources"):
        await nxt(system.applied)
    assert system.sources.sites.find("app://maccms/1") is None and system.sources.tables.resolve(CARD).site == "mock"
    assert any("сайт поставщика maccms пропущен" in record.getMessage() for record in caplog.records)


async def test_a_provider_of_another_protocol_is_no_provider_of_this_module(tmp_path):
    mock = MockProvider(protocol=2)
    mock.add_app("mock", "Мок (Mock)")
    async with serving(mock) as server:
        built = await start(tmp_path, mock, server)
        got = await state_is(built.states, "incompatible")
        assert json.loads(got["reason"])["code"] == "provider_incompatible"
        assert built.sources.tables.resolve(CARD).site is None
        await built.probe.stop()
        await built.sources.stop()


async def test_a_wrong_token_is_a_refusal_the_state_says(tmp_path):
    mock = MockProvider(token="right")
    mock.add_app("mock", "Мок (Mock)")
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
    assert (table["title"], table["names"], table["site"]) == ("Мок (Mock) · 名", ["名"], "mock")
    assert [(row["key"], row["player"], row["state"], row["heights"], row["video"]) for row in table["rows"]] == [
        ("mock:X:1", "line:X", "playable", [1080], "tok 1"), ("mock:X:2", "line:X", "playable", [2160], "tok 2"),
        ("mock:Y:3", "line:Y", "absent", [], "tok 3")]
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
    assert [row["key"] for row in sent["last_rows"]] == ["mock:X:1", "mock:X:2", "mock:Y:3"]


async def test_a_read_the_provider_refuses_fails_with_its_reason_and_the_last_table_still_serves(system):
    await system.read()
    system.mock.read_error = json.dumps({"code": "app_title_absent", "app": "mock"})
    failed = await system.read()
    assert failed == {"url": CARD, "reason": system.mock.read_error}
    again = await system.table()
    assert again["state"] == "failed" and again["reason"] == system.mock.read_error and len(again["table"]["rows"]) == 3


async def test_a_read_that_ends_with_no_outcome_is_a_broken_stream_and_never_a_part_of_a_table(system):
    system.mock.read_breaks = True
    failed = await system.read()
    assert json.loads(failed["reason"])["code"] == "provider_stream_broken"
    assert (await system.table())["table"] is None


async def test_a_row_of_a_player_the_manifest_does_not_name_is_an_answer_against_the_specification(system):
    system.mock.apps["mock"].cards[7]["rows"][0]["player"] = "other:X"
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


# --- the stream of an episode ------------------------------------------------------------------------------------------------------

async def test_the_stream_of_a_link_of_the_family_is_asked_of_the_provider_and_any_other_address_is_not_handled(system):
    system.mock.stream_of(LINK, StreamReply(handled=True, stream_url="https://cdn.model/a.m3u8", headers={"Referer": "r"},
                                            playlist=True))
    got = await system.probe.request("sources.video.stream", {"url": LINK, "max_height": 1080})
    assert got == {"handled": True, "stream_url": "https://cdn.model/a.m3u8", "headers": {"Referer": "r"}, "progressive": False,
                   "playlist": True, "player_fetch": False}
    (_path, sent), = [request for request in system.mock.requests if request[0] == "stream"]
    assert sent == {"url": LINK, "max_height": 1080, "term_hours": 1}
    assert (await system.probe.request("sources.video.stream", {"url": "https://example.org/x"})) == {"handled": False}


async def test_a_separate_audio_address_of_a_stream_is_not_dropped_on_its_way_to_the_downloads(system):
    system.mock.stream_of(LINK, StreamReply(handled=True, stream_url="https://cdn.model/v.mp4", audio_url="https://cdn.model/a.mp4",
                                            headers={"Referer": "https://player.model/"}))
    got = await system.probe.request("sources.video.stream", {"url": LINK})
    assert got["audio_url"] == "https://cdn.model/a.mp4" and got["stream_url"] == "https://cdn.model/v.mp4"
    assert got["progressive"] is False and got["playlist"] is False


async def test_a_stream_the_provider_cannot_give_is_an_error_with_its_reason_and_whether_it_may_pass(system):
    other = link_of("mock", 7, "X", "tok 2")
    system.mock.stream_of(LINK, StreamReply(handled=True, error=json.dumps({"code": "app_no_parser"}), transient=False))
    system.mock.stream_of(other, StreamReply(handled=True, error=json.dumps({"code": "player_status"}), transient=True))
    refused = await system.probe.request("sources.video.stream", {"url": LINK})
    passing = await system.probe.request("sources.video.stream", {"url": other})
    assert (refused["handled"], json.loads(refused["error"])["code"], refused["transient"]) == (True, "app_no_parser", False)
    assert (passing["handled"], json.loads(passing["error"])["code"], passing["transient"]) == (True, "player_status", True)
    # A link of the family that the provider calls not its own is against the specification: no page goes to a generic downloader.
    lost = await system.probe.request("sources.video.stream", {"url": link_of("mock", 7, "X", "tok 3")})
    assert lost["handled"] is True and json.loads(lost["error"])["code"] == "provider_bad_answer"


# --- the search ----------------------------------------------------------------------------------------------------------------

async def test_the_search_gives_the_cards_of_the_provider_or_its_reason_for_each_source_of_search(system):
    got = await system.probe.request("sources.apps.search", {"query": "名", "page": 1})
    (found,) = got["apps"]
    assert (found["app"], found["label"]) == ("mock", "Мок (Mock)") and [card["url"] for card in found["cards"]] == [CARD]
    assert (await system.probe.request("sources.apps.search", {"query": "名", "app": "nobody"}))["apps"] == []
    system.mock.search_status = 500
    failed = (await system.probe.request("sources.apps.search", {"query": "名"}))["apps"]
    assert [(each["app"], json.loads(each["reason"])["code"]) for each in failed] == [("mock", "provider_unavailable")]


# --- a provider that is down ------------------------------------------------------------------------------------------------------------

async def test_a_provider_that_goes_down_is_a_state_and_a_reason_and_keeps_its_sites_and_comes_back_by_itself(system):
    await system.read()
    await system.server.down()
    assert json.loads((await state_is(system.states, "down"))["reason"])["code"] == "provider_unavailable"
    # The pages of its sites are still its sites': nothing is forgotten, the last table serves.
    assert system.sources.tables.resolve(CARD).site == "mock"
    failed = await system.read()
    assert json.loads(failed["reason"])["code"] == "provider_unavailable"
    assert len((await system.table())["table"]["rows"]) == 3
    streamed = await system.probe.request("sources.video.stream", {"url": LINK})
    assert streamed["handled"] is True and streamed["transient"] is True and "provider_unavailable" in streamed["error"]
    found = (await system.probe.request("sources.apps.search", {"query": "名"}))["apps"]
    assert [json.loads(each["reason"])["code"] for each in found] == ["provider_unavailable"]
    await system.server.up()
    await state_is(system.states, "up")
    assert system.mock.reads_begun == 1  # the read that failed while the provider was down never reached it


async def test_the_saved_manifest_registers_the_sites_before_the_provider_is_reached(tmp_path):
    mock = MockProvider()
    mock.add_app("mock", "Мок (Mock)")
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
    found = await sources.on_apps_search(type("Env", (), {"payload": {"query": "x"}})())
    assert found == {"apps": []}


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
            await sources.repo.save_table(_stored("app://mock/8", "line:X", [1080]))
            assert await sources._forget_unlisted_pages() is True
            assert (await sources.repo.stored(CARD)).table is not None
            assert (await sources.repo.stored("app://mock/8")).table is None
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


@needs_provider
def test_the_models_of_the_wire_are_the_same_text_at_both_ends_of_the_socket():
    """The tracker and the provider each hold the models of what crosses the socket: they are one text, and a difference is
    a protocol that two processes read two ways."""
    from chinese_sources import wire as theirs
    from chinese_sources.core import contracts as their_rows
    from core.contracts import apps, provider as ours, release_table

    for name in ("ProviderInfo", "SiteAddress", "ManifestSite", "ManifestFamily", "ManifestPlace", "ManifestPlayer", "ManifestSearch",
                 "Manifest", "FeedEvent", "ReadRequest", "ReadFrame", "StreamRequest", "StreamReply", "ProviderState"):
        assert getattr(theirs, name).model_json_schema() == getattr(ours, name).model_json_schema(), name
    assert [each.value for each in their_rows.RowState] == [each.value for each in release_table.RowState]
    for name, owner in (("RowVoice", release_table), ("TableRow", release_table),
                        ("UnreadPart", release_table), ("AppSearchRequest", apps), ("AppCard", apps), ("AppResults", apps),
                        ("AppSearchReply", apps)):
        assert getattr(their_rows, name).model_json_schema() == getattr(owner, name).model_json_schema(), name
    for constant in ("PROTOCOL", "HEARTBEAT_S", "SILENCE_S", "FRAME_PROGRESS", "FRAME_ALIVE", "FRAME_DRAFT", "FRAME_ERROR",
                     "EVENT_HELLO", "EVENT_CHANGED"):
        assert getattr(theirs, constant) == getattr(ours, constant), constant
