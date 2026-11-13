"""What a client of an application learned of the application (its parsers, its hosts, its zone) is believed for a term
and asked again when the term is over or the host stopped answering: a client that never asks again names a parser or a
host the application has left until the process restarts. Every client here is run against a model of the application by
its protocol whose published things change between two requests of the same client."""
from __future__ import annotations

import json

import httpx
import pytest

from modules.sources.apps import AppError, AppProfile, Lease, session_ended
from modules.sources.apps.app99 import App99Client
from modules.sources.apps.appdrama import AppDramaClient
from modules.sources.apps.appget import AppGetClient, encrypt
from modules.sources.apps.appv7 import AppV7Client
from modules.sources.apps.gulu import GuluClient
from app99_model import App99Model, profile as app99_profile
from app_protocol_models import GuluModel
from appdrama_model import HOST as DRAMA_HOST, NAVIGATOR, DramaModel, Video

TERM = 601.0  # a little over the term of what a client learned (apps/__init__.py, 10 minutes)


class Clock:
    def __init__(self) -> None:
        self.now = 1_731_300_000.0

    def __call__(self) -> float:
        return self.now


def web(handle) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def test_a_refusal_of_the_session_is_told_from_a_refusal_no_guest_escapes():
    assert session_ended(None, "", 401) and session_ended(401, "") and session_ended(1, "token expired")
    assert session_ended(1, "登录已过期") and session_ended(1, "请先登录")
    assert not session_ended(1, "session ended")  # no word of the ended session: the catalogue's own rule decides
    assert not session_ended(401, "VIP会员专享") and not session_ended(None, "quota exceeded", 401)


def test_a_lease_lasts_its_term_and_ends_when_it_is_let_go():
    clock = Clock()
    lease = Lease(600.0, clock)
    assert not lease.live
    lease.renew()
    clock.now += 599
    assert lease.live
    clock.now += 2
    assert not lease.live
    lease.renew(30.0)
    assert lease.live
    lease.lapse()
    assert not lease.live


# --- App99: the guest and the system of the application -----------------------------------------------------------------

ADDRESS = "https://cdn.model/1/index.m3u8"


def app99_world() -> App99Model:
    model = App99Model(parsers=[9])
    model.parses[(9, "tok")] = ADDRESS
    return model


async def test_http_401_ends_the_guest_and_even_a_parser_asks_again_under_a_new_session_of_the_same_device():
    model = app99_world()
    async with web(model.handle) as http:
        client = App99Client(app99_profile(), lambda: http)
        assert await client.parse(9, "tok") == ADDRESS
        model.unauthorized.add("/app/vodParser")
        assert await client.parse(9, "tok") == ADDRESS
    assert model.paths() == ["/app/systemInit", "/app/userInfo", "/app/vodParser", "/app/vodParser", "/app/systemInit",
                             "/app/userInfo", "/app/vodParser"]
    assert (model.sessions, model.logins) == (2, 2)


async def test_a_quota_or_a_vip_check_is_no_end_of_the_session_and_opens_no_other():
    model = app99_world()
    async with web(model.handle) as http:
        client = App99Client(app99_profile(), lambda: http)
        await client.system()
        model.said["/vod/detail"] = "VIP会员专享"
        with pytest.raises(AppError) as refused:
            await client.detail("1")
        assert "VIP" in refused.value.reason
    assert model.paths() == ["/app/systemInit", "/app/userInfo", "/vod/detail"] and model.logins == 1


async def test_the_system_of_the_application_is_asked_again_after_its_term_under_the_same_guest():
    model = app99_world()
    clock = Clock()
    async with web(model.handle) as http:
        client = App99Client(app99_profile(), lambda: http, clock=clock)
        assert [each["id"] for each in (await client.system())["parser_api"]] == [9]
        model.parsers = [7]  # the application has changed its parsers
        clock.now += TERM - 2
        assert [each["id"] for each in (await client.system())["parser_api"]] == [9]
        clock.now += 3
        assert [each["id"] for each in (await client.system())["parser_api"]] == [7]
        await client.system()
    assert model.paths() == ["/app/systemInit", "/app/userInfo", "/app/systemInit"] and model.logins == 1


async def test_a_system_that_cannot_be_asked_again_stays_as_it_was_and_is_not_asked_at_every_request():
    model = app99_world()
    clock = Clock()
    down = {"on": False}

    def handle(request: httpx.Request) -> httpx.Response:
        if down["on"] and request.url.path.endswith("/app/systemInit"):
            raise httpx.ConnectTimeout("down", request=request)
        return model.handle(request)

    async with web(handle) as http:
        client = App99Client(app99_profile(), lambda: http, clock=clock)
        await client.system()
        down["on"] = True
        clock.now += TERM
        assert [each["id"] for each in (await client.system())["parser_api"]] == [9]
        before = len(model.calls)
        await client.system()
        assert len(model.calls) == before
        clock.now += 31  # the short term of a failed asking is over: it is asked again
        down["on"] = False
        model.parsers = [7]
        assert [each["id"] for each in (await client.system())["parser_api"]] == [7]


# --- Gulu: the boot of the application ---------------------------------------------------------------------------------

async def test_the_boot_is_asked_again_in_the_same_session_and_the_parser_is_the_one_it_names_now():
    model = GuluModel()
    model.card(5, "T", "JD4K", [("1", "tok1", "https://cdn.model/1.m3u8")])
    clock = Clock()
    async with web(model.handle) as http:
        client = GuluClient(model.profile(), lambda: http, clock=clock)
        assert (await client.resolve("JD4K", "tok1")).address == "https://cdn.model/1.m3u8"
        old = model.PARSER
        model.PARSER = "https://gulu-parser-moved.model/api?url="
        clock.now += TERM
        assert (await client.resolve("JD4K", "tok1")).address == "https://cdn.model/1.m3u8"
    parsed = [url for url in model.asked if "parser" in url]
    assert parsed[0].startswith(old) and parsed[1].startswith(model.PARSER)
    assert len(model.keys) == 1  # the same session: one handshake


async def test_a_session_the_server_dropped_is_opened_anew_when_the_boot_is_asked_again():
    model = GuluModel()
    model.card(5, "T", "JD4K", [("1", "tok1", "https://cdn.model/1.m3u8")])
    clock = Clock()
    async with web(model.handle) as http:
        client = GuluClient(model.profile(), lambda: http, clock=clock)
        await client.resolve("JD4K", "tok1")
        model.keys.clear()
        clock.now += TERM
        assert (await client.resolve("JD4K", "tok1")).address == "https://cdn.model/1.m3u8"
    assert len(model.keys) == 1


# --- AppGet (Moli): the published list of hosts and the parsers of a card ----------------------------------------------

KEY, IV = "0123456789abcdef", "fedcba9876543210"
SITE = "https://site.model/hosts.txt"
DETAIL = {"vod": {"vod_name": "T", "vod_year": "2021"}, "vod_play_list": [{"player_info": {"show": "4K", "parse": "abc"}, "urls": [
    {"name": "01", "url": "JD-1", "from": "JD4K", "nid": 1, "token": "", "parse_api_url": ""}]}]}


class Published:
    """The list of hosts the application publishes and the hosts that answer."""

    def __init__(self, listed: list[str], alive: set[str]) -> None:
        self.listed, self.alive = listed, alive
        self.lists = 0
        self.asked: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == SITE:
            self.lists += 1
            return httpx.Response(200, text="\n".join(self.listed))
        host = f"{request.url.scheme}://{request.url.host}"
        self.asked.append(f"{host}{request.url.path.rsplit('/', 1)[-1]}")
        if host not in self.alive:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json={"code": 1, "msg": "ok", "data": encrypt(json.dumps(DETAIL), KEY, IV)})


def moli(model: Published, http: httpx.AsyncClient, clock: Clock) -> AppGetClient:
    profile = AppProfile("moli", "Moli", "appget", {"dataKey": KEY, "dataIv": IV, "site": SITE})
    return AppGetClient(profile, lambda: http, clock=clock)


async def test_the_published_list_of_hosts_is_read_again_after_its_term():
    model = Published(["https://a.model"], {"https://a.model", "https://b.model"})
    clock = Clock()
    async with web(model.handle) as http:
        client = moli(model, http, clock)
        await client.card("1")
        await client.card("1")
        assert model.lists == 1
        model.listed = ["https://b.model"]
        clock.now += 301
        await client.card("1")
    assert model.lists == 2 and model.asked[-1] == "https://b.modelvodDetail"


async def test_when_no_host_answers_the_list_is_read_again_and_the_hosts_it_names_now_are_tried():
    model = Published(["https://a.model"], {"https://a.model"})
    async with web(model.handle) as http:
        client = moli(model, http, Clock())
        await client.card("1")
        model.listed, model.alive = ["https://a.model", "https://moved.model"], {"https://moved.model"}
        await client.card("1")
    assert model.lists == 2
    assert model.asked.count("https://a.modelvodDetail") == 2 and model.asked[-1] == "https://moved.modelvodDetail"


async def test_a_list_that_names_no_new_host_is_no_reason_to_ask_the_hosts_again():
    model = Published(["https://a.model"], set())
    async with web(model.handle) as http:
        client = moli(model, http, Clock())
        with pytest.raises(AppError) as down:
            await client.card("1")
        assert down.value.transient
    assert model.lists == 2 and model.asked == ["https://a.modelvodDetail"]


async def test_the_parsers_a_card_gave_are_believed_for_a_term_and_the_card_is_asked_again_after_it():
    model = Published(["https://a.model"], {"https://a.model"})
    clock = Clock()
    async with web(model.handle) as http:
        client = moli(model, http, clock)
        await client._asked_with("JD4K", "JD-1", "7")
        await client._asked_with("JD4K", "JD-1", "7")
        assert model.asked == ["https://a.modelvodDetail"]
        clock.now += TERM
        await client._asked_with("JD4K", "JD-1", "7")
    assert model.asked == ["https://a.modelvodDetail"] * 2


# --- AppDrama (Huayi, Juzi): the host a request did not reach -----------------------------------------------------------

async def test_a_host_that_stops_answering_is_replaced_by_the_one_the_navigator_names_now():
    model = DramaModel()
    model.cards[1] = ("T", 2021, [Video("zijianm3u8", "4K", "1", "zijian_tok1")])
    seen: list[str] = []
    state = {"dead": False, "domain": DRAMA_HOST}

    def handle(request: httpx.Request) -> httpx.Response:
        if str(request.url) == NAVIGATOR:
            return httpx.Response(200, json={"domain": state["domain"]})
        seen.append(request.url.host)
        if state["dead"] and request.url.host == "drama.model":
            raise httpx.ConnectTimeout("down", request=request)
        return model.handle(request)

    profile = model.profile()
    profile = AppProfile(profile.name, profile.label, profile.protocol, {**profile.settings, "site": NAVIGATOR, "host": DRAMA_HOST})
    async with web(handle) as http:
        client = AppDramaClient(profile, lambda: http)
        assert (await client.card("1")).names == ["T"]
        assert model.zones == 1
        state["dead"], state["domain"] = True, "https://drama-moved.model"
        assert (await client.card("1")).names == ["T"]
    assert model.zones == 2 and seen[-1] == "drama-moved.model" and "drama-moved.model" in seen


async def test_a_host_of_the_profile_that_is_down_from_the_first_request_is_replaced_too():
    model = DramaModel()
    model.cards[1] = ("T", 2021, [Video("zijianm3u8", "4K", "1", "zijian_tok1")])

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "gone.model":
            raise httpx.ConnectTimeout("down", request=request)
        return model.handle(request)

    profile = model.profile()
    profile = AppProfile(profile.name, profile.label, profile.protocol, {**profile.settings, "site": NAVIGATOR, "host": "https://gone.model"})
    async with web(handle) as http:
        assert (await AppDramaClient(profile, lambda: http).card("1")).names == ["T"]


async def test_without_a_published_navigator_a_host_that_does_not_answer_is_the_answer():
    model = DramaModel()
    model.cards[1] = ("T", 2021, [])

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/api/v5/find/app/zone" and str(request.url) != NAVIGATOR:
            raise httpx.ConnectTimeout("down", request=request)
        return model.handle(request)

    async with web(handle) as http:
        with pytest.raises(AppError) as down:
            await AppDramaClient(model.profile(), lambda: http).card("1")
        assert down.value.transient


# --- AppV7 (Mtq, Xiaozhu): the parsers of the lines ---------------------------------------------------------------------

API, PARSER = "https://api.model/v7/logs", "https://parser.model/p?url="
SETTINGS = {"label": "蒙太奇", "protocol": "appv7", "api": API, "package": "pk", "build_time": "1700000000", "salt": "s",
            "version": "1.0", "version_number": "9", "platform_version": "13", "card_identity": json.dumps({"app": "x"})}


def v7_model():
    from test_sources_appv7 import Model
    return Model()


async def test_a_line_asked_without_a_card_goes_by_the_parser_the_card_of_the_profile_gives_now_for_a_term():
    model = v7_model()
    seeded = {**SETTINGS, "resolver_card": "90331", "parsers": json.dumps({"zijianm3u8": {
        "code": "zijianm3u8", "parse_api": "https://old-parser.model/p?url=", "parse_secret": False}})}
    clock = Clock()
    async with web(model.handle) as http:
        client = AppV7Client(AppProfile("mtq", "蒙太奇", "appv7", seeded), lambda: http, clock=clock)
        assert (await client.resolve("zijianm3u8", "zijian_9", "")).address == "https://store.model/z/9.m3u8"
        assert [body["method"] for body in model.bodies] == ["video_detail"]
        assert model.asked[-1].url.host == "parser.model"  # not the parser the profile began with
        await client.resolve("zijianm3u8", "zijian_9", "")
        assert len(model.bodies) == 1
        clock.now += TERM
        await client.resolve("zijianm3u8", "zijian_9", "")
        assert len(model.bodies) == 2


async def test_the_parser_a_card_gave_is_believed_for_a_term_and_the_card_is_asked_again_after_it():
    model = v7_model()
    clock = Clock()
    async with web(model.handle) as http:
        client = AppV7Client(AppProfile("xiaozhu", "小猪", "appv7", SETTINGS), lambda: http, clock=clock)
        await client.resolve("JD4K", "jd-1", "90331")
        await client.resolve("JD4K", "jd-1", "90331")
        assert [body["method"] for body in model.bodies].count("video_detail") == 1
        clock.now += TERM
        await client.resolve("JD4K", "jd-1", "90331")
    assert [body["method"] for body in model.bodies].count("video_detail") == 2
