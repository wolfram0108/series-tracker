"""The application 布布 (modules/sources/apps/yunduo.py): the request for a stream on a vector made by another library (the
owner's prototype), and the client against a model of the application by its protocol — the card with its lines, the
stream of an episode, a refusal, the search."""
from __future__ import annotations

import httpx
import pytest

from modules.sources.apps import AppError, AppProfile
from modules.sources.apps.appdrama import field
from modules.sources.apps.yunduo import YunDuoClient, signature, stream_request

HOST = "https://api.model"
SETTINGS = {"label": "布布", "protocol": "yunduo", "host": HOST, "id": "finger-1", "dev": "dev-1", "sk": "secret-1",
            "web_sign": "sign-1", "x_client": "client-1", "ua": "Model/1.0"}
STAMP, NONCE = 1731300000000, "00112233445566778899aabbccddeeff"
SIGNED = "61544B6E8BF1FC37B26184ED10E297A41C2AAA73017D811C7FBB70793DB2BE73"
REQUEST = ("0a0a50414e312d746f6b656e120450414e3118808296ccb132222030303131323233333434353536363737383839396161626263636464"
           "656566662a4036313534344236453842463146433337423236313834454431304532393741343143324141413733303137443831314337"
           "46424237303739334442324245373332056465762d313801")

DETAIL = {"code": 200, "msg": "获取成功", "data": [{
    "vod_id": 209, "vod_name": "万界独尊", "vod_sub": "Ten Thousand Worlds", "vod_year": "2021",
    "vod_play_from": "PAN1$$$zijianm3u8$$$empty$$$PAN1",
    "vod_play_url": "第1集$PAN1-token#第2集$PAN1-two$$$第1集$zj-1$$$$$$第1集$PAN1-again"}],
    "vodplayer": [{"from": "PAN1", "show": "盘一"}, {"from": "zijianm3u8", "show": "ZJ4K"}, {"from": "CO4K", "show": "CO4K"}]}


class Model:
    """The application: its catalogue and its answer about the stream of an episode."""

    def __init__(self) -> None:
        self.asked: list[httpx.Request] = []
        self.streams = {"PAN1-token": field(1, 1) + field(2, "ok") + field(3, "https://cdn.model/p/1/index.m3u8?sign=1"),
                        # A refusal names an address too.
                        "zj-1": field(1, 403) + field(2, "今日次数已达上限") + field(3, "https://gone.model/zj/1.m3u8"),
                        "PAN1-two": field(1, 0)}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        path = request.url.path.removeprefix("/api.php/web")
        if path == "/vod/get_detail":
            return httpx.Response(200, json=DETAIL if request.url.params["vod_id"] == "209" else {"code": 200, "data": []})
        if path == "/search/index":
            return httpx.Response(200, json={"code": 200, "msg": "success", "total": 1, "pageCount": 1, "data": [
                {"vod_id": 209, "vod_name": "万界独尊", "vod_year": "2021", "vod_area": "大陆", "vod_class": "动漫",
                 "vod_remarks": "第497集", "vod_pic": "https://pic.model/209.jpg"}]})
        if path == "/decode/url":
            token = next(key for key in self.streams if key.encode() in request.content)
            return httpx.Response(200, content=self.streams[token])
        return httpx.Response(404)


@pytest.fixture
async def world():
    model = Model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(model.handle)) as http:
        yield model, YunDuoClient(AppProfile("yunduo", "布布", "yunduo", SETTINGS), lambda: http,
                                  clock=lambda: STAMP / 1000, nonce=lambda: NONCE)


def test_the_request_for_a_stream_is_what_another_library_makes_of_the_same_profile():
    assert signature(SETTINGS, STAMP, NONCE) == SIGNED
    assert stream_request(SETTINGS, "PAN1", "PAN1-token", STAMP, NONCE).hex() == REQUEST


async def test_the_card_lists_its_lines_in_its_order_with_the_names_the_application_shows(world):
    model, client = world
    card = await client.card("209")
    assert card.names == ["万界独尊", "Ten Thousand Worlds"] and card.year == "2021"
    # A line without an episode is not listed; a code the card gives twice names its second line apart.
    assert [(line.code, line.name, line.listed, [(item.place, item.label, item.token) for item in line.items])
            for line in card.lines] == [
        ("PAN1", "盘一", False, [(1, "第1集", "PAN1-token"), (2, "第2集", "PAN1-two")]),
        ("zijianm3u8", "ZJ4K", False, [(1, "第1集", "zj-1")]),
        ("PAN1.2", "盘一", False, [(1, "第1集", "PAN1-again")])]
    asked = model.asked[0]
    assert (asked.headers["x-client"], asked.headers["web-sign"], asked.headers["user-agent"]) == ("client-1", "sign-1", "Model/1.0")
    with pytest.raises(AppError) as absent:
        await client.card("1")
    assert "app_title_absent" in absent.value.reason


async def test_the_stream_of_an_episode_is_asked_by_the_signed_request_and_played_from_its_origin(world):
    model, client = world
    stream = await client.resolve("PAN1", "PAN1-token")
    assert stream.address == "https://cdn.model/p/1/index.m3u8?sign=1"
    assert stream.headers == {"User-Agent": "Model/1.0", "Referer": "https://cdn.model"}
    assert model.asked[-1].content.hex() == REQUEST and model.asked[-1].headers["content-type"] == "application/x-protobuf"
    # The second line of a code is asked by the code the application knows; the card is read for it when it is not held.
    model.streams["PAN1-again"] = model.streams["PAN1-token"]
    await client.resolve("PAN1.2", "PAN1-again", "209")
    assert model.asked[-2].url.path.endswith("/vod/get_detail") and field(2, "PAN1") in model.asked[-1].content


async def test_a_refusal_in_words_is_the_answer_about_the_episode_and_its_address_is_no_stream(world):
    _model, client = world
    with pytest.raises(AppError) as refused:
        await client.resolve("zijianm3u8", "zj-1")
    assert "今日次数已达上限" in refused.value.reason and not refused.value.transient
    # No address and no words: a failure of the way, asked again by the policy of the players.
    with pytest.raises(AppError) as failed:
        await client.resolve("PAN1", "PAN1-two")
    assert failed.value.transient


async def test_the_search_gives_the_cards_as_the_application_gives_them(world):
    _model, client = world
    page = await client.search_page("万界独尊", 1)
    assert (page.pages, page.total) == (1, 1)
    assert page.cards == [{"id": 209, "name": "万界独尊", "sub": "", "year": "2021", "area": "大陆", "class": "动漫",
                           "remarks": "第497集", "blurb": "", "pic": "https://pic.model/209.jpg"}]
