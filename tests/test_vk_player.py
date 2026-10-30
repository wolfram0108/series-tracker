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
PLAYER_PAGE = '<html><head><title>Video embed</title></head><script>{"mp4_720":"https:\\/\\/cdn\\/p720.mp4","mp4_1080":"https:\\/\\/cdn\\/p1080.mp4"}</script></html>'
SHELL = "<html><head><title>VK</title></head></html>"


def vk(show: str, page: str = PLAYER_PAGE, seen: list | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((request.method, request.url.path, request.content.decode(), dict(request.headers)))
        if request.method == "POST":
            return httpx.Response(200, text=show)
        return httpx.Response(200, text=page)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("url,video", [("https://vk.com/video-170515668_456256320", "-170515668_456256320"),
                                       (EMBED, "-170515668_456256320"),
                                       ("https://vkvideo.ru/video_ext.php?oid=7&amp;id=9&amp;hash=x", "7_9"),
                                       ("https://example.com/page", None), ("", None)])
def test_the_video_is_named_by_a_direct_link_and_by_an_embed_address(url, video):
    assert video_of(url) == video


@pytest.mark.asyncio
async def test_the_direct_call_goes_first_with_the_video_and_the_marks_of_an_ajax_request():
    seen = []

    answer = await VkPlayer().fetch(REF, vk(GOOD, seen=seen), PAGE)

    method, path, body, headers = seen[0]
    assert (method, path, len(seen)) == ("POST", "/al_video.php", 1)
    assert "act=show" in body and "video=-170515668_456256320" in body and "al=1" in body
    assert headers["x-requested-with"] == "XMLHttpRequest"
    assert VkPlayer().read(answer).kind is Kind.PLAYABLE


@pytest.mark.asyncio
async def test_a_video_the_direct_call_refuses_is_looked_at_through_its_player_page():
    seen = []

    answer = await VkPlayer().fetch(REF, vk(DENIED, seen=seen), PAGE)

    assert [method for method, *_ in seen] == ["POST", "GET"]
    assert VkPlayer().read(answer).resolutions == (720, 1080)


@pytest.mark.asyncio
async def test_a_refused_video_whose_player_page_is_the_shell_stays_unsure_not_closed():
    answer = await VkPlayer().fetch(REF, vk(DENIED, SHELL), PAGE)

    assert VkPlayer().read(answer).kind is Kind.NOT_PLAYER


@pytest.mark.asyncio
async def test_a_deleted_video_is_not_looked_for_in_the_player_page():
    seen = []

    answer = await VkPlayer().fetch(REF, vk(GONE, seen=seen), PAGE)

    assert [method for method, *_ in seen] == ["POST"]
    assert VkPlayer().read(answer).kind is Kind.NOT_FOUND


@pytest.mark.asyncio
async def test_without_an_embed_address_a_refusal_has_no_page_to_fall_back_to():
    seen = []
    ref = PlayerRef("vk", "https://vk.com/video-1_2", "")

    answer = await VkPlayer().fetch(ref, vk(DENIED, seen=seen), PAGE)

    assert [method for method, *_ in seen] == ["POST"] and VkPlayer().read(answer).kind is Kind.RESTRICTED


@pytest.mark.parametrize("answer,kind", [(Answer(200, GOOD), Kind.PLAYABLE), (Answer(200, PLAYER_PAGE), Kind.PLAYABLE),
                                         (Answer(200, SHELL), Kind.NOT_PLAYER), (Answer(None, error="ReadTimeout"), Kind.NO_ANSWER),
                                         (Answer(200, DENIED), Kind.RESTRICTED)])
def test_the_reading_follows_the_kind_of_what_came_back(answer, kind):
    assert VkPlayer().read(answer).kind is kind


@pytest.mark.asyncio
async def test_the_stream_for_a_download_is_the_highest_file_of_the_direct_call():
    stream = await VkPlayer().resolve("https://vk.com/video-170515668_456256320", vk(GOOD))

    assert stream == {"stream_url": "https://cdn/2160.mp4", "headers": {"Referer": "https://vk.com/"}}


@pytest.mark.asyncio
async def test_a_refused_embed_address_gives_the_stream_of_its_player_page():
    stream = await VkPlayer().resolve(EMBED, vk(DENIED))

    assert stream["stream_url"] == "https://cdn/p1080.mp4"


@pytest.mark.asyncio
async def test_a_direct_link_that_is_refused_gives_no_stream():
    assert await VkPlayer().resolve("https://vk.com/video-1_2", vk(DENIED)) is None
