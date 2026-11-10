"""The video applications (modules/sources/apps): the protocol of the App99 family on fixed vectors, the session, the table
of a card (every line a player of its own, in the order of the card, its frame measured on its newest episode), the stream of an episode
through the parsers its line allows, and the search — against a model of the application by its protocol
(tests/app99_model.py)."""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from core import Bus
from core.db import Database
from core.envelope import Envelope
from core.http_limits import BoundedTransport
from modules.sources.apps import AppProfile, card_address, profiles_of
from modules.sources.apps.app99 import App99Client, seal, signature, unseal
from modules.sources.module import SourcesModule
from modules.sources.apps.lines import LineRegistry
from modules.sources.players.app_lines import AppPlayer, measure_file_frame
from modules.sources.players.contract import Answer, Kind, Reading, StreamUnavailable
from modules.sources.players.registry import PlayerRegistry
from modules.sources.sites import ReadContext
from modules.sources.sites.apps import AppSite
from app99_model import HOST, UUID, App99Model, Card, Line, profile
from schema_db import migrated

PLAYLIST = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nseg0.ts\n#EXT-X-ENDLIST\n"
CDN = "https://cdn.model/"


# --- the protocol, on vectors made by an independent client checked against the real application ----------------------

def test_a_request_is_sealed_and_signed_and_an_answer_is_read_compressed_or_not_as_the_application_does():
    key = UUID.replace("-", "").encode()
    payload = {"kw": "万界独尊", "page": 1, "token": "t0", "timestamp": "1700000000000", "nonce": "bm9uY2U="}
    sealed = seal(payload, key, bytes(range(16)))
    assert sealed == ("AAECAwQFBgcICQoLDA0OD/yg1S+Zwi5ntGSXHutHQgTqjrOjIZySopjg3dHOFDIWcMm9WzPCw0SAnl6TVl1x1RdiHKLPLTq6wD7ncqGo"
                      "R+iO4RCw/j1JnRHDV9gyhFcfNx/jJb/9qkJzDreb8oI1Sg==")
    assert signature("bm9uY2U=", "1700000000000", sealed, "t0") == \
        "54da72493a4b6f185817ff1517fec8e67a08775de2f4c124592f2a727e833e4e"
    compressed = ("AAECAwQFBgcICQoLDA0OD7rs33iAI/XvBzanEmu0+5LaUB/tx/ZvjPjs/50CwIht4D6c7AAmeMJ66G3imipWcuLxiZDyPh+KsbMbZ/IfBGpZ"
                  "cVZOlRTBZDXgLXPg9Ynf")
    assert unseal(compressed, key) == {"code": 0, "msg": "success", "data": {"name": "万界独尊"}}
    assert unseal(sealed, key) == payload


def test_an_application_is_described_by_the_environment_and_a_value_that_is_not_one_stops_the_start():
    found = profiles_of({"ST_APP_BANRI": '{"label": "半日", "protocol": "app99", "host": "h"}', "ST_OTHER": "x"})
    assert [(each.name, each.label, each.protocol) for each in found] == [("banri", "半日", "app99")]
    with pytest.raises(ValueError):
        profiles_of({"ST_APP_X": '{"host": "h"}'})


# --- a world of the model and a CDN as the real one serves --------------------------------------------------------------

# What the hosts of 半日 were seen to serve (the inventory of 2026-11-10): playlists announced as images, an MP4 announced
# as a picture, a 403 with a page of HTML, a page instead of a video, an advertisement host that refuses the connection,
# a redirect to the playlist.
HTML = "<!DOCTYPE html><html><head><title>403 Forbidden</title></head></html>"
MP4_HEAD = b"\0\0\0\x20ftypisom" + b"\0" * 5000


def world(model: App99Model, playlists: dict[str, str] | None = None) -> tuple[httpx.AsyncClient, list[str]]:
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(HOST):
            return model.handle(request)
        asked.append(url)
        path = request.url.path
        if path.startswith("/png/"):  # a playlist announced as a picture
            return httpx.Response(200, headers={"Content-Type": "image/png"}, text=PLAYLIST)
        if path.startswith("/jpeg/"):  # an MP4 announced as a picture, its range honoured
            return httpx.Response(206, headers={"Content-Type": "image/jpeg"}, content=MP4_HEAD)
        if path.startswith("/forbidden/"):
            return httpx.Response(403, headers={"Content-Type": "text/html"}, text=HTML)
        if path.startswith("/page/"):  # a page instead of the stream, with a status of success
            return httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html><body>Error 1020</body></html>")
        if path.startswith("/moved/"):
            return httpx.Response(302, headers={"Location": url.replace("/moved/", "/png/")})
        if request.url.host == "ad.model":  # the host of an advertisement given instead of the episode
            raise httpx.ConnectError("All connection attempts failed", request=request)
        text = (playlists or {}).get(url)
        return httpx.Response(200, text=text) if text is not None else httpx.Response(404, text="")
    return httpx.AsyncClient(transport=BoundedTransport(httpx.MockTransport(handle))), asked


def admitting(*codes: str, refused: dict[str, str] | None = None) -> LineRegistry:
    return LineRegistry(frozenset(codes), refused or {})


def card_world() -> App99Model:
    model = App99Model(parsers=[3, 5, 9])
    model.line("ffm3u8", "非凡（备用线路）", kind=0)
    model.line("CO4K", "4K", parsers="9")
    model.line("co_egg", "花絮", kind=4, parsers="2")
    model.cards[219017] = Card(219017, "万界独尊", sub="Ten Thousand Worlds", lines=[
        Line("ffm3u8", [("第01集", f"{CDN}a/index.m3u8"), ("第02集", f"{CDN}b/index.m3u8")]),
        Line("CO4K", [("1", "CO4K_tok1"), ("2", "CO4K_tok2"), ("《万界独尊》PV", "CO4K_pv")]),
        Line("co_egg", [("419", "co_egg_x")]),
    ])
    return model


async def test_a_card_is_a_table_of_its_admitted_lines_each_row_with_the_frame_of_its_own_episode(caplog):
    model = card_world()
    model.parses[(9, "CO4K_pv")] = f"{CDN}c/pv.m3u8"
    model.parses[(9, "CO4K_tok1")] = f"{CDN}d/1.m3u8"
    playlists = {f"{CDN}{name}": PLAYLIST for name in ("a/index.m3u8", "b/index.m3u8", "c/pv.m3u8", "d/1.m3u8")}
    http, _asked = world(model, playlists)
    heads: list[str] = []
    frames = {"a": 720, "b": 1080, "c": 2160, "d": 1080}

    async def measure(_http, head, _referer):
        heads.append(head.segment)
        return frames[head.segment.removeprefix(CDN).split("/")[0]]

    registry = admitting("ffm3u8", "CO4K", refused={"co_egg": "419: stream — no parser"})
    async with http:
        player = AppPlayer(App99Client(profile(), lambda: http), measure, registry=registry)
        players = PlayerRegistry([player])
        site = AppSite(player)
        assert site.match_url("app://banri/219017") and not site.match_url("app://other/219017")
        assert site.canonical_url("app://banri/219017/") == card_address("banri", "219017")
        with caplog.at_level("INFO", logger="sources"):
            draft = await site.read("app://banri/219017", ReadContext(http, lambda *_: None))

        rows = [(row.key, row.player, row.player_label, row.text, row.state.value, row.heights) for row in draft.rows]
        # Every row of an admitted line, measured on its own episode; a line is named by its code; a line the registry
        # does not admit is no player, and the log of the read says which and why.
        assert rows == [
            ("banri:ffm3u8:1", "banri:ffm3u8", "ffm3u8", "第01集", "playable", [720]),
            ("banri:ffm3u8:2", "banri:ffm3u8", "ffm3u8", "第02集", "playable", [1080]),
            ("banri:CO4K:1", "banri:CO4K", "CO4K", "1", "playable", [1080]),
            ("banri:CO4K:2", "banri:CO4K", "CO4K", "2", "not_obtained", []),
            ("banri:CO4K:3", "banri:CO4K", "CO4K", "《万界独尊》PV", "playable", [2160]),
        ]
        assert [row.site_fields["line_name"] for row in draft.rows] == ["非凡（备用线路）"] * 2 + ["4K"] * 3
        assert any("co_egg (419: stream — no parser)" in record.getMessage() for record in caplog.records)
        # A row whose episode gave no stream says why.
        assert '"code":"app_no_parser"' in draft.rows[3].reason
        assert draft.names == ["万界独尊", "Ten Thousand Worlds"] and draft.title == "半日 · 万界独尊 (2021)"
        # One ask a row: each episode once; the refused line is not asked.
        assert sorted(heads) == sorted(f"{CDN}{name}/seg0.ts" for name in "abcd")
        assert sorted(fields["url"] for path, fields in model.calls if path == "/app/vodParser") == \
            ["CO4K_pv", "CO4K_tok1", "CO4K_tok2"]
        assert [row.site_fields["stream"] for row in draft.rows] == ["playlist"] * 2 + ["parser"] * 3
        assert players.video_of("banri:CO4K", draft.rows[2].link) == "CO4K_tok1"
        assert players.get("banri:CO4K") is player and "banri:CO4K" in players and "other:CO4K" not in players
        assert [(line.name, line.label) for line in player.line_players()] == [("banri:CO4K", "CO4K"),
                                                                               ("banri:ffm3u8", "ffm3u8")]

        # The next read keeps every frame the last table measured on its row: only the rows without one are asked again.
        parsed = len([1 for path, _fields in model.calls if path == "/app/vodParser"])
        again = await site.read("app://banri/219017", ReadContext(http, lambda *_: None, last_rows=tuple(draft.rows)))
        assert [(row.state, row.heights) for row in again.rows] == [(row.state, row.heights) for row in draft.rows]
        assert len(heads) == 4
        assert [fields["url"] for path, fields in model.calls if path == "/app/vodParser"][parsed:] == ["CO4K_tok2"]

        # The stream of an episode of a line of playlists is its playlist, its frame not measured for the download.
        got = await player.stream(draft.rows[0].link, http, None, players.pacer)
        assert got == {"stream_url": f"{CDN}a/index.m3u8", "headers": {}, "progressive": False, "playlist": True}
        assert len(heads) == 4


def test_the_registry_of_an_application_admits_its_lines_and_says_why_it_refused_the_others(tmp_path):
    (tmp_path / "banri.json").write_text(json.dumps({"app": "banri", "admitted": {"CO4K": {}, "NBY": {}}, "refused": {
        "ffm3u8": {"episode": "第01集", "step": "stream", "reason": "403"}}}), encoding="utf-8")
    registry = LineRegistry.load("banri", str(tmp_path))
    assert registry.admits("CO4K") and registry.admits("NBY") and not registry.admits("ffm3u8")
    assert registry.why_not("ffm3u8") == "第01集: stream — 403"
    assert "not admitted" in registry.why_not("ZZZ")
    assert LineRegistry.load("other", str(tmp_path)).admitted == frozenset()


async def test_a_card_of_many_episodes_is_measured_at_the_pace_of_twenty_to_thirty_rows_a_second():
    """Every answer of the application and of its CDN takes half a second, as the live ones do: the rows of a card are
    asked side by side at the pace of the read, not one after another."""
    model = App99Model(parsers=[9])
    model.line("BLU", "蓝光", parsers="9")
    items = [(f"第{n}集", f"tok{n}") for n in range(1, 201)]
    model.cards[1] = Card(1, "万界独尊", lines=[Line("BLU", items)])
    for n in range(1, 201):
        model.parses[(9, f"tok{n}")] = f"{CDN}e{n}/index.m3u8"

    async def handle(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(HOST):
            if request.url.path.endswith("/app/vodParser"):
                await asyncio.sleep(0.5)
            return model.handle(request)
        await asyncio.sleep(0.5)
        return httpx.Response(200, text=PLAYLIST)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        player = AppPlayer(App99Client(profile(), lambda: http), measure=lambda *_: _height(1080), registry=admitting("BLU"))
        site = AppSite(player)
        began = time.monotonic()
        draft = await site.read("app://banri/1", ReadContext(http, lambda *_: None))
        rate = len(draft.rows) / (time.monotonic() - began)
    assert [row.heights for row in draft.rows] == [[1080]] * 200
    assert 20 <= rate <= 30, rate


async def test_what_a_parser_gave_serves_the_download_within_the_hour_and_an_address_no_longer_served_is_parsed_anew():
    model = card_world()
    model.parses[(9, "CO4K_tok1")] = f"{CDN}d/1.m3u8"
    served = {f"{CDN}d/1.m3u8": PLAYLIST}
    http, _asked = world(model, served)
    now = [0.0]
    async with http:
        player = AppPlayer(App99Client(profile(), lambda: http), measure=lambda *_: _height(1080), clock=lambda: now[0],
                           registry=admitting("CO4K"))
        players = PlayerRegistry([player])
        draft = await AppSite(player).read("app://banri/219017", ReadContext(http, lambda *_: None))
        parses = lambda: [fields["url"] for path, fields in model.calls if path == "/app/vodParser"].count("CO4K_tok1")
        assert parses() == 1 and draft.rows[0].heights == [1080]
        # The download within the hour takes the address the read parsed.
        now[0] = 3599.0
        got = await player.stream(draft.rows[0].link, http, None, players.pacer)
        assert got["stream_url"] == f"{CDN}d/1.m3u8" and parses() == 1
        # Past the hour the episode is parsed again.
        now[0] = 3600.0
        await player.stream(draft.rows[0].link, http, None, players.pacer)
        assert parses() == 2
        # The host no longer serves the kept address: the episode is parsed anew, once, and the new address serves.
        del served[f"{CDN}d/1.m3u8"]
        model.parses[(9, "CO4K_tok1")] = f"{CDN}d/2.m3u8"
        served[f"{CDN}d/2.m3u8"] = PLAYLIST
        got = await player.stream(draft.rows[0].link, http, None, players.pacer)
        assert got["stream_url"] == f"{CDN}d/2.m3u8" and parses() == 3


async def test_a_stream_is_what_its_host_serves_by_its_signature_and_a_row_without_one_says_what_the_host_served():
    """Neither the form of the address nor the type the host announces tells a stream: playlists come as `image/png`, an
    MP4 as `image/jpeg`; a page, a status or a refused connection is no stream, and the row says which."""
    model = App99Model(parsers=[9])
    model.line("L", "线", parsers="9")
    kinds = {"png": f"{CDN}png/1", "jpeg": f"{CDN}jpeg/2", "forbidden": f"{CDN}forbidden/3", "page": f"{CDN}page/4",
             "ad": "https://ad.model/adVideoLp/5.mp4", "moved": f"{CDN}moved/6", "whole": f"{CDN}whole/7"}
    model.cards[1] = Card(1, "万界独尊", lines=[Line("L", [(name, f"tok-{name}") for name in kinds])])
    for name, address in kinds.items():
        model.parses[(9, f"tok-{name}")] = address
    http, asked = world(model)
    files: list[str] = []

    async def measure_file(address, _headers):
        files.append(address)
        return Answer(200, reading=Reading(Kind.PLAYABLE, 200, (1080,), files=((1080, address),)))

    async with http:
        player = AppPlayer(App99Client(profile(), lambda: http), measure=lambda *_: _height(720), measure_file=measure_file,
                           registry=admitting("L"))
        players = PlayerRegistry([player])
        draft = await AppSite(player).read("app://banri/1", ReadContext(http, lambda *_: None))
        got = {row.text: (row.state.value, row.heights, row.reason or "") for row in draft.rows}
        assert got["png"][:2] == ("playable", [720]) and got["moved"][:2] == ("playable", [720])
        assert got["jpeg"][:2] == ("playable", [1080]) and files == [f"{CDN}jpeg/2"]
        assert got["forbidden"][:2] == ("not_obtained", []) and "answered 403" in got["forbidden"][2]
        assert "served no stream" in got["page"][2] and "Error 1020" in got["page"][2]
        assert "ConnectError" in got["ad"][2] and "ad.model" in got["ad"][2]
        assert got["whole"][0] == "not_obtained"
        assert f"{CDN}jpeg/2" in asked  # only the head of the file was asked, never the whole of it

        jpeg = next(row for row in draft.rows if row.text == "jpeg")
        assert (await player.stream(jpeg.link, http, None, players.pacer))["progressive"] is True
        png = next(row for row in draft.rows if row.text == "png")
        assert (await player.stream(png.link, http, None, players.pacer))["playlist"] is True


async def test_a_file_ffprobe_cannot_open_says_what_ffprobe_said_not_that_it_holds_no_picture():
    refused = await measure_file_frame("http://127.0.0.1:9/adVideoLp/1.mp4", {})
    assert refused.reading.kind is Kind.NO_FILE
    assert "refused" in refused.reading.message.lower() and "127.0.0.1:9/adVideoLp" not in refused.reading.message


async def _height(value):
    return value


async def test_the_stream_of_an_episode_comes_from_the_parsers_its_line_allows_in_the_order_of_the_application():
    model = card_world()
    model.line("CO4K", "4K", parsers="9,5", headers='{"Referer": "https://app.model/"}')
    model.parses[(9, "CO4K_tok1")] = f"{CDN}c/index.m3u8"
    http, _asked = world(model, {f"{CDN}c/index.m3u8": PLAYLIST})
    async with http:
        player = AppPlayer(App99Client(profile(), lambda: http), registry=admitting("CO4K"))
        players = PlayerRegistry([player])
        link = player.link_of("app://banri/219017", "CO4K", "CO4K_tok1")
        got = await player.stream(link, http, None, players.pacer)
        assert got == {"stream_url": f"{CDN}c/index.m3u8", "headers": {"Referer": "https://app.model/"}, "progressive": False,
                       "playlist": True}
        # Parser 5 comes before 9 in the application's list: its empty answer is passed, 9 gives the stream.
        assert [int(fields["id"]) for path, fields in model.calls if path == "/app/vodParser"] == [5, 9]

        refused = player.link_of("app://banri/219017", "CO4K", "CO4K_tok2")
        with pytest.raises(StreamUnavailable) as caught:
            await player.stream(refused, http, None, players.pacer)
        assert "app_no_parser" in caught.value.reason and "5, 9" in caught.value.reason


async def test_the_session_is_opened_once_and_again_when_the_application_refuses_a_request_of_the_catalog():
    model = card_world()
    http, _asked = world(model)
    async with http:
        client = App99Client(profile(), lambda: http)
        await client.detail("219017")
        await client.detail("219017")
        assert model.paths() == ["/app/systemInit", "/app/userInfo", "/vod/detail", "/vod/detail"]
        model.refuse.add("/vod/detail")
        await client.detail("219017")
        assert model.paths()[4:] == ["/vod/detail", "/app/systemInit", "/app/userInfo", "/vod/detail"]
        assert model.sessions == 2


async def test_the_search_gives_the_cards_of_each_application_as_it_gives_them_each_a_source_by_its_address(tmp_path):
    model = App99Model()
    model.found = [{"id": 819312, "name": "万界独尊", "sub": "我身为剑", "year": "2013", "area": "内地", "class": "动画,国漫",
                    "remarks": "", "blurb": "　　《我是大医生》", "pic": "https://img.model/1.jpg"},
                   {"id": 219017, "name": "万界独尊", "sub": "Ten Thousand Worlds", "year": "2021", "remarks": "更新至第499集"},
                   {"name": "无号"}]
    model.pages = 3
    http, _asked = world(model)
    bus = Bus()
    sources = SourcesModule(bus, Database(str(migrated(tmp_path / "s.db"))), torrent_cache_dir=str(tmp_path / "cache"),
                            apps=[profile()])
    sources._http = http
    try:
        reply = await sources.on_apps_search(Envelope(topic="sources.apps.search", kind="query",
                                                      payload={"query": "万界独尊", "page": 2}))
        (results,) = reply["apps"]
        assert (results["app"], results["label"], results["page"], results["pages"], results["total"]) == ("banri", "半日", 2, 3, 3)
        assert [(card["id"], card["url"], card["sub"]) for card in results["cards"]] == [
            ("819312", "app://banri/819312", "我身为剑"), ("219017", "app://banri/219017", "Ten Thousand Worlds"), ("", "", "")]
        assert results["cards"][0]["genres"] == "动画,国漫" and results["cards"][0]["blurb"] == "《我是大医生》"
        assert [fields for path, fields in model.calls if path == "/vod/search"][0]["kw"] == "万界独尊"
        # The card picked is a source of the site of the application, by its address.
        resolved = sources.tables.resolve("app://banri/219017")
        assert (resolved.site, resolved.canonical_url) == ("banri", "app://banri/219017")


        # An application that cannot be asked gives its reason instead of cards; the search does not fail for it.
        down = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503, text="")))
        sources.apps[0].client = App99Client(profile(), lambda: down)
        failed = (await sources.on_apps_search(Envelope(topic="sources.apps.search", kind="query",
                                                        payload={"query": "x"})))["apps"][0]
        assert failed["cards"] == [] and '"code":"app_status"' in failed["reason"] and '"status":503' in failed["reason"]
        await down.aclose()
    finally:
        await http.aclose()


def test_an_unknown_protocol_of_an_application_stops_the_start(tmp_path):
    odd = AppProfile(name="odd", label="Odd", protocol="other", settings={"uuid": UUID})
    with pytest.raises(ValueError):
        SourcesModule(Bus(), Database(str(migrated(tmp_path / "s.db"))), apps=[odd])


async def test_the_name_a_site_gives_its_source_is_kept_with_its_table(tmp_path):
    from core.contracts import ReleaseTable, RowState, TableRow
    from modules.sources.repository import SourcesRepository

    repo = SourcesRepository(Database(str(migrated(tmp_path / "s.db"))))
    table = ReleaseTable(url="app://banri/219017", site="banri", names=["万界独尊"], title="半日 · 万界独尊 (2021)",
                         read_at="2026-11-10T10:00:00+00:00", complete=True,
                         rows=[TableRow(key="banri:CO4K:1", player="banri:CO4K", state=RowState.PLAYABLE, text="1")])
    await repo.save_table(table)
    assert (await repo.stored("app://banri/219017")).table.title == "半日 · 万界独尊 (2021)"
