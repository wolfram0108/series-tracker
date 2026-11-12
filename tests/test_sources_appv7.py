"""The applications of the AppV7 family (modules/sources/apps/appv7.py): the client against a model of the API by its
protocol — the sealed request and its headers, the card whose lines name their parsers, the secret method, the container
of an answer, a refusal that names an address, the search."""
from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest

from modules.sources.apps import AppError, AppProfile
from modules.sources.apps.appv7 import CONTAINER_MARK, AppV7Client, api_key, seal, unseal
from modules.sources.apps.gulu import cbc

API, PARSER = "https://api.model/v7/logs", "https://parser.model/p?url="
SETTINGS = {"label": "小猪", "protocol": "appv7", "api": API, "package": "pk", "build_time": "1700000000", "salt": "s",
            "version": "1.0", "version_number": "9", "platform_version": "13", "card_identity": json.dumps({"app": "x"}),
            "ua": "Dart/3.13 (dart:io)"}
KEY = {k: SETTINGS[k] for k in ("package", "build_time", "salt")}

DETAIL = {"vod_info": {"vod_id": 90331, "vod_name": "无上神帝", "vod_sub": "", "vod_year": "2021", "vod_url_with_player": [
    {"code": "JD4K", "name": "JD4K-可下载", "url": "第1集$jd-1#第2集$jd-2", "parse_api": PARSER, "parse_secret": True,
     "headers": {"Referer": "https://player.model/"}},
    {"code": "zijianm3u8", "name": "蓝光4K", "url": "第1集$zijian_1", "parse_api": PARSER, "parse_secret": False},
    {"code": "zijianm3u8", "name": "蓝光4K 2", "url": "第1集$zijian_2", "parse_api": PARSER, "parse_secret": False},
    {"code": "bfzym3u8", "name": "超清", "url": "第1集$https://public.model/1/index.m3u8"},
    {"code": "rose", "name": "空", "url": ""}]}}


class Model:
    def __init__(self) -> None:
        self.asked: list[httpx.Request] = []
        self.bodies: list[dict] = []
        self.parsed = {"sealed-jd-1": {"code": 200, "url": "https://store.model/jd/1.m3u8", "header": {"Origin": "https://o.model"}},
                       "zijian_1": {"code": -7, "msg": "今日解析次数已达上限", "url": "https://gone.model/1.m3u8"},
                       "zijian_9": {"code": 200, "url": "https://store.model/z/9.m3u8"}}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=self.parsed[parse_qs(request.url.query.decode())["url"][0]])
        wire = base64.b64decode(request.content)
        body = json.loads(cbc(wire[16:], api_key(KEY), wire[:16], decrypt=True))
        self.bodies.append(body)
        if body["method"] == "video_detail":
            found = body["data"]["id"] == 90331
            return httpx.Response(200, json={"code": 1, "data": DETAIL} if found else {"code": 0, "msg": "视频不存在", "data": None})
        if body["method"] == "search":
            return httpx.Response(200, json={"code": 1, "data": [{"vod_id": 90331, "vod_name": "无上神帝", "vod_year": "2021",
                                                                  "vod_remarks": "更新至650集"}]})
        if body["method"] == "secret_parse_api_url":
            return httpx.Response(200, json={"code": 1, "data": "sealed-" + body["data"]["url"]})
        return httpx.Response(404)


@pytest.fixture
async def world():
    model = Model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(model.handle)) as http:
        yield model, AppV7Client(AppProfile("xiaozhu", "小猪", "appv7", SETTINGS), lambda: http, clock=lambda: 1731300000.0,
                                 iv=lambda: bytes(range(16)))


def container(text: str, iv: bytes) -> str:
    """An answer the way the family hides it: the head of the sealed text as a run of numbers (the client's own inverse)."""
    raw = iv + cbc(text.encode(), api_key(KEY), iv)
    head, tail = raw[:24], raw[24:]
    shifted = [(byte + (11 if place % 2 == 0 else 4 if place % 3 == 0 else 2 if place % 5 == 0 else 3)) & 255
               for place, byte in enumerate(head)]
    numbers = base64.b64encode("".join(f"0x{value}" for value in reversed(shifted)).encode()).decode()
    return (numbers + base64.b64encode(tail).decode() + CONTAINER_MARK
            + base64.b64encode(str(len(numbers)).encode()).decode())


def test_a_request_is_sealed_under_the_key_of_the_build_and_an_answer_is_json_or_a_container():
    iv = bytes(range(16))
    wire = base64.b64decode(seal(SETTINGS, {"method": "search"}, iv))
    assert wire[:16] == iv and json.loads(cbc(wire[16:], api_key(KEY), iv, decrypt=True)) == {"method": "search"}
    sealed = {"code": 1, "data": [{"vod_name": "无上神帝"}]}
    assert unseal(SETTINGS, json.dumps(sealed)) == sealed
    # The same answer in the container, even with the 'equal sign' padding of its parts and a break of lines in the text.
    for text in (container(json.dumps(sealed, ensure_ascii=False), iv), container(json.dumps(sealed), bytes(16))):
        assert unseal(SETTINGS, text[:50] + "\n" + text[50:]) == sealed


async def test_a_request_goes_with_the_headers_of_the_build_and_the_card_with_the_identity_of_the_profile(world):
    model, client = world
    await client.card("90331")
    headers = model.asked[0].headers
    assert (headers["user-agent"], headers["pk-id"], headers["build-time"], headers["version-number"], headers["platform"]) == (
        "Dart/3.13 (dart:io)", "pk", "1700000000", "9", "android")
    assert model.bodies[0] == {"method": "video_detail", "data": {"token": "", "id": 90331, "app": "x"}, "csrf": 1731300000000}


async def test_the_card_lists_every_line_with_episodes_and_names_a_repeated_code_by_its_place(world):
    _model, client = world
    card = await client.card("90331")
    assert card.names == ["无上神帝"] and card.year == "2021"
    assert [(line.code, line.name, line.listed, [(item.label, item.token) for item in line.items]) for line in card.lines] == [
        ("JD4K", "JD4K-可下载", False, [("第1集", "jd-1"), ("第2集", "jd-2")]),
        ("zijianm3u8", "蓝光4K", False, [("第1集", "zijian_1")]), ("zijianm3u8.2", "蓝光4K 2", False, [("第1集", "zijian_2")]),
        ("bfzym3u8", "超清", True, [("第1集", "https://public.model/1/index.m3u8")])]
    with pytest.raises(AppError) as absent:
        await client.card("1")
    assert "视频不存在" in absent.value.reason


async def test_the_stream_of_a_secret_line_is_its_token_sealed_by_the_application_and_given_to_its_parser(world):
    model, client = world
    await client.card("90331")
    stream = await client.resolve("JD4K", "jd-1", "90331")
    assert stream.address == "https://store.model/jd/1.m3u8"
    assert stream.headers == {"Referer": "https://player.model/", "Origin": "https://o.model"}
    assert model.bodies[-1] == {"method": "secret_parse_api_url", "data": {"token": "", "url": "jd-1", "player_code": "JD4K"},
                                "csrf": 1731300000000}
    parser = model.asked[-1]
    assert parser.headers["accept"] == "*/*" and parser.headers["user-agent"] == "Dart/3.11 (dart:io)"


async def test_a_line_without_a_parser_gives_its_addresses_and_the_card_is_asked_again_for_a_line_it_does_not_hold(world):
    model, client = world
    # No card read before: the row of the line is of a card, and the client asks it for the line's player.
    stream = await client.resolve("bfzym3u8", "https://public.model/1/index.m3u8", "90331")
    assert stream.address == "https://public.model/1/index.m3u8"
    assert [body["method"] for body in model.bodies] == ["video_detail"]
    with pytest.raises(AppError) as unknown:
        await client.resolve("ZZZ", "x", "90331")
    assert "app_no_parser" in unknown.value.reason


async def test_a_refusal_of_the_parser_that_names_an_address_is_its_answer_and_no_stream(world):
    _model, client = world
    await client.card("90331")
    with pytest.raises(AppError) as refused:
        await client.resolve("zijianm3u8", "zijian_1", "90331")
    assert "今日解析次数已达上限" in refused.value.reason and refused.value.daily_quota and not refused.value.transient


async def test_a_status_refusal_keeps_the_words_of_the_parser_and_tells_its_daily_quota():
    model = Model()

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(429, json={"code": 429, "msg": "当天限额已超限（今日已播放 25002/25000 次）"})
        return model.handle(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = AppV7Client(AppProfile("xiaozhu", "小猪", "appv7", SETTINGS), lambda: http)
        await client.card("90331")
        with pytest.raises(AppError) as limit:
            await client.resolve("zijianm3u8", "zijian_2", "90331")
    assert limit.value.status == 429 and limit.value.transient and limit.value.daily_quota


async def test_an_answer_that_is_not_the_api_is_told_apart_from_a_refusal():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(200, text="not json"))) as http:
        client = AppV7Client(AppProfile("xiaozhu", "小猪", "appv7", SETTINGS), lambda: http)
        with pytest.raises(AppError) as answer:
            await client.search_page("x", 1)
    assert "app_answer_broken" in answer.value.reason


async def test_the_search_gives_the_cards_as_the_application_gives_them(world):
    _model, client = world
    page = await client.search_page("无上神帝", 1)
    assert (page.pages, page.total) == (None, None)
    assert page.cards[0]["id"] == 90331 and page.cards[0]["remarks"] == "更新至650集"


async def test_a_token_of_another_application_is_parsed_by_the_line_the_profile_or_a_card_gave_without_its_own_card():
    model = Model()
    seeded = {**SETTINGS, "parsers": json.dumps({"zijianm3u8": {"code": "zijianm3u8", "parse_api": PARSER, "parse_secret": False}})}
    async with httpx.AsyncClient(transport=httpx.MockTransport(model.handle)) as http:
        client = AppV7Client(AppProfile("mtq", "蒙太奇", "appv7", seeded), lambda: http)
        assert (await client.resolve("zijianm3u8", "zijian_9", "")).address == "https://store.model/z/9.m3u8"
        assert model.bodies == []  # no card was read, no secret method asked
        bare = AppV7Client(AppProfile("mtq", "蒙太奇", "appv7", SETTINGS), lambda: http)
        with pytest.raises(AppError) as unknown:
            await bare.resolve("zijianm3u8", "zijian_9", "")
        assert "app_no_parser" in unknown.value.reason
        # A card read publishes its lines: the parser is the line's from then on, whichever card the token is of.
        await bare.card("90331")
        assert (await bare.resolve("zijianm3u8", "zijian_9", "")).address == "https://store.model/z/9.m3u8"
