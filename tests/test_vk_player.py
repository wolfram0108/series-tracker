import json

import httpx
import pytest

from modules.sources.players.contract import Answer, Kind, PlayerRef
from modules.sources.players.vk import VkPlayer, video_of

EMBED = "https://vkvideo.ru/video_ext.php?oid=-170515668&id=456256320&hash=5b73"
REF = PlayerRef("vk", "https://vk.com/video-170515668_456256320", EMBED)
PAGE = "https://1.ruchime.org/release/test/episode-1"


def shown(**params) -> str:
    return json.dumps({"payload": [0, ["T", "<div/>", {"player": {"type": "vk", "params": [params]}}]]})


GOOD = shown(url720="https://cdn/720.mp4", url2160="https://cdn/2160.mp4")
DENIED = '{"payload":["8",["\\"Ошибка доступа (1)\\"","false","\\"\\""]]}'
GONE = '{"payload":["8",["\\"Видеозапись недоступна, так как была удалена.\\"","false","\\"\\""]]}'
SHELL = "<html><head><title>VK</title></head></html>"


def vk(show: str, seen: list | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((request.method, request.url.path, request.content.decode(), dict(request.headers)))
        return httpx.Response(200, text=show)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("url,video", [("https://vk.com/video-170515668_456256320", "-170515668_456256320"),
                                       (EMBED, "-170515668_456256320"),
                                       ("https://vkvideo.ru/video_ext.php?oid=7&amp;id=9&amp;hash=x", "7_9"),
                                       ("https://example.com/page", None), ("", None)])
def test_the_video_is_named_by_a_direct_link_and_by_an_embed_address(url, video):
    assert video_of(url) == video


def test_a_page_player_gives_the_direct_link_as_its_address():
    button = f"<button onclick=\"switchSource('{EMBED.replace('&', '&amp;')}',this)\">VK</button>"

    ref = VkPlayer().find(button, PAGE)

    assert (ref.url, ref.raw_url) == ("https://vk.com/video-170515668_456256320", EMBED)


@pytest.mark.asyncio
async def test_the_direct_call_is_the_only_request_and_carries_the_video_and_the_marks_of_an_ajax_request():
    seen = []

    answer = await VkPlayer().fetch(REF, vk(GOOD, seen), PAGE)

    method, path, body, headers = seen[0]
    assert (method, path, len(seen)) == ("POST", "/al_video.php", 1)
    assert "act=show" in body and "video=-170515668_456256320" in body and "al=1" in body
    assert headers["x-requested-with"] == "XMLHttpRequest"
    assert VkPlayer().read(answer).kind is Kind.PLAYABLE


@pytest.mark.asyncio
async def test_a_video_the_direct_call_refuses_stays_refused_and_the_player_page_is_not_asked():
    seen = []

    answer = await VkPlayer().fetch(REF, vk(DENIED, seen), PAGE)

    assert [method for method, *_ in seen] == ["POST"]
    assert VkPlayer().read(answer).kind is Kind.RESTRICTED


@pytest.mark.asyncio
async def test_an_address_without_a_video_is_no_answer():
    answer = await VkPlayer().fetch(PlayerRef("vk", "https://example.com/x", ""), vk(GOOD), PAGE)

    assert VkPlayer().read(answer).kind is Kind.NO_ANSWER


@pytest.mark.parametrize("answer,kind", [(Answer(200, GOOD), Kind.PLAYABLE), (Answer(200, GONE), Kind.NOT_FOUND),
                                         (Answer(200, DENIED), Kind.RESTRICTED), (Answer(200, SHELL), Kind.NOT_PLAYER),
                                         (Answer(None, error="ReadTimeout"), Kind.NO_ANSWER), (Answer(429, ""), Kind.HTTP_ERROR)])
def test_the_reading_follows_the_kind_of_what_came_back(answer, kind):
    assert VkPlayer().read(answer).kind is kind


@pytest.mark.parametrize("ref,address", [(REF, "https://vkvideo.ru/video-170515668_456256320"),
                                         (PlayerRef("vk", "https://example.com/x", ""), None)])
def test_the_downloader_is_given_the_direct_link_and_nothing_else(ref, address):
    assert VkPlayer().download_address(ref) == address
