import json
from urllib.parse import parse_qsl

import httpx
import pytest

from modules.sources.players.contract import Kind
from modules.sources.players.vk import VkPlayer

PAGE = "https://1.ruchime.org/release/test/episode-1"
SHELL = "<html><head><title>VK</title></head><body></body></html>"
SHOW_DENIED = '{"payload":["8",["\\"Ошибка доступа (1)\\"","false","\\"\\""]]}'


def embed(video: int, hash_: str = "h") -> str:
    return f"https://vkvideo.ru/video_ext.php?oid=-10&id={video}&hash={hash_}"


def ref_of(video: int, hash_: str = "h"):
    return VkPlayer.ref_of(embed(video, hash_))


def shell(token: str = "tok-1", expires: int = 4102444800) -> str:
    return (f'<html><head><title>VK</title></head><body><script>init = {{webToken: {{"access_token": "{token}", "expired_at": {expires}}}, '
            f'apiConfigDomains: {{"apiDomain": "api.vkvideo.ru"}}}};</script></body></html>')


def item(video: int, *heights: int, **extra) -> dict:
    files = {f"mp4_{height}": f"https://cdn.test/{video}/{height}.mp4" for height in heights}
    return {"owner_id": -10, "id": video, **({"files": files} if heights else {}), **extra}


def answer(*items: dict) -> str:
    return json.dumps({"response": {"items": list(items)}})


def shown(*heights: int) -> str:
    params = {f"url{height}": f"https://cdn.test/shown/{height}.mp4" for height in heights}
    return json.dumps({"payload": [0, ["T", "<div/>", {"player": {"type": "vk", "params": [params]}}]]})


class Vk:
    """A VK that answers by what a test says: player pages by video, the API by a function of the form, the page call."""

    def __init__(self, pages=None, api=None, show=None) -> None:
        self.pages, self.api, self.show, self.seen = pages or {}, api, show, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = dict(parse_qsl(request.content.decode()))
        self.seen.append((request.method, request.url.host, request.url.path, form, dict(request.headers)))
        if request.url.path == "/video_ext.php":
            page = self.pages.get(int(request.url.params["id"]), SHELL)
            return httpx.Response(200, text=page(request) if callable(page) else page)
        if request.url.path == "/method/video.get":
            return httpx.Response(200, text=self.api(form) if self.api else answer())
        return httpx.Response(200, text=self.show(form) if self.show else SHOW_DENIED)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def paths(self) -> list[str]:
        return [path for _method, _host, path, _form, _headers in self.seen]

    def forms(self, path: str) -> list[dict]:
        return [form for _method, _host, seen_path, form, _headers in self.seen if seen_path == path]

    def agents(self, path: str) -> list[str]:
        return [headers["user-agent"] for _method, _host, seen_path, _form, headers in self.seen if seen_path == path]


async def look(vk: Vk, video: int = 20, want_files: bool = False, player: VkPlayer | None = None, http=None):
    player = player or VkPlayer()
    got = await player._answer(ref_of(video), http or vk.client(), PAGE, want_files)
    return player.read(got)


@pytest.mark.asyncio
async def test_a_page_that_names_heights_without_addresses_is_playable_for_a_probe_but_not_enough_for_a_download():
    page = "<html><head><title>Video embed</title></head><body>mp4_480 mp4_720</body></html>"

    probe = await look(Vk(pages={20: page}))
    download = await look(Vk(pages={20: page}, show=lambda form: shown(480)), want_files=True)

    assert (probe.kind, probe.resolutions, probe.files) == (Kind.PLAYABLE, (480, 720), ())
    assert (download.kind, download.files) == (Kind.PLAYABLE, ((480, "https://cdn.test/shown/480.mp4"),))


@pytest.mark.asyncio
async def test_a_rejected_token_is_dropped_so_the_next_ask_takes_a_new_one():
    calls = []

    def api(form):
        calls.append(form["access_token"])
        if form["access_token"] == "old":
            return json.dumps({"error": {"error_code": 5, "error_msg": "User authorization failed"}})
        return answer(item(20, 720))

    pages = iter([shell("old"), shell("new")])
    vk = Vk(pages={20: lambda request: next(pages)}, api=api)
    player = VkPlayer()
    http = vk.client()

    first = await look(vk, 20, player=player, http=http)
    second = await look(vk, 20, player=player, http=http)

    assert first.kind is not Kind.PLAYABLE
    assert (second.kind, calls) == (Kind.PLAYABLE, ["old", "new"])


# --- the batch call ----------------------------------------------------------------------------------------------

def refs(count: int, first: int = 100):
    return [ref_of(first + number) for number in range(count)]


@pytest.mark.asyncio
async def test_a_token_refused_twice_ends_the_batch_without_going_on_for_ever():
    vk = Vk(pages={100: shell(), 105: shell(), 109: shell()},
            api=lambda form: json.dumps({"error": {"error_code": 5, "error_msg": "expired"}}))

    settled = await VkPlayer().read_batch(refs(10), vk.client(), PAGE)

    assert settled == {}
    assert len(vk.forms("/method/video.get")) <= 3
