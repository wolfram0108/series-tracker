import json
from types import SimpleNamespace
from urllib.parse import parse_qsl

import httpx
import pytest

from modules.sources.players import vk as vk_module
from modules.sources.players.contract import Answer, Kind
from modules.sources.players.vk import AGENTS, VkPlayer
from modules.sources.policy import settle

PAGE = "https://1.ruchime.org/release/test/episode-1"
SHELL = "<html><head><title>VK</title></head><body></body></html>"
PRIVATE = "<html><head><title>Video embed</title></head><body><div>Это видео скрыто настройками приватности</div></body></html>"
SHOW_DENIED = '{"payload":["8",["\\"Ошибка доступа (1)\\"","false","\\"\\""]]}'


def embed(video: int, hash_: str = "h") -> str:
    return f"https://vkvideo.ru/video_ext.php?oid=-10&id={video}&hash={hash_}"


def ref_of(video: int, hash_: str = "h"):
    return VkPlayer.ref_of(embed(video, hash_))


def old_page(video: int, *heights: int) -> str:
    params = {"vid": video, **{f"mp4_{height}": f"https://cdn.test/{video}/{height}.mp4" for height in heights}}
    return f'<html><head><title>Video embed</title></head><body><script>\nvar playerParams = {{"params": [{json.dumps(params)}]}};\n</script></body></html>'


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


def test_a_player_found_on_a_page_keeps_its_embed_address_with_the_key():
    button = f"<button onclick=\"switchSource('{embed(20).replace('&', '&amp;')}',this)\">VK</button>"

    ref = VkPlayer().find(button, PAGE)

    assert (ref.url, ref.raw_url) == ("https://vk.com/video-10_20", embed(20))


def test_the_downloader_is_given_the_player_link_with_its_key_and_nothing_without_one():
    player = VkPlayer()

    assert player.download_address(ref_of(20)) == embed(20)
    assert player.download_address(VkPlayer.ref_of("https://vkvideo.ru/video_ext.php?oid=-10&id=20")) is None
    assert player.owns(embed(20)) and not player.owns("https://vkvideo.ru/video-10_20")


@pytest.mark.asyncio
async def test_a_page_that_lists_the_files_is_enough_and_nothing_else_is_asked():
    vk = Vk(pages={20: old_page(20, 144, 720)})

    reading = await look(vk)

    assert (reading.kind, reading.resolutions, vk.paths()) == (Kind.PLAYABLE, (144, 720), ["/video_ext.php"])


@pytest.mark.asyncio
async def test_a_shell_page_gives_the_token_and_the_api_gives_the_files():
    vk = Vk(pages={20: shell()}, api=lambda form: answer(item(20, 720, 2160)))

    reading = await look(vk)

    assert (reading.kind, reading.resolutions) == (Kind.PLAYABLE, (720, 2160))
    assert vk.paths() == ["/video_ext.php", "/method/video.get"]
    form = vk.forms("/method/video.get")[0]
    assert (form["videos"], form["is_embed"], form["ref_domain"], form["access_token"]) == ("-10_20_h", "1", "1.ruchime.org", "tok-1")


@pytest.mark.asyncio
async def test_a_token_taken_once_is_used_for_the_next_video_without_asking_the_page():
    vk = Vk(pages={20: shell(), 21: shell()}, api=lambda form: answer(item(20, 720), item(21, 480)))
    player, http = VkPlayer(), None
    http = vk.client()

    await look(vk, 20, player=player, http=http)
    second = await look(vk, 21, player=player, http=http)

    assert (second.kind, second.resolutions) == (Kind.PLAYABLE, (480,))
    assert vk.paths() == ["/video_ext.php", "/method/video.get", "/method/video.get"]


@pytest.mark.asyncio
async def test_a_token_is_not_shared_with_another_session():
    vk = Vk(pages={20: shell()}, api=lambda form: answer(item(20, 720)))
    player = VkPlayer()

    await look(vk, 20, player=player)
    await look(vk, 20, player=player)

    assert vk.paths().count("/video_ext.php") == 2


@pytest.mark.asyncio
async def test_a_token_whose_time_has_run_out_is_not_used(monkeypatch):
    vk = Vk(pages={20: shell(expires=2_000_000_000)}, api=lambda form: answer(item(20, 720)))
    player, http = VkPlayer(), None
    http = vk.client()
    monkeypatch.setattr(vk_module, "time", SimpleNamespace(time=lambda: 1_000_000_000.0))
    await look(vk, 20, player=player, http=http)
    monkeypatch.setattr(vk_module, "time", SimpleNamespace(time=lambda: 2_000_000_000.0))

    await look(vk, 20, player=player, http=http)

    assert vk.paths().count("/video_ext.php") == 2


@pytest.mark.asyncio
async def test_a_refusal_the_player_page_prints_closes_the_video_and_no_other_entry_is_asked():
    vk = Vk(pages={20: PRIVATE})

    reading = await look(vk)

    assert (reading.kind, vk.paths()) == (Kind.RESTRICTED, ["/video_ext.php"])


@pytest.mark.asyncio
async def test_a_refusal_of_the_api_about_the_video_closes_it():
    vk = Vk(pages={20: shell()}, api=lambda form: json.dumps({"error": {"error_code": 15, "error_msg": "Access denied"}}))

    assert (await look(vk)).kind is Kind.RESTRICTED


@pytest.mark.asyncio
async def test_a_video_the_api_leaves_out_and_the_page_calls_private_is_closed():
    vk = Vk(pages={20: shell(), 21: PRIVATE}, api=lambda form: answer(item(20, 720)))
    player = VkPlayer()
    http = vk.client()
    await look(vk, 20, player=player, http=http)

    reading = await look(vk, 21, player=player, http=http)

    assert reading.kind is Kind.RESTRICTED
    assert vk.paths() == ["/video_ext.php", "/method/video.get", "/method/video.get", "/video_ext.php"]


@pytest.mark.asyncio
async def test_the_refusal_of_the_page_call_does_not_close_a_video_nothing_else_has_spoken_for():
    vk = Vk(pages={20: SHELL}, show=lambda form: SHOW_DENIED)

    reading = await look(vk)

    assert reading.kind is Kind.NOT_PLAYER
    assert vk.paths() == ["/video_ext.php", "/al_video.php"]


@pytest.mark.asyncio
async def test_the_page_call_is_one_more_source_of_files_when_the_others_have_none():
    vk = Vk(pages={20: SHELL}, show=lambda form: shown(360, 1080))

    reading = await look(vk, want_files=True)

    assert (reading.kind, reading.resolutions) == (Kind.PLAYABLE, (360, 1080))
    assert dict(reading.files)[1080] == "https://cdn.test/shown/1080.mp4"
    assert vk.forms("/al_video.php")[0]["video"] == "-10_20"


@pytest.mark.asyncio
async def test_a_page_that_names_heights_without_addresses_is_playable_for_a_probe_but_not_enough_for_a_download():
    page = "<html><head><title>Video embed</title></head><body>mp4_480 mp4_720</body></html>"

    probe = await look(Vk(pages={20: page}))
    download = await look(Vk(pages={20: page}, show=lambda form: shown(480)), want_files=True)

    assert (probe.kind, probe.resolutions, probe.files) == (Kind.PLAYABLE, (480, 720), ())
    assert (download.kind, download.files) == (Kind.PLAYABLE, ((480, "https://cdn.test/shown/480.mp4"),))


@pytest.mark.asyncio
async def test_a_video_no_entry_could_read_is_not_sure_and_asked_again_by_the_policy():
    vk = Vk(pages={20: SHELL})

    verdict = await settle(VkPlayer(), ref_of(20), vk.client(), PAGE)

    assert (verdict.reading.kind, verdict.attempts, verdict.exhausted) == (Kind.NOT_PLAYER, 3, True)


def test_an_answer_without_a_reading_is_no_answer():
    assert VkPlayer().read(Answer(None, error="ReadTimeout")).kind is Kind.NO_ANSWER


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
async def test_more_than_fifty_players_go_out_in_batches_of_fifty_even_with_a_hundred_asked():
    vk = Vk(pages={100: shell()}, api=lambda form: answer(*(item(int(key.split("_")[1]), 720) for key in form["videos"].split(","))))
    asked = refs(121)

    settled = await VkPlayer().read_batch(asked, vk.client(), PAGE)

    assert [len(form["videos"].split(",")) for form in vk.forms("/method/video.get")] == [50, 50, 21]
    assert len(settled) == 121 and all(reading.kind is Kind.PLAYABLE for reading in settled.values())


@pytest.mark.asyncio
async def test_a_batch_answer_is_matched_by_the_video_whatever_its_order_and_a_missing_video_is_left_out():
    vk = Vk(pages={100: shell(), 101: shell(), 102: shell()}, api=lambda form: answer(item(102, 480), item(100, 1080)))
    asked = refs(3)

    settled = await VkPlayer().read_batch(asked, vk.client(), PAGE)

    assert {ref.url: reading.resolutions for ref, reading in settled.items()} == {"https://vk.com/video-10_102": (480,),
                                                                                  "https://vk.com/video-10_100": (1080,)}


@pytest.mark.asyncio
async def test_a_video_that_states_its_restriction_in_a_batch_is_closed_and_one_that_lists_nothing_is_left_to_the_single_ask():
    vk = Vk(pages={100: shell()}, api=lambda form: answer(item(100, 720), item(101, content_restricted=1, content_restricted_message="Closed"), item(102)))

    settled = await VkPlayer().read_batch(refs(3), vk.client(), PAGE)

    assert {ref.url.rsplit("_", 1)[1]: reading.kind for ref, reading in settled.items()} == {"100": Kind.PLAYABLE, "101": Kind.RESTRICTED}


@pytest.mark.asyncio
async def test_the_token_is_looked_for_in_three_players_under_both_agents_and_then_given_up():
    vk = Vk(pages={}, api=lambda form: answer())

    settled = await VkPlayer().read_batch(refs(10), vk.client(), PAGE)

    assert settled == {}
    assert vk.paths() == ["/video_ext.php"] * 6
    assert vk.agents("/video_ext.php") == list(AGENTS) * 3


@pytest.mark.asyncio
async def test_the_second_agent_gets_the_token_when_the_first_gets_a_page_without_one():
    def page(request):
        return shell() if request.headers["user-agent"] == AGENTS[1] else old_page(100, 720)

    vk = Vk(pages={100: page, 105: page, 109: page}, api=lambda form: answer(item(100, 720), item(101, 480)))

    settled = await VkPlayer().read_batch(refs(10), vk.client(), PAGE)

    assert vk.paths() == ["/video_ext.php", "/video_ext.php", "/method/video.get"]
    assert vk.forms("/method/video.get")[0]["access_token"] == "tok-1"
    assert len(settled) == 2


@pytest.mark.asyncio
async def test_a_token_the_api_refuses_is_taken_again_once_and_the_batch_is_repeated():
    tokens = iter(["old", "new"])

    def api(form):
        if form["access_token"] == "old":
            return json.dumps({"error": {"error_code": 5, "error_msg": "expired"}})
        return answer(item(100, 720), item(101, 480))

    vk = Vk(pages={100: lambda request: shell(next(tokens)), 105: shell("new"), 109: shell("new")}, api=api)

    settled = await VkPlayer().read_batch(refs(10), vk.client(), PAGE)

    assert [form["access_token"] for form in vk.forms("/method/video.get")] == ["old", "new"]
    assert len(settled) == 2


@pytest.mark.asyncio
async def test_a_token_refused_twice_ends_the_batch_without_going_on_for_ever():
    vk = Vk(pages={100: shell(), 105: shell(), 109: shell()},
            api=lambda form: json.dumps({"error": {"error_code": 5, "error_msg": "expired"}}))

    settled = await VkPlayer().read_batch(refs(10), vk.client(), PAGE)

    assert settled == {}
    assert len(vk.forms("/method/video.get")) <= 3


@pytest.mark.asyncio
async def test_one_player_is_not_a_batch_and_a_link_without_a_key_is_not_asked_for():
    vk = Vk(pages={100: shell()}, api=lambda form: answer(item(100, 720)))
    keyless = VkPlayer.ref_of("https://vkvideo.ru/video_ext.php?oid=-10&id=101")

    assert await VkPlayer().read_batch([ref_of(100)], vk.client(), PAGE) == {}
    assert await VkPlayer().read_batch([ref_of(100), keyless], vk.client(), PAGE) == {}
    assert vk.seen == []
