"""The search of the public shares of 139 Cloud (modules/sources/apps/share139_search.py and the search of share139.py): the
posts of the public channels and the answers of the search APIs read into candidates, each looked into through the cloud itself,
each folder offered a card whose number is kept — against models of the indexes and of the cloud by the forms the owner's
prototype reads (no snapshot of a real answer)."""
from __future__ import annotations

import json

import httpx
import pytest

from core.db import Database
from modules.sources.apps import AppError, profiles_of
from modules.sources.apps.share139 import (ACCESSIBLE_FOR_S, FOUND_FOR_S, NOTHING_FOUND_FOR_S, VERIFIED_AT_MOST, Share139Client,
                                           search_sources_of)
from modules.sources.apps.share139_search import IndexAnswerError, pansou_candidates, parse_share, telegram_candidates, title_matches
from modules.sources.repository import SHARE_CARDS_FROM, SourcesRepository

TITLE = "万界独尊"
OTHER_TITLE = "九棺"
PROFILE = {"label": "139云盘", "latin": "139 Cloud", "protocol": "share139", "shares": {}}


def post(number: int, text: str, *links: str, when: str = "2026-11-10T10:00:00+00:00", channel: str = "yunpan139") -> str:
    anchors = "".join(f'<a href="{link}">{link}</a> ' for link in links)
    return (f'<div class="tgme_widget_message" data-post="{channel}/{number}"><div class="tgme_widget_message_text">{text}'
            f'<br>{anchors}</div><time datetime="{when}"></time></div>')


def share(link: str) -> str:
    return f"https://yun.139.com/shareweb/#/w/i/{link}"


class World:
    """The channels, the search APIs and the cloud: what each says is set by the test, every request is counted."""

    def __init__(self) -> None:
        self.channels: dict[str, str] = {}      # channel -> the page its search gives
        # host -> the JSON its search gives (a status when an int); «none found» unless a test says more
        self.pansou: dict[str, object] = {host: {"code": 0, "data": {"results": []}} for host in ("so.252035.xyz", "pansou.app")}
        self.cloud: dict[str, object] = {}      # link -> the listing of its root, or the words of a refusal
        self.folders: dict[tuple[str, str], dict] = {}   # (link, folder) -> the listing of a folder
        self.requests: list[str] = []
        self.listings: list[str] = []
        self.asked: list[dict] = []
        self.refuse_channels: int | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.requests.append(f"{host}{request.url.path}")
        if host == "t.me":
            if self.refuse_channels:
                return httpx.Response(self.refuse_channels)
            channel = request.url.path.rsplit("/", 1)[-1]
            assert request.url.params["q"]
            return httpx.Response(200, text=self.channels.get(channel, ""))
        if host in self.pansou:
            assert request.url.params["src"] == "tg" and request.url.params["conc"] == "1"
            assert len(request.url.params["channels"].split(",")) <= 3
            answer = self.pansou[host]
            return httpx.Response(answer) if isinstance(answer, int) else httpx.Response(200, json=answer)
        if request.url.path.endswith("getOutLinkInfoV6"):
            body = json.loads(request.content)["getOutLinkInfoReq"]
            self.listings.append(f"{body['linkID']}/{body['pCaID']}")
            self.asked.append(body)
            if body["pCaID"] == "root":
                said = self.cloud.get(body["linkID"], "分享已取消")
                if isinstance(said, str):
                    return httpx.Response(200, json={"code": "200000727", "desc": said})
                return httpx.Response(200, json={"code": "0", "data": said})
            return httpx.Response(200, json={"code": "0", "data": self.folders[(body["linkID"], body["pCaID"])]})
        return httpx.Response(404)


def videos(*names: str) -> dict:
    return {"coLst": [{"coID": f"c{number}", "coName": name, "coSize": 1, "extInfo": {"width": "3840"}}
                      for number, name in enumerate(names, start=1)], "caLst": []}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def world(db_path):
    world, clock = World(), Clock()
    repo = SourcesRepository(Database(str(db_path)))
    profile = profiles_of({"ST_APP_SHARE139": json.dumps(PROFILE)})[0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
        def client() -> Share139Client:
            return Share139Client(profile, lambda: http, folders=repo, clock=clock, interval=0)

        yield world, clock, client, repo


def ids(page) -> list[int]:
    return [int(each["id"]) for each in page.cards]


# --- what the indexes say, read ----------------------------------------------------------------------------------------

def test_a_share_is_an_address_of_139_cloud_itself_with_no_credentials_and_its_code_is_the_one_given_or_the_address_carries():
    for address in ("https://caiyun.139.com/w/i/public123", share("public123"), "https://caiyun.139.com/m/i?public123"):
        assert parse_share(address) == ("public123", "")
    assert parse_share(share("public123") + "?pwd=abcd") == ("public123", "abcd")
    assert parse_share(share("public123"), "given") == ("public123", "given")
    for address in ("https://yun.139.com.evil.invalid/w/i/public123", "https://evil.invalid/?u=https://yun.139.com/w/i/public123",
                    "https://user:pass@yun.139.com/w/i/public123", "https://pan.baidu.com/s/public123", "ftp://yun.139.com/w/i/public123"):
        assert parse_share(address) is None


def test_the_posts_of_a_channel_offer_the_shares_whose_text_holds_the_words_and_a_post_of_one_share_gives_its_code():
    page = (post(1, f"名称：{TITLE} 4K 更498<br>提取码：ab12", share("only1234")) + post(2, f"{OTHER_TITLE} 全集", share("wrong456"))
            + post(3, f"{TITLE} 两部", share("first111"), share("second22"), "https://pan.baidu.com/s/other")
            + post(4, f"{TITLE} 文本里的链接 {share('inline99')}", when="2026-11-09T10:00:00+00:00"))
    found = telegram_candidates(page, TITLE)
    assert [(each.link_id, each.password, each.title.split()[0]) for each in found] == [
        ("only1234", "ab12", TITLE), ("first111", "", TITLE), ("second22", "", TITLE), ("inline99", "", TITLE)]
    assert found[0].publisher_url == "https://t.me/yunpan139/1" and found[0].published_at == "2026-11-10T10:00:00+00:00"
    assert found[0].found_by == "telegram" and title_matches(TITLE, "名称：万界 独尊 4K")


def test_the_answer_of_a_search_api_is_read_for_its_results_and_an_answer_of_another_shape_or_a_refusal_is_an_error():
    answer = {"code": 0, "data": {"results": [
        {"title": f"{TITLE} 4K", "channel": "pub", "message_id": 7, "datetime": "2026-11-01", "links": [
            {"url": share("public123"), "password": "abcd"}, {"url": "https://pan.baidu.com/s/x"}]},
        {"title": OTHER_TITLE, "links": [{"url": share("wrong456")}]}],
        "merged_by_type": {"mobile": [{"url": share("merged01"), "note": f"{TITLE} 合集", "source": "tg:pub"}, "junk"]}}}
    found = pansou_candidates(answer, TITLE)
    assert [(each.link_id, each.password, each.publisher_url) for each in found] == [
        ("public123", "abcd", "https://t.me/pub/7"), ("merged01", "", "tg:pub")]
    for broken in ("<html>", [], {"code": 0, "data": "x"}, {"code": 0, "data": {}}):
        with pytest.raises(IndexAnswerError) as shape:
            pansou_candidates(broken, TITLE)
        assert shape.value.code == "search_index_shape"
    with pytest.raises(IndexAnswerError) as refused:
        pansou_candidates({"code": 500, "data": {"results": []}}, TITLE)
    assert refused.value.code == "search_index_refused"


def test_the_indexes_of_the_profile_are_a_list_of_at_most_six_of_a_known_kind_and_a_fault_of_it_stops_the_start():
    assert [each["kind"] for each in search_sources_of({})] == ["telegram", "telegram", "pansou", "pansou"]
    assert search_sources_of({"search_sources": "[]"}) == []
    assert search_sources_of({"search_sources": json.dumps([{"kind": "telegram", "channel": "somechannel"}])})[0]["channel"] == "somechannel"
    for bad in ("not json", "{}", json.dumps([{"kind": "telegram", "channel": "no spaces"}]), json.dumps([{"kind": "pansou", "url": "ftp://x"}]),
                json.dumps([{"kind": "pansou", "url": "https://x/y", "channels": ["a", "b", "c", "d"]}]),
                json.dumps([{"kind": "other"}] * 2), json.dumps([{"kind": "telegram", "channel": "somechannel"}] * 7)):
        with pytest.raises(ValueError):
            search_sources_of({"search_sources": bad})


# --- the search ---------------------------------------------------------------------------------------------------------

async def test_the_search_offers_the_folder_of_a_share_a_post_gives_each_with_a_card_that_is_read_again_by_a_client_that_never_searched(world):
    cloud, clock, client, repo = world
    cloud.channels["yunpan139"] = post(1, f"名称：{TITLE} 4K<br>提取码：ab12", share("public123"))
    cloud.cloud["public123"] = videos("第1集.mp4", "第2集.mp4")
    page = await client().search_page(TITLE, 1)
    assert len(page.cards) == 1 and (page.pages, page.total) == (1, 1)
    card = page.cards[0]
    assert int(card["id"]) >= SHARE_CARDS_FROM and card["name"] == f"{TITLE} 4K" and card["remarks"] == "accessible"
    assert card["blurb"] == "https://t.me/yunpan139/1"
    assert cloud.requests == ["t.me/s/yunpan139", "t.me/s/ydypzyfx", "share-kd-njs.yun.139.com/yun-share/richlifeApp/devapp/IOutLink/getOutLinkInfoV6"]
    # Another client, with the same memory, reads the card by its number: the search is not repeated and the code goes with it.
    fresh = client()
    data = await fresh.card(card["id"])
    assert data.names == [f"{TITLE} 4K"] and [each.label for each in data.lines[0].items] == ["第1集.mp4", "第2集.mp4"]
    assert cloud.asked[-1]["linkID"] == "public123" and cloud.asked[-1]["passwd"] == "ab12"
    # The same share found again keeps its number.
    clock.now += FOUND_FOR_S + 1
    again = await client().search_page(TITLE, 1)
    assert [each["id"] for each in again.cards] == [card["id"]]
    assert [each["link_id"] for each in await repo.share_folders()] == ["public123"]


async def test_what_the_indexes_answered_is_believed_for_half_an_hour_the_cloud_for_five_minutes_and_the_answer_none_for_two(world):
    cloud, clock, client, _repo = world
    cloud.channels["yunpan139"] = post(1, TITLE, share("public123"))
    cloud.cloud["public123"] = videos("1.mp4")
    app = client()
    await app.search_page(TITLE, 1)
    asked = len(cloud.requests)
    await app.search_page(TITLE, 1)
    clock.now += ACCESSIBLE_FOR_S - 1
    await app.search_page(TITLE, 1)
    assert len(cloud.requests) == asked, "neither the indexes nor the cloud are asked again within their terms"
    clock.now += 2
    await app.search_page(TITLE, 1)
    assert len(cloud.requests) == asked + 1 and cloud.requests[-1].endswith("getOutLinkInfoV6"), "the cloud is asked, the indexes not"
    clock.now += FOUND_FOR_S
    await app.search_page(TITLE, 1)
    assert cloud.requests[asked + 1:asked + 3] == ["t.me/s/yunpan139", "t.me/s/ydypzyfx"]
    # A title nobody posted: every index is asked once, and the answer «none» holds for two minutes only.
    nothing = client()
    assert (await nothing.search_page("没有这部", 1)).cards == []
    sent = len(cloud.requests)
    await nothing.search_page("没有这部", 1)
    assert len(cloud.requests) == sent
    clock.now += NOTHING_FOUND_FOR_S + 1
    await nothing.search_page("没有这部", 1)
    assert len(cloud.requests) > sent


async def test_the_fallback_indexes_are_asked_only_when_the_others_found_nothing_and_one_channel_after_another(world):
    cloud, _clock, client, _repo = world
    cloud.pansou = {"so.252035.xyz": {"code": 0, "data": {"results": [{"title": TITLE, "links": [{"url": share("viaapi01")}]}]}},
                    "pansou.app": 500}
    cloud.cloud["viaapi01"] = videos("1.mp4")
    page = await client().search_page(TITLE, 1)
    assert [each["remarks"] for each in page.cards] == ["accessible"]
    assert cloud.requests[:3] == ["t.me/s/yunpan139", "t.me/s/ydypzyfx", "so.252035.xyz/api/search"]
    assert not any(each.startswith("pansou.app") for each in cloud.requests)


async def test_a_folder_its_owner_deleted_is_not_offered_one_that_wants_a_password_and_one_the_cloud_did_not_answer_are_offered_so(world):
    cloud, _clock, client, _repo = world
    cloud.channels["yunpan139"] = (post(1, f"{TITLE} 已删", share("deleted11"), when="2026-11-03T10:00:00+00:00")
                                   + post(2, f"{TITLE} 加密", share("secret222"), when="2026-11-02T10:00:00+00:00")
                                   + post(3, f"{TITLE} 好", share("good3333"), when="2026-11-04T10:00:00+00:00"))
    cloud.cloud.update({"secret222": "提取码错误", "good3333": videos("1.mp4")})
    page = await client().search_page(TITLE, 1)
    assert [(each["name"].split()[-1], each["remarks"]) for each in page.cards] == [("好", "accessible"), ("加密", "password_required")]
    assert "deleted11" not in json.dumps(page.cards)


async def test_a_search_looks_into_the_first_few_candidates_only_and_offers_the_rest_unchecked(world):
    cloud, _clock, client, _repo = world
    cloud.channels["yunpan139"] = "".join(post(number, f"{TITLE} 第{number}部", share(f"share{number:04d}"),
                                               when=f"2026-11-{number:02d}T10:00:00+00:00") for number in range(1, 7))
    for number in range(1, 7):
        cloud.cloud[f"share{number:04d}"] = videos("1.mp4")
    page = await client().search_page(TITLE, 1)
    assert len(page.cards) == 6 and len(cloud.listings) == VERIFIED_AT_MOST
    assert [each["remarks"] for each in page.cards] == ["accessible"] * VERIFIED_AT_MOST + ["unchecked"] * (6 - VERIFIED_AT_MOST)
    # The latest posts are looked into first; the next search looks into the next ones.
    assert cloud.listings == ["share0006/root", "share0005/root", "share0004/root"]
    app = client()
    await app.search_page(TITLE, 1)
    second = await app.search_page(TITLE, 1)
    assert cloud.listings[VERIFIED_AT_MOST:] == ["share0006/root", "share0005/root", "share0004/root",
                                                 "share0003/root", "share0002/root", "share0001/root"][:2 * VERIFIED_AT_MOST]
    assert [each["remarks"] for each in second.cards] == ["accessible"] * 6


async def test_a_share_of_folders_is_offered_as_the_folders_the_words_name_and_each_is_a_card_of_its_own(world):
    cloud, _clock, client, repo = world
    cloud.channels["yunpan139"] = post(1, f"{TITLE} 全集", share("library1"))
    cloud.cloud["library1"] = {"coLst": [], "caLst": [{"caID": "f1", "caName": f"{TITLE} 第一季"}, {"caID": "f2", "caName": "别的剧"},
                                                      {"caID": "f3", "caName": f"{TITLE} 第二季"}]}
    cloud.folders[("library1", "f1")] = videos("S1E1.mp4")
    page = await client().search_page(TITLE, 1)
    assert [each["name"] for each in page.cards] == [f"{TITLE} 全集 / {TITLE} 第一季", f"{TITLE} 全集 / {TITLE} 第二季"]
    first = await client().card(page.cards[0]["id"])
    assert [each.label for each in first.lines[0].items] == ["S1E1.mp4"]
    assert sorted(each["folder_id"] for each in await repo.share_folders()) == ["f1", "f3"]
    # A share of one folder is that folder: the card of the share reads it.
    cloud.channels["ydypzyfx"] = post(2, f"{TITLE} 单", share("single001"), channel="ydypzyfx")
    cloud.cloud["single001"] = {"coLst": [], "caLst": [{"caID": "only", "caName": "x"}]}
    cloud.folders[("single001", "only")] = videos("E1.mp4")
    single = [each for each in (await client().search_page(TITLE, 1)).cards if each["name"].endswith("单")]
    assert [each.label for each in (await client().card(single[0]["id"])).lines[0].items] == ["E1.mp4"]


async def test_every_index_down_is_an_error_not_the_answer_nothing_found_and_an_index_that_refused_is_not_asked_again_for_a_while(world):
    cloud, clock, client, _repo = world
    cloud.refuse_channels = 429
    cloud.pansou = {"so.252035.xyz": 503, "pansou.app": {"code": 0, "data": "x"}}
    app = client()
    with pytest.raises(AppError) as down:
        await app.search_page(TITLE, 1)
    assert down.value.transient and "app_search_unavailable" in down.value.reason and "app_status" in down.value.reason
    asked = len(cloud.requests)
    with pytest.raises(AppError):
        await app.search_page("凡人修仙传", 1)
    assert len(cloud.requests) == asked + 1, "only the index that did not refuse (it answered in a form of no index) is asked again"
    clock.now += 601
    cloud.refuse_channels = None
    cloud.channels["yunpan139"] = post(1, TITLE, share("back0001"))
    cloud.cloud["back0001"] = videos("1.mp4")
    assert [each["remarks"] for each in (await app.search_page(TITLE, 1)).cards] == ["accessible"]


async def test_what_was_found_before_is_still_offered_when_the_channels_refuse_and_the_search_apis_find_none(world):
    cloud, clock, client, _repo = world
    cloud.channels["yunpan139"] = post(1, TITLE, share("public123"))
    cloud.cloud["public123"] = videos("1.mp4")
    first = await client().search_page(TITLE, 1)
    cloud.channels.clear()
    cloud.refuse_channels = 503
    clock.now += FOUND_FOR_S + 1
    # Every channel refuses now, but the folder found before is the owner's to choose still.
    again = await client().search_page(TITLE, 1)
    assert [each["id"] for each in again.cards] == [first.cards[0]["id"]]
    assert [each["name"] for each in again.cards] == [f"{TITLE}"]


async def test_the_search_of_a_client_with_no_indexes_is_the_shares_the_profile_connects_and_the_folders_found_before(world):
    cloud, _clock, _client, repo = world
    profile = profiles_of({"ST_APP_SHARE139": json.dumps({**PROFILE, "search_sources": [], "shares": {
        "1": {"title": "东大高武学院", "year": "2024", "link_id": "public-link", "folder_id": "root"}}})})[0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(cloud.handle)) as http:
        app = Share139Client(profile, lambda: http, folders=repo, interval=0)
        assert [(each["id"], each["year"]) for each in (await app.search_page("高武", 1)).cards] == [("1", "2024")]
        assert (await app.search_page("没有", 1)).cards == [] and cloud.requests == []


async def test_a_card_nobody_registered_is_absent_and_the_memory_of_the_folders_keeps_a_number_per_folder(world):
    cloud, _clock, client, repo = world
    with pytest.raises(AppError) as absent:
        await client().card("1000042")
    assert "app_title_absent" in absent.value.reason
    first = await repo.share_folder_register("linkA", "root", password="pw", title="一", publisher_url="u")
    assert first == SHARE_CARDS_FROM
    assert await repo.share_folder_register("linkA", "sub", title="二") == SHARE_CARDS_FROM + 1
    # What is known again replaces what was; an empty value does not.
    assert await repo.share_folder_register("linkA", "root", title="一一") == first
    held = await repo.share_folder(first)
    assert (held["password"], held["title"], held["publisher_url"]) == ("pw", "一一", "u")
    assert await repo.share_folder(SHARE_CARDS_FROM + 9) is None
