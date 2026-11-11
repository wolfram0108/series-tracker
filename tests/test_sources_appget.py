"""The applications of the AppGet family (modules/sources/apps/appget.py): the codec on vectors made by another library
(the owner's prototype), and the client against a model of the application by its protocol — the list of hosts, the
card with its lines, the stream of an episode by each way the application has for it, the search."""
from __future__ import annotations

import json

import httpx
import pytest

from modules.sources.apps import AppError, AppProfile
from modules.sources.apps.appget import AppGetClient, decrypt, encrypt, lines_of

KEY, IV = "0123456789abcdef", "fedcba9876543210"
SITE = "https://site.model/hosts.txt"
DOWN, HOST = "https://down.model", "https://api.model"
PARSER = "https://parser.model/wmm.php?key=k&url="
PLAYLIST = "#EXTM3U\n#EXTINF:6,\nseg0.ts\n#EXT-X-ENDLIST\n"


def test_the_codec_is_aes_cbc_under_the_keys_of_the_profile_as_another_library_makes_it():
    assert encrypt("1731300000", KEY, IV) == "7+DtZGPGHm4Lej+jTBz9TQ=="
    sealed = "xAMSaaypaPLtk1ZwjWCVS8IJqaqgD4hD0m4h3D50p2rAjs0NpD+KNmvEA6AbhZaL"
    assert decrypt(sealed, KEY, IV) == '{"vod_id":"4303","имя":"师兄"}' and encrypt('{"vod_id":"4303","имя":"师兄"}', KEY, IV) == sealed


def profile(**more) -> AppProfile:
    return AppProfile("moli", "Moli", "appget", {"dataKey": KEY, "dataIv": IV, "site": SITE, **more})


def group(code: str, show: str, parse: str, urls: list[tuple[str, str, str]]) -> dict:
    return {"player_info": {"show": show, "parse": parse}, "urls": [
        {"name": name, "url": url, "from": code, "nid": place, "token": "", "parse_api_url": address}
        for place, (name, url, address) in enumerate(urls, start=1)]}


DETAIL = {"vod": {"vod_name": "万界独尊", "vod_year": "2021", "vod_sub": "Ten Thousand Worlds"}, "vod_play_list": [
    group("JD4K", "4K", "abc123", [("01", "JD-1", "abc123JD-1"), ("02", "JD-2", "abc123JD-2")]),
    group("NBY", "NB-蓝光-限", PARSER, [("1", "NBY-x1", PARSER + "NBY-x1"), ("2", "NBY-x2", PARSER + "NBY-x2")]),
    group("ffm3u8", "F蓝光-限", PARSER, [("第01集", "https://cdn.model/f/1/index.m3u8", PARSER + "https://cdn.model/f/1/index.m3u8")]),
    group("ffm3u8", "备用F-有广", "", [("第01集", "https://cdn.model/f/1/index.m3u8", "https://cdn.model/f/1/index.m3u8")]),
]}


class Model:
    """The application: the published list of its hosts (the first is down), its API and the parser of its lines."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object, dict]] = []
        self.parsed: list[str] = []
        self.answers = {"NBY-x1": httpx.Response(200, text=PLAYLIST),
                        "NBY-x2": httpx.Response(200, json={"code": 200, "url": "https://cdn.model/n/2.m3u8"}),
                        # A refusal names an address too, as the parsers of the applications do.
                        "https://cdn.model/f/1/index.m3u8": httpx.Response(200, json={
                            "code": 404, "msg": "解析失败", "url": "https://gone.model/f/1.m3u8"})}
        self.parse_answer: dict = {"json": json.dumps({"url": "https://cdn.model/jd/1.m3u8"})}

    def sealed(self, payload: dict) -> httpx.Response:
        return httpx.Response(200, json={"code": 1, "msg": "ok", "data": encrypt(json.dumps(payload, ensure_ascii=False), KEY, IV)})

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == SITE:
            return httpx.Response(200, text=f"﻿{DOWN}/\n{HOST}\nnot an address\n")
        if url.startswith(DOWN):
            raise httpx.ConnectError("down", request=request)
        if url.startswith("https://parser.model/"):
            token = request.url.params["url"]
            self.parsed.append(token)
            return self.answers[token]
        method = url.rsplit("/", 1)[-1]
        body = request.content.decode()
        self.calls.append((method, body if method == "vodParse" else json.loads(body), dict(request.headers)))
        if method == "vodDetail":
            return self.sealed(DETAIL)
        if method == "vodParse":
            return self.sealed(self.parse_answer)
        if method == "searchList":
            return self.sealed({"search_list": [{"vod_id": 4307, "vod_name": "万界独尊", "vod_year": "2021",
                                                 "vod_remarks": "更新至499集", "type_name": "国产动漫"}]})
        return httpx.Response(404)


@pytest.fixture
async def world():
    model = Model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(model.handle)) as http:
        yield model, AppGetClient(profile(), lambda: http, clock=lambda: 1731300000.0)


def test_a_code_a_card_gives_twice_names_its_second_line_apart():
    found = [(line.code, line.name, line.listed, [item.label for item in line.items]) for line, _asked in lines_of(DETAIL)]
    assert found == [("JD4K", "4K", False, ["01", "02"]), ("NBY", "NB-蓝光-限", False, ["1", "2"]),
                     ("ffm3u8", "F蓝光-限", False, ["第01集"]), ("ffm3u8.2", "备用F-有广", True, ["第01集"])]


async def test_the_card_is_asked_of_the_next_host_when_the_first_listed_is_down(world):
    model, client = world
    card = await client.card("4307")
    assert card.names == ["万界独尊", "Ten Thousand Worlds"] and card.year == "2021"
    assert [line.code for line in card.lines] == ["JD4K", "NBY", "ffm3u8", "ffm3u8.2"]
    method, body, headers = model.calls[0]
    assert (method, body) == ("vodDetail", {"vod_id": "4307"})
    assert headers["app-api-verify-time"] == "1731300000" and "app-api-verify-sign" not in headers


async def test_the_stream_of_an_episode_is_what_its_way_gives(world):
    model, client = world
    await client.card("4307")
    # A playlist by the path of its address is the stream itself.
    direct = await client.resolve("ffm3u8.2", "https://cdn.model/f/1/index.m3u8", "4307")
    assert direct.address == "https://cdn.model/f/1/index.m3u8" and direct.headers["User-Agent"]
    # A parser that answers with the playlist: its address is the stream. One that names the stream: that address.
    assert (await client.resolve("NBY", "NBY-x1", "4307")).address == PARSER + "NBY-x1"
    named = await client.resolve("NBY", "NBY-x2", "4307")
    assert named.address == "https://cdn.model/n/2.m3u8" and named.headers == {}
    # A refusal in words is the parser's answer about the episode.
    with pytest.raises(AppError) as refused:
        await client.resolve("ffm3u8", "https://cdn.model/f/1/index.m3u8", "4307")
    assert "解析失败" in refused.value.reason and not refused.value.transient


async def test_an_episode_without_an_address_of_a_parser_is_parsed_by_the_application_signed_by_the_time(world):
    model, client = world
    await client.card("4307")
    stream = await client.resolve("JD4K", "JD-1", "4307")
    assert stream.address == "https://cdn.model/jd/1.m3u8" and stream.headers == {}
    method, body, headers = model.calls[-1]
    assert method == "vodParse" and headers["app-api-verify-sign"] == encrypt("1731300000", KEY, IV)
    assert body.startswith("parse_api=abc123&url=") and body.endswith("&token=")
    # The application names no stream and says nothing: a failure of the way, asked again by the policy of the players.
    model.parse_answer = {"json": ""}
    with pytest.raises(AppError) as failed:
        await client.resolve("JD4K", "JD-2", "4307")
    assert failed.value.transient
    # A refusal of the application's own parser, with an address in it: the words are the answer, the address no stream.
    model.parse_answer = {"json": json.dumps({"code": 403, "msg": "今日次数已达上限", "url": "https://gone.model/jd/3.m3u8"})}
    with pytest.raises(AppError) as refused:
        await client.resolve("JD4K", "JD-3", "4307")
    assert "今日次数已达上限" in refused.value.reason and not refused.value.transient


async def test_the_card_is_asked_again_for_the_parser_of_an_episode_the_client_no_longer_holds(world):
    """The link of a row is the card, the line and the token: after a start of the service the stream of the episode is
    asked with what its card says now."""
    model, client = world
    stream = await client.resolve("NBY", "NBY-x1", "4307")
    assert stream.address == PARSER + "NBY-x1"
    assert [method for method, _body, _headers in model.calls] == ["vodDetail"]
    await client.resolve("NBY", "NBY-x2", "4307")
    assert [method for method, _body, _headers in model.calls] == ["vodDetail"]


async def test_the_search_gives_the_cards_as_the_application_gives_them(world):
    model, client = world
    page = await client.search_page("万界独尊", 1)
    assert [(card["id"], card["name"], card["year"], card["remarks"]) for card in page.cards] == \
        [(4307, "万界独尊", "2021", "更新至499集")]
    assert model.calls[-1][:2] == ("searchList", {"type_id": 0, "keywords": "万界独尊", "page": 1})
