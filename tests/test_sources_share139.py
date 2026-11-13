"""The public shares of 139 Cloud (modules/sources/apps/share139.py): the card of a share is the video files of its folder
by the quality the cloud stores for each, the stream of a file is asked of the cloud at every resolve, the search finds the
shares the profile connects — against a model of the cloud by its protocol (the answers are of the form the owner's
prototype reads, no snapshot of a real one)."""
from __future__ import annotations

import json

import httpx
import pytest

from modules.sources.apps import AppError, profiles_of
from modules.sources.apps.share139 import CATALOG_REQUESTS, LISTED_AT_MOST, Share139Client, natural, quality_of

PROFILE = {"label": "139云盘", "latin": "139 Cloud", "protocol": "share139", "shares": {
    "1": {"title": "东大高武学院", "year": "2024", "link_id": "public-link", "folder_id": "root"},
    "2": {"title": "另一部", "link_id": "other-link", "folder_id": "folder-2", "password": "pw"}}}


def files(*named: tuple[str, str, object]) -> list[dict]:
    return [{"coID": token, "coName": name, "coSize": 123, "extInfo": ext} for name, token, ext in named]


class Cloud:
    def __init__(self) -> None:
        self.bodies: list[tuple[str, dict]] = []
        self.listing: dict[str, dict] = {}  # folder id -> the data of its listing
        self.present: dict[str, str] = {}   # content id -> its preview address
        self.issued = 0
        self.refuse: str | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        method, body = request.url.path.rsplit("/", 1)[-1], json.loads(request.content)
        self.bodies.append((method, body))
        assert request.headers["Content-Type"] == "application/json" and request.headers["Referer"] == "https://yun.139.com/"
        if self.refuse:
            return httpx.Response(200, json={"code": "200000727", "desc": self.refuse})
        if method == "getOutLinkInfoV6":
            return httpx.Response(200, json={"code": "0", "data": self.listing[body["getOutLinkInfoReq"]["pCaID"]]})
        if method == "getContentInfoFromOutLink":
            content = body["getContentInfoFromOutLinkReq"]["contentId"]
            self.issued += 1
            address = self.present.get(content)
            return httpx.Response(200, json={"code": "0", "data": {"contentInfo": {"presentURL": f"{address}?sig={self.issued}"}
                                                                  if address else {}}})
        return httpx.Response(404)


@pytest.fixture
async def world():
    cloud = Cloud()
    profile = profiles_of({"ST_APP_SHARE139": json.dumps(PROFILE)})[0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(cloud.handle)) as http:
        yield cloud, Share139Client(profile, lambda: http, interval=0)


def test_a_name_is_read_the_way_a_person_orders_files_and_a_width_is_a_quality():
    assert sorted(["E10.mp4", "E2.mp4", "E1.mp4"], key=natural) == ["E1.mp4", "E2.mp4", "E10.mp4"]
    assert [quality_of(width)[0] for width in (3840, 3000, 1920, 1280, 720, 0)] == [
        "preview4k", "preview1080", "preview1080", "preview1080", "preview_unknown", "preview_unknown"]


async def test_the_card_lists_every_video_of_the_folder_by_the_quality_the_cloud_stores_in_the_order_of_the_names(world):
    cloud, client = world
    cloud.listing["root"] = {"caLst": [{"caID": "sub", "caName": "sub"}], "coLst": files(
        ("S01EP10.4K.mp4", "c10", {"width": "3840", "height": "1640"}), ("S01EP2.4K.mp4", "c2", {"width": 3840}),
        ("S01EP2.4K.NoSub.mp4", "c2n", json.dumps({"width": "3840"})), ("S01EP2.1080P.mp4", "h2", {"width": "1920"}),
        ("S01EP2.1080P_修复.mp4", "h2f", {"width": "1920"}), ("poster.jpg", "p", {}), ("unmeasured.mkv", "u", {}))}
    card = await client.card("1")
    assert (card.names, card.year) == (["东大高武学院"], "2024")
    lines = {line.code: line for line in card.lines}
    assert list(lines) == ["preview4k", "preview1080", "preview_unknown"]
    # Every file is an episode with its name as it is: nothing is numbered, dropped or told apart by what the name says.
    assert [(each.place, each.label, each.token) for each in lines["preview4k"].items] == [
        (1, "S01EP2.4K.mp4", "c2"), (2, "S01EP2.4K.NoSub.mp4", "c2n"), (3, "S01EP10.4K.mp4", "c10")]
    assert [each.label for each in lines["preview1080"].items] == ["S01EP2.1080P.mp4", "S01EP2.1080P_修复.mp4"]
    assert [each.label for each in lines["preview_unknown"].items] == ["unmeasured.mkv"]
    assert not any(line.listed for line in card.lines)
    method, body = cloud.bodies[0]
    assert method == "getOutLinkInfoV6" and body["getOutLinkInfoReq"] == {
        "linkID": "public-link", "pCaID": "root", "passwd": "", "account": "", "bNum": 1, "eNum": LISTED_AT_MOST}


async def test_a_share_whose_root_holds_one_folder_is_that_folder_and_the_password_goes_with_the_request(world):
    cloud, client = world
    cloud.listing["folder-2"] = {"coLst": [], "caLst": [{"caID": "inner", "caName": "inner"}]}
    cloud.listing["inner"] = {"coLst": files(("E1.mp4", "e1", {"width": "1920"})), "caLst": []}
    card = await client.card("2")
    assert [line.code for line in card.lines] == ["preview1080"]
    assert [body["getOutLinkInfoReq"]["pCaID"] for _method, body in cloud.bodies] == ["folder-2", "inner"]
    assert cloud.bodies[0][1]["getOutLinkInfoReq"]["passwd"] == "pw"


async def test_a_folder_of_more_files_than_one_window_is_read_window_by_window():
    windows = {1: files(*((f"E{number}.mp4", f"c{number}", {"width": "3840"}) for number in range(1, LISTED_AT_MOST + 1))),
               LISTED_AT_MOST + 1: files(("E201.mp4", "c201", {"width": "3840"}))}
    asked = []

    def listing(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)["getOutLinkInfoReq"]
        asked.append((body["bNum"], body["eNum"]))
        return httpx.Response(200, json={"code": "0", "data": {"coLst": windows[body["bNum"]], "caLst": []}})

    profile = profiles_of({"ST_APP_SHARE139": json.dumps(PROFILE)})[0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(listing)) as http:
        card = await Share139Client(profile, lambda: http, interval=0).card("1")
    assert asked == [(1, 200), (201, 400)]
    assert len(card.lines[0].items) == 201 and card.lines[0].items[-1].label == "E201.mp4"


async def test_a_folder_that_does_not_end_or_comes_again_is_no_card_a_part_is_never_given_as_the_whole(world):
    cloud, client = world
    count = []

    def endless(request: httpx.Request) -> httpx.Response:
        count.append(1)
        start = json.loads(request.content)["getOutLinkInfoReq"]["bNum"]
        return httpx.Response(200, json={"code": "0", "data": {"coLst": files(*((f"E{start + n}.mp4", f"c{start + n}", {})
                                                                               for n in range(LISTED_AT_MOST))), "caLst": []}})

    profile = profiles_of({"ST_APP_SHARE139": json.dumps(PROFILE)})[0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(endless)) as http:
        with pytest.raises(AppError) as large:
            await Share139Client(profile, lambda: http, interval=0).card("1")
    assert "app_folder_too_large" in large.value.reason and str(CATALOG_REQUESTS * LISTED_AT_MOST) in large.value.reason
    assert len(count) == CATALOG_REQUESTS
    cloud.listing["root"] = {"coLst": files(*((f"E{n}.mp4", f"c{n}", {}) for n in range(LISTED_AT_MOST))), "caLst": []}
    with pytest.raises(AppError) as repeated:
        await client.card("1")
    assert "app_pagination_repeated" in repeated.value.reason
    with pytest.raises(AppError) as absent:
        await client.card("9")
    assert "app_title_absent" in absent.value.reason


async def test_a_folder_of_folders_is_no_card_of_episodes_and_names_its_folders(world):
    cloud, client = world
    cloud.listing["root"] = {"coLst": [], "caLst": [{"caID": "a", "caName": "第一季"}, {"caID": "b", "caName": "第二季"}]}
    with pytest.raises(AppError) as chosen:
        await client.card("1")
    assert "app_folder_selection_required" in chosen.value.reason and "第一季, 第二季" in chosen.value.reason
    # A folder that holds videos and folders is read for its own videos.
    cloud.listing["root"] = {"coLst": files(("E1.mp4", "e1", {"width": "3840"})), "caLst": [{"caID": "a", "caName": "花絮"}]}
    assert [each.label for each in (await client.card("1")).lines[0].items] == ["E1.mp4"]


async def test_every_resolve_asks_the_cloud_for_the_preview_and_no_address_is_kept(world):
    cloud, client = world
    cloud.present["c2"] = "https://cdn.model/master.m3u8"
    first, second = await client.resolve("preview4k", "c2", "1"), await client.resolve("preview4k", "c2", "1")
    assert first.address != second.address and first.address.startswith("https://cdn.model/master.m3u8?sig=")
    assert first.headers["Referer"] == "https://yun.139.com/"
    assert [(method, body["getContentInfoFromOutLinkReq"]) for method, body in cloud.bodies] == [
        ("getContentInfoFromOutLink", {"contentId": "c2", "linkID": "public-link", "account": ""})] * 2


async def test_a_file_without_a_preview_and_a_refusal_of_the_cloud_are_told_apart(world):
    cloud, client = world
    with pytest.raises(AppError) as none:
        await client.resolve("preview4k", "gone", "1")
    assert "app_no_stream" in none.value.reason and not none.value.transient
    cloud.refuse = "分享已取消"
    with pytest.raises(AppError) as refused:
        await client.resolve("preview4k", "gone", "1")
    assert "app_refused" in refused.value.reason and "分享已取消" in refused.value.reason


async def test_an_answer_that_is_no_answer_of_the_cloud_and_a_host_that_is_down_are_told_apart():
    profile = profiles_of({"ST_APP_SHARE139": json.dumps(PROFILE)})[0]
    for handler, transient, code in ((lambda request: httpx.Response(503, text=""), True, "app_status"),
                                     (lambda request: httpx.Response(200, text="<html>"), False, "app_answer_broken")):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(AppError) as failed:
                await Share139Client(profile, lambda: http, interval=0).resolve("preview4k", "c2", "1")
            assert code in failed.value.reason and failed.value.transient is transient

    def down(request: httpx.Request):
        raise httpx.ConnectTimeout("down", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        with pytest.raises(AppError) as unreachable:
            await Share139Client(profile, lambda: http, interval=0).card("1")
        assert unreachable.value.transient and "app_unreachable" in unreachable.value.reason


async def test_the_search_finds_the_shares_the_profile_connects_by_the_words_of_their_titles(world):
    _cloud, client = world
    page = await client.search_page("高武", 1)
    assert [(each["id"], each["name"], each["year"]) for each in page.cards] == [("1", "东大高武学院", "2024")]
    assert (page.pages, page.total) == (1, 1)
    assert (await client.search_page("高武", 2)).cards == [] and (await client.search_page("没有", 1)).cards == []
    assert [each["id"] for each in (await client.search_page("", 1)).cards] == ["1", "2"]
