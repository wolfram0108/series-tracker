"""The applications of the AppDrama family (modules/sources/apps/appdrama.py): the codec on vectors made by the owner's
prototype, the session and the requests against a model of the server by its protocol (tests/appdrama_model.py), the
card as lines, the stream of an episode, the search — and a line one player whichever application lists it."""
from __future__ import annotations

import base64
import json
import random

import httpx
import pytest

from core.http_limits import BoundedTransport
from modules.sources.apps import AppError
from modules.sources.apps.app99 import App99Client
from modules.sources.apps.appdrama import (AppDramaClient, answer_of, cbc_encrypt_hex, detail_of, ecb_encrypt, field,
                                           page_of, play_of)
from modules.sources.apps.lines import LineRegistry
from modules.sources.players.app_lines import AppLines, AppPlayer
from modules.sources.players.registry import PlayerRegistry
from modules.sources.sites import ReadContext
from modules.sources.sites.apps import AppSite
from app99_model import App99Model, Card, Line, profile as app99_profile
from appdrama_model import HOST, DramaModel, Video

PLAYLIST = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nseg0.ts\n#EXT-X-ENDLIST\n"
CDN = "https://cdn.model/"

# --- the codec, on vectors of the prototype (sourcekit/appdrama_codec.py) ----------------------------------------------


def test_the_codec_is_the_one_of_the_application_on_the_vectors_of_the_prototype():
    assert ecb_encrypt("searchKeys=万界独尊&page=1&pagesize=501700000000000", "0123456789abcdef") == \
        "u06yShKGu35jL6NJYmgwiWSfEg5u0jXr+IHtEDlcDAylVodW+be0esxtYR8tl9a+BKLjHGVFxOzWR3zHFAVzXw=="
    assert cbc_encrypt_hex('{"country":"CN","v":1}', "0123456789abcdef", "0123456789abcdef") == \
        "d9ae6f3eaa613e177ba66b9eecf46bd4da1dfb25974631f5602397a621f5b794"
    secure = (field(1, "rAnDoM12abcdefghijkl") + field(2, "mnopqrst") + field(3, "FAKEFAKEFAKEFAKEFAK=") + field(4, 1700000000000)
              + field(5, "rAnDoM1="))
    assert base64.b64encode(secure).decode() == \
        "ChRyQW5Eb00xMmFiY2RlZmdoaWprbBIIbW5vcHFyc3QaFEZBS0VGQUtFRkFLRUZBS0VGQUs9IIDQlf+8MSoIckFuRG9NMT0="
    zone = field(1, 1700000000000) + field(2, "c2lnbg==") + field(3, "fake1fake1fake1=") + field(4, "rnd1rnd1rnd1rnd=") \
        + field(5, "fake2fake2fake2=")
    assert base64.b64encode(zone).decode() == \
        "CIDQlf+8MRIIYzJsbmJnPT0aEGZha2UxZmFrZTFmYWtlMT0iEHJuZDFybmQxcm5kMXJuZD0qEGZha2UyZmFrZTJmYWtlMj0="

    code, message, data = answer_of(base64.b64decode(
        "CMgBEgdzdWNjZXNzGpEBILjwGDIG566A5LuLSgzkuIfnlYzni6zlsIqQAeUP6gEqCAsSATEiC3ppamlhbl90b2sxSgp6aWppYW5tM3U4Ugjok53lhYk0S2gB6gF"
        "DCAwSATEiJGh0dHBzOi8vYzEucnJjZG5iZjQuY29tL3gvaW5kZXgubTN1OEoIYmZ6eW0zdThSCkLotoXpq5jmuIVwAQ=="))
    detail = detail_of(data)
    assert (code, message, detail["id"], detail["name"], detail["intro"], detail["year"]) == \
        (200, "success", 407608, "万界独尊", "简介", 2021)
    assert detail["videos"] == [
        {"title": "1", "path": "zijian_tok1", "source": "zijianm3u8", "source_cn": "蓝光4K", "is_vip": False},
        {"title": "1", "path": "https://c1.rrcdnbf4.com/x/index.m3u8", "source": "bfzym3u8", "source_cn": "B超高清", "is_vip": True}]
    _code, _message, play = answer_of(base64.b64decode(
        "CMgBGj4KG2h0dHBzOi8vY2RuLm1vZGVsL3BsYXkubTN1OCgBMh0KB1JlZmVyZXISEmh0dHBzOi8vYXBwLm1vZGVsLw=="))
    assert play_of(play) == {"play_url": "https://cdn.model/play.m3u8", "headers": {"Referer": "https://app.model/"}, "msg": ""}
    _code, _message, page = answer_of(base64.b64decode("CMgBGisKKRi48BgqDOS4h+eVjOeLrOWwimoS5pu05paw6Iez56ysNDk56ZuGcOUP"))
    assert [(card["id"], card["name"], card["remarks"], card["year"]) for card in page_of(page)] == \
        [(407608, "万界独尊", "更新至第499集", 2021)]


# --- the client against the model of the server ---------------------------------------------------------------------

def world(*models, playlists: dict[str, str] | None = None) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for model in models:
            if url.startswith(getattr(model, "host_prefix", HOST)) or url.startswith("https://navigator.model"):
                return model.handle(request)
        text = (playlists or {}).get(url)
        return httpx.Response(200, text=text) if text is not None else httpx.Response(404)
    return httpx.AsyncClient(transport=BoundedTransport(httpx.MockTransport(handle)))


def drama_world() -> DramaModel:
    model = DramaModel()
    model.cards[407608] = ("万界独尊", 2021, [
        Video("zijianm3u8", "蓝光4K", "1", "zijian_tok1"), Video("qq", "QQ蓝光", "1", "https://v.qq.com/x/1.html"),
        Video("zijianm3u8", "蓝光4K", "2", "zijian_tok2"), Video("qq", "QQ蓝光", "2", "https://v.qq.com/x/2.html", vip=True)])
    model.plays[("zijianm3u8", "zijian_tok1")] = (f"{CDN}z1/index.m3u8", {"Referer": "https://drama.model/"})
    model.plays[("qq", "https://v.qq.com/x/1.html")] = (f"{CDN}q1/index.m3u8", {})
    return model


async def test_the_session_opens_by_the_key_of_the_zone_at_the_host_the_navigator_names_and_a_card_is_its_lines():
    model = drama_world()
    async with world(model) as http:
        client = AppDramaClient(model.profile(), lambda: http, rng=random.Random(7))
        card = await client.card("407608")
        assert (card.names, card.year) == (["万界独尊"], "2021")
        assert [(line.code, line.name, line.listed, [(each.place, each.label, each.token) for each in line.items])
                for line in card.lines] == [
            ("zijianm3u8", "蓝光4K", False, [(1, "1", "zijian_tok1"), (2, "2", "zijian_tok2")]),
            ("qq", "QQ蓝光", False, [(1, "1", "https://v.qq.com/x/1.html"), (2, "2", "https://v.qq.com/x/2.html")])]
        stream = await client.resolve("zijianm3u8", "zijian_tok1")
        assert (stream.address, stream.headers) == (f"{CDN}z1/index.m3u8", {"Referer": "https://drama.model/"})
        # The application gives no stream of an episode: the reason with its message, not a guess.
        with pytest.raises(AppError) as caught:
            await client.resolve("qq", "https://v.qq.com/x/2.html")
        assert '"code":"app_no_stream"' in caught.value.reason and "解析失败" in caught.value.reason
        assert not caught.value.transient  # a refusal in words is the answer about the episode
        # No address and no words: the application failed on the way (as under load), and the episode is asked again.
        model.silent.add(("zijianm3u8", "zijian_tok2"))
        with pytest.raises(AppError) as caught:
            await client.resolve("zijianm3u8", "zijian_tok2")
        assert '"code":"app_no_stream"' in caught.value.reason and caught.value.transient
        assert model.zones == 1 and model.calls[0] == ("/api/proto/v5/drama/getDetail", {"id": "407608"})
        assert model.calls[1] == ("/api/proto/v5/videoUsableUrl", {"vodPlayFrom": "zijianm3u8", "playUrl": "zijian_tok1"})
        # A card the application does not know is a refusal with its message.
        with pytest.raises(AppError) as caught:
            await client.card("1")
        assert '"code":"app_refused"' in caught.value.reason and "not found" in caught.value.reason


async def test_the_fallback_host_serves_when_the_navigator_does_not_and_the_search_gives_its_cards():
    model = drama_world()
    model.navigator_down = True
    model.found = [(407608, "万界独尊", 2021, "更新至第499集"), (609260, "万界独尊", 2026, "全21集")]
    async with world(model) as http:
        client = AppDramaClient(model.profile(), lambda: http)
        page = await client.search_page("万界独尊", 1)
    assert [(card["id"], card["name"], card["year"], card["remarks"]) for card in page.cards] == \
        [(407608, "万界独尊", "2021", "更新至第499集"), (609260, "万界独尊", "2026", "全21集")]
    assert model.calls == [("/api/proto/v5/drama/search", {"searchKeys": "万界独尊", "page": "1", "pagesize": "50"})]


# --- a line is one player whichever application lists it -------------------------------------------------------------

async def test_a_line_of_two_applications_is_one_player_and_each_row_is_asked_of_its_own_application():
    drama = drama_world()
    app99 = App99Model(parsers=[9])
    app99.line("qq", "QQ", parsers="9")
    app99.cards[219017] = Card(219017, "万界独尊", lines=[Line("qq", [("第1集", "https://v.qq.com/x/1.html")])])
    app99.parses[(9, "https://v.qq.com/x/1.html")] = f"{CDN}a1/index.m3u8"
    app99.host_prefix = "https://app99.model"
    playlists = {f"{CDN}{name}/index.m3u8": PLAYLIST for name in ("z1", "q1", "a1")}

    async def measure(_http, head, _referer, _headers=None):
        return {"z1": 2160, "q1": 1080, "a1": 720}[head.segment.removeprefix(CDN).split("/")[0]]

    async with world(drama, app99, playlists=playlists) as http:
        huayi = AppPlayer(AppDramaClient(drama.profile(), lambda: http), measure,
                          registry=LineRegistry(frozenset({"zijianm3u8", "qq"})))
        banri = AppPlayer(App99Client(app99_profile(), lambda: http), measure, registry=LineRegistry(frozenset({"qq"})))
        lines = AppLines([banri, huayi])
        players = PlayerRegistry([lines])
        from_drama = await AppSite(huayi, lines).read("app://huayi/407608", ReadContext(http, lambda *_: None))
        from_app99 = await AppSite(banri, lines).read("app://banri/219017", ReadContext(http, lambda *_: None))

    assert [(row.key, row.player, row.player_label, row.text, row.state.value, row.heights) for row in from_drama.rows] == [
        ("huayi:zijianm3u8:1", "line:zijianm3u8", "zijianm3u8", "1", "playable", [2160]),
        ("huayi:zijianm3u8:2", "line:zijianm3u8", "zijianm3u8", "2", "absent", []),
        ("huayi:qq:1", "line:qq", "qq", "1", "playable", [1080]),
        ("huayi:qq:2", "line:qq", "qq", "2", "absent", [])]
    assert [(row.key, row.player, row.heights) for row in from_app99.rows] == [("banri:qq:1", "line:qq", [720])]
    assert from_drama.title == "华谊 · 万界独尊 (2021)" and from_drama.names == ["万界独尊"]
    # One player of the line in both applications, in the order of sources once.
    assert lines.codes() == ["qq", "zijianm3u8"] and players.get("line:qq") is lines
    assert players.video_of("line:qq", from_drama.rows[2].link) == "https://v.qq.com/x/1.html"
