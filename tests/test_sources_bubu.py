"""The Android client of 布布 (modules/sources/apps/bubu.py): the signed request, the answer sealed under a key of the build, the
card with its lines, the stream of an episode and the refusals of the service — against a model of the service by its
protocol (tests/app_protocol_models.py, BubuModel)."""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from modules.sources.apps import AppError, AppProfile, parsed_address
from modules.sources.apps.bubu import BubuClient
from app_protocol_models import BubuModel

STREAM = "https://cdn.model/1/index.m3u8"


@pytest.fixture
async def world():
    model = BubuModel()
    model.card(214, "丹道至尊", "zijianm3u8", [("第1集", "tok-1", STREAM), ("第2集", "tok-2", "https://cdn.model/2/index.m3u8")])
    async with httpx.AsyncClient(transport=httpx.MockTransport(model.handle)) as http:
        yield model, BubuClient(model.profile(), lambda: http, clock=lambda: 1731300000.0, nonce=lambda: "12345678")


def test_an_address_may_be_the_whole_data_of_an_answer_as_well_as_a_key_of_it():
    assert parsed_address({"code": 1, "msg": "ok", "data": STREAM}) == (STREAM, "ok")
    assert parsed_address({"code": 1, "url": STREAM}) == (STREAM, "")
    assert parsed_address({"code": 1, "msg": "ok", "data": "not an address"}) == ("", "ok")
    assert parsed_address({"code": 403, "msg": "no", "data": STREAM}) == ("", "no")


async def test_a_request_carries_the_build_and_a_signature_the_service_checks_and_the_device_of_the_profile(world):
    model, client = world
    headers = client.headers()
    build = model.BUILD
    nonce = hashlib.sha256(b"11111111-2222-3333-4444-555555555555").hexdigest()[:8].upper() + "12345678"
    value = f"finger={build['finger']}&id={build['id']}&nonce={nonce}&sk={build['sk']}&time=1731300000000&v=8"
    assert headers["x-nonc"] == nonce and headers["x-sign"] == hashlib.sha256(value.encode()).hexdigest().upper()
    assert (headers["x-aid"], headers["x-ave"], headers["x-avn"], headers["x-time"]) == ("aid-model", "8", "1.6.1", "1731300000000")
    assert headers["x-device-id"] == "11111111-2222-3333-4444-555555555555" and headers["Accept"] == "application/json"
    await client.search_page("丹道", 1)
    assert model.devices == {"11111111-2222-3333-4444-555555555555"}


async def test_the_card_lists_its_lines_with_their_episodes(world):
    _model, client = world
    card = await client.card("214")
    assert (card.names, card.year) == (["丹道至尊"], "2021")
    assert [(line.code, line.listed, [(each.place, each.label, each.token) for each in line.items]) for line in card.lines] == [
        ("zijianm3u8", False, [(1, "第1集", "tok-1"), (2, "第2集", "tok-2")])]
    with pytest.raises(AppError) as absent:
        await client.card("999")
    assert "app_title_absent" in absent.value.reason


async def test_the_stream_of_an_episode_is_the_address_of_the_answer_with_the_agent_of_a_player(world):
    model, client = world
    stream = await client.resolve("zijianm3u8", "tok-1", "214")
    assert stream.address == STREAM and "Chrome" in stream.headers["User-Agent"]
    request = next(url for url in model.asked if "decode" in url)
    assert "vodFrom=zijianm3u8" in request and "url=tok-1" in request


async def test_a_refusal_of_the_service_in_words_is_its_answer_about_the_episode_and_silence_is_a_failure_of_the_way(world):
    model, client = world
    model.words["tok-1"] = (403, "今日解析次数已达上限")
    with pytest.raises(AppError) as spent:
        await client.resolve("zijianm3u8", "tok-1", "214")
    assert "今日解析次数已达上限" in spent.value.reason and not spent.value.transient and spent.value.daily_quota
    model.words["tok-2"] = (1, "")  # parsed, said nothing, gave no address
    with pytest.raises(AppError) as silent:
        await client.resolve("zijianm3u8", "tok-2", "214")
    assert silent.value.transient


async def test_a_line_not_held_is_asked_by_its_code_after_the_card_is_asked_again(world):
    model, client = world
    await client.resolve("zijianm3u8", "tok-1", "214")
    assert [url for url in model.asked if "get_detail" in url] != []


async def test_a_request_the_service_does_not_accept_is_shown_as_the_refusal_it_is(world):
    model, _client = world
    profile = model.profile()
    other = AppProfile(profile.name, profile.label, profile.protocol, {**profile.settings, "sk": "another"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(model.handle)) as http:
        with pytest.raises(AppError) as refused:
            await BubuClient(other, lambda: http).search_page("丹道", 1)
    assert "app_refused" in refused.value.reason and "LOGIN_USERNAME_EMPTY" in refused.value.reason


async def test_an_answer_that_cannot_be_opened_and_a_status_with_no_answer_are_told_apart(world):
    model, _client = world
    profile = model.profile()

    def tampered(request: httpx.Request) -> httpx.Response:
        answer = model.handle(request)
        return httpx.Response(200, content=answer.content[:-1] + bytes([answer.content[-1] ^ 1]), headers=answer.headers)

    def wrong_key(request: httpx.Request) -> httpx.Response:
        # Sealed under the key 0 but said to be under the key 1.
        return httpx.Response(200, content=model.sealed({"code": 200, "data": []}, number=0).content,
                              headers={model.KEY_HEADER: "1"})

    for handler in (tampered, wrong_key, lambda request: httpx.Response(200, content=b"x" * 40, headers={model.KEY_HEADER: "9"}),
                    lambda request: httpx.Response(200, content=b"x" * 40)):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(AppError) as broken:
                await BubuClient(profile, lambda: http).search_page("丹道", 1)
        assert "app_answer_broken" in broken.value.reason
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503, text=""))) as http:
        with pytest.raises(AppError) as down:
            await BubuClient(profile, lambda: http).search_page("丹道", 1)
    assert down.value.transient and "app_status" in down.value.reason


async def test_the_search_gives_the_cards_as_the_application_gives_them(world):
    _model, client = world
    page = await client.search_page("丹道", 1)
    assert [(each["id"], each["name"], each["year"]) for each in page.cards] == [(214, "丹道至尊", "2021")]
    assert (page.pages, page.total) == (1, 1)
