"""The application 米诺 (modules/sources/apps/appmn.py): the client against a model of the application by its protocol —
the card with its lines, the guest session, the ticket of an episode and its parser, a session that ended, a refusal, the
search."""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from modules.sources.apps import AppError, AppProfile
from modules.sources.apps.appmn import AppMnClient, parser_address, ticket_data

HOST = "https://api.model"
SETTINGS = {"label": "米诺", "protocol": "appmn", "host": HOST, "client_pub": "pub-1", "version": "1.0.13",
            "device_brand": "Brand", "device_manufacturer": "Maker", "device_model": "Model 8", "sig_src": "java",
            "so_version": "13", "device_id": "0123456789abcdef"}

DETAIL = {"code": 1, "msg": "success", "data": {"vod_info": {
    "vod_id": 1522016, "vod_name": "万界独尊", "vod_sub": "", "vod_year": "2021", "vod_url_with_player": [
        {"code": "duanju", "name": "蓝光4K【推荐】", "url": "01$dj-1#02$dj-2"},
        {"code": "zijianm3u8", "name": "4K", "url": "01$zj-1"},
        {"code": "rose", "name": "蓝光", "url": ""}]}}}


def ticket(token: str, api: str = "/api/v1/vod/parse?url=") -> str:
    claims = {"v": 1, "typ": "play_ticket", "data": {"parse_api": api, "url": token, "parse_secret": "kept as it is"}}
    return base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".signature"


class Model:
    """The application: its catalogue, its sessions (each lives `life` seconds), its tickets and its parser."""

    def __init__(self) -> None:
        self.asked: list[httpx.Request] = []
        self.sessions: list[str] = []
        self.ended: set[str] = set()
        self.life = 600
        self.parsed = {"dj-1": {"code": 200, "msg": "获取成功", "url": "https://store.model/dj/1.mp4?sign=1", "type": "mp4"},
                       # A quota of the parser of a line: a refusal that names an address too.
                       "zj-1": {"code": -7, "msg": "今日解析次数已达上限", "url": "https://gone.model/zj/1.m3u8"}}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        path = request.url.path
        if path == "/api/v1/security/init":
            self.sessions.append(f"sid_{len(self.sessions) + 1}")
            return httpx.Response(200, json={"code": 1, "msg": "success", "data": {
                "session_id": self.sessions[-1], "expires_in": self.life}})
        if path == "/api/v1/video/detail":
            found = request.url.params["id"] == "1522016"
            return httpx.Response(200, json=DETAIL if found else {"code": 0, "msg": "视频不存在", "data": None})
        if path == "/api/v1/search":
            return httpx.Response(200, json={"code": 1, "msg": "success", "data": {"total": 1, "total_pages": 1, "hits": [
                {"vod_id": 1522016, "vod_name": "万界独尊", "vod_year": "2021", "vod_area": "内地", "vod_class": "动漫",
                 "vod_remarks": "更新至497集", "vod_blurb": "…", "vod_pic": "https://pic.model/1.jpg"}]}})
        if path == "/api/v1/vod/ticket":
            session = request.headers.get("x-session-id", "")
            if session not in self.sessions or session in self.ended:
                return httpx.Response(200, json={"code": 401, "msg": "session expired", "data": None})
            return httpx.Response(200, json={"code": 1, "msg": "success", "data": {
                "ticket": ticket(json.loads(request.content)["token"])}})
        if path == "/api/v1/vod/parse":
            return httpx.Response(200, json=self.parsed[request.url.params["url"]])
        return httpx.Response(404)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.asked]


@pytest.fixture
async def world():
    model, now = Model(), [1731300000.0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(model.handle)) as http:
        yield model, AppMnClient(AppProfile("appmn", "米诺", "appmn", SETTINGS), lambda: http, clock=lambda: now[0]), now


def test_a_ticket_names_the_parser_of_its_episode_and_the_address_it_is_asked_at():
    data = ticket_data(ticket("dj/1"))
    assert (data["parse_api"], data["url"]) == ("/api/v1/vod/parse?url=", "dj/1")
    # A parser of the host of the application, an absolute one with a place for the token, one with `url=`, one without.
    assert parser_address(HOST, data) == f"{HOST}/api/v1/vod/parse?url=dj%2F1"
    assert parser_address(HOST, {"parse_api": "https://p.model/{md5}.json", "url": "abc"}) == "https://p.model/abc.json"
    assert parser_address(HOST, {"parse_api": "https://p.model/?url=", "url": "a/b"}) == "https://p.model/?url=a%2Fb"
    assert parser_address(HOST, {"parse_api": "https://p.model/v/", "url": "abc"}) == "https://p.model/v/abc"
    for broken in ({"parse_api": "", "url": "abc"}, {"parse_api": "ftp://p.model/", "url": "abc"}):
        with pytest.raises(ValueError):
            parser_address(HOST, broken)
    with pytest.raises(ValueError):
        ticket_data("bm90IGpzb24.signature")


async def test_the_card_is_read_without_a_session_and_lists_its_lines_with_their_names(world):
    model, client, _now = world
    card = await client.card("1522016")
    assert card.names == ["万界独尊"] and card.year == "2021"
    assert [(line.code, line.name, [(item.label, item.token) for item in line.items]) for line in card.lines] == [
        ("duanju", "蓝光4K【推荐】", [("01", "dj-1"), ("02", "dj-2")]), ("zijianm3u8", "4K", [("01", "zj-1")])]
    assert model.paths() == ["/api/v1/video/detail"] and model.asked[0].headers["x-device-id"] == "0123456789abcdef"
    with pytest.raises(AppError) as absent:
        await client.card("1")
    assert "视频不存在" in absent.value.reason


async def test_the_stream_of_an_episode_is_its_ticket_given_to_its_parser_under_one_session(world):
    model, client, now = world
    stream = await client.resolve("duanju", "dj-1")
    assert stream.address == "https://store.model/dj/1.mp4?sign=1" and stream.headers == {"User-Agent": "okhttp/4.12.0"}
    assert model.paths() == ["/api/v1/security/init", "/api/v1/vod/ticket", "/api/v1/vod/parse"]
    opened = json.loads(model.asked[0].content)
    assert (opened["device_id"], opened["client_pub"], opened["so_version"], opened["platform"]) == (
        "0123456789abcdef", "pub-1", 13, "android")
    # The session is kept while it lasts, and opened anew shortly before its end.
    await client.resolve("duanju", "dj-1")
    assert model.paths().count("/api/v1/security/init") == 1
    now[0] += model.life - 20
    await client.resolve("duanju", "dj-1")
    assert model.sessions == ["sid_1", "sid_2"] and model.asked[-2].headers["x-session-id"] == "sid_2"


async def test_a_ticket_refused_for_a_session_that_ended_is_asked_again_once_under_a_new_one(world):
    model, client, _now = world
    await client.resolve("duanju", "dj-1")
    model.ended.add("sid_1")
    assert (await client.resolve("duanju", "dj-1")).address.startswith("https://store.model/")
    assert model.sessions == ["sid_1", "sid_2"]
    assert model.paths()[-4:] == ["/api/v1/vod/ticket", "/api/v1/security/init", "/api/v1/vod/ticket", "/api/v1/vod/parse"]


async def test_a_quota_of_the_parser_is_its_answer_about_the_episode_and_nothing_else_is_tried(world):
    model, client, _now = world
    with pytest.raises(AppError) as refused:
        await client.resolve("zijianm3u8", "zj-1")
    assert "今日解析次数已达上限" in refused.value.reason and not refused.value.transient
    # No other session, device or line is asked on a refusal: one session, one ticket, one ask of the parser.
    assert model.paths() == ["/api/v1/security/init", "/api/v1/vod/ticket", "/api/v1/vod/parse"]


async def test_the_search_gives_the_cards_as_the_application_gives_them(world):
    _model, client, _now = world
    page = await client.search_page("万界独尊", 1)
    assert (page.pages, page.total) == (1, 1)
    assert page.cards == [{"id": 1522016, "name": "万界独尊", "sub": "", "year": "2021", "area": "内地", "class": "动漫",
                           "remarks": "更新至497集", "blurb": "…", "pic": "https://pic.model/1.jpg"}]
