"""The Vibix player: the catalog is captured once for a series, an episode reads from its best live manifest."""
from types import SimpleNamespace

import httpx
import pytest

from modules.sources.players import vibix
from modules.sources.players.contract import Kind, PlayerRef
from modules.sources.players.vibix import VibixPlayer, link_of, parse_link, pick_season
from modules.sources.players.vibix_protocol import Episode, Stream, encode_envelope

KEY = b"K" * 32
MANIFEST = b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.000,\nseg.ts\n#EXT-X-ENDLIST\n"
PAGE = "https://1.ruchime.org/release/test/episode-1"


def client(statuses: dict[str, int]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        for marker, status in statuses.items():
            if marker in str(request.url):
                return httpx.Response(status, content=encode_envelope(MANIFEST, KEY) if status == 200 else b"<html/>")
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def catalog(player: VibixPlayer, monkeypatch, *episodes: Episode) -> None:
    async def playlist(page_url: str):
        return list(episodes), KEY
    monkeypatch.setattr(player, "playlist", playlist)


def episode(number: int, *streams: Stream, season: int = 1) -> Episode:
    return Episode(number, f"Серия {number}", f"Сезон {season}", streams, season)


async def read(player: VibixPlayer, http: httpx.AsyncClient, link: str):
    return player.read(await player.fetch(PlayerRef("vibix", link), http, PAGE))


def test_the_link_names_the_season_and_the_episode_and_nothing_else_parses():
    assert parse_link(link_of(PAGE, 2, 7)) == (2, 7)
    assert parse_link(PAGE) is None and parse_link(PAGE + "#cvh=1") is None


def test_several_seasons_need_a_choice_and_a_missing_season_is_refused():
    both = [episode(1, season=1), episode(1, season=2)]
    assert pick_season([episode(1)], None) == (1, "")
    assert pick_season(both, 2) == (2, "")
    assert pick_season(both, None)[0] is None and pick_season(both, 3)[0] is None


@pytest.mark.asyncio
async def test_an_episode_takes_the_best_manifest_that_reads(monkeypatch):
    player = VibixPlayer()
    catalog(player, monkeypatch, episode(1, Stream(1080, "https://cdn.test/dead.m3u8"), Stream(720, "https://cdn.test/live.m3u8")))
    async with client({"dead.m3u8": 503, "live.m3u8": 200}) as http:
        reading = await read(player, http, link_of(PAGE, 1, 1))
    assert reading.kind is Kind.PLAYABLE and reading.resolutions == (720,)


@pytest.mark.asyncio
async def test_when_no_manifest_reads_the_last_refusal_is_the_reading(monkeypatch):
    player = VibixPlayer()
    catalog(player, monkeypatch, episode(1, Stream(480, "https://cdn.test/dead.m3u8")))
    async with client({"dead.m3u8": 400}) as http:
        reading = await read(player, http, link_of(PAGE, 1, 1))
    assert (reading.kind, reading.status) == (Kind.HTTP_ERROR, 400)


@pytest.mark.asyncio
async def test_an_episode_the_catalog_lacks_or_a_foreign_link_does_not_read_as_a_stream(monkeypatch):
    player = VibixPlayer()
    catalog(player, monkeypatch, episode(1, Stream(720, "https://cdn.test/live.m3u8")))
    async with client({"live.m3u8": 200}) as http:
        assert (await read(player, http, link_of(PAGE, 1, 9))).kind is Kind.NOT_FOUND
        assert (await read(player, http, PAGE)).kind is Kind.NOT_PLAYER


@pytest.mark.asyncio
async def test_the_catalog_is_captured_once_for_all_the_episodes_of_a_series(monkeypatch):
    captures = []

    async def capture(browser, page_url):
        captures.append(page_url)
        return SimpleNamespace(body=b"api", manifest_body=None)

    async def browser():
        return object()

    monkeypatch.setattr(vibix, "capture_with_browser", capture)
    monkeypatch.setattr(vibix, "decode_playlist",
                        lambda body, key: [episode(n, Stream(720, "https://cdn.test/live.m3u8")) for n in (1, 2, 3)])
    player = VibixPlayer(browser_getter=browser)
    monkeypatch.setattr(player, "_key_store", SimpleNamespace(resolve=lambda **_: (KEY, None)))
    async with client({"live.m3u8": 200}) as http:
        for number in (1, 2, 3):
            assert (await read(player, http, link_of(PAGE, 1, number))).kind is Kind.PLAYABLE
    assert captures == [PAGE]
