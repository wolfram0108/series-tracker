"""The Vibix player: the catalog is captured once for a series, an episode reads from its best live manifest."""
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from core import storage
from core.reasons import parse
from modules.sources.players import vibix
from modules.sources.players.contract import Kind, PlayerRef
from modules.sources.players.vibix import VibixFailure, VibixPlayer, link_of
from sources_support import STEP_LIMIT_S, within
from modules.sources.players.vibix_protocol import (Episode, Stream, encode_envelope)

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
    return player.read(await within(player.fetch(PlayerRef("vibix", link), http, PAGE), STEP_LIMIT_S, "the ask of Vibix"))


async def resolve(player: VibixPlayer, url: str, http: httpx.AsyncClient) -> dict:
    return await within(player.resolve(url, http), STEP_LIMIT_S, "the resolution of a Vibix link")


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
    monkeypatch.setattr(player, "_key_store", SimpleNamespace(resolve=lambda **_: (KEY, None), path=Path("key.json")))
    async with client({"live.m3u8": 200}) as http:
        for number in (1, 2, 3):
            assert (await read(player, http, link_of(PAGE, 1, number))).kind is Kind.PLAYABLE
    assert captures == [PAGE]


# --- the disk is reached through the storage helper: a stalled disk ends the wait instead of holding the event loop --------

class Calls:
    """Spy over `storage.call`: what was sent to the storage and what it was told to own."""

    def __init__(self, monkeypatch, fail: Exception | None = None):
        self.sent: list[tuple[str, tuple]] = []
        self._real, self._fail = storage.call, fail
        monkeypatch.setattr(storage, "call", self)

    async def __call__(self, fn, *args, **kwargs):
        self.sent.append((getattr(fn, "__name__", "?"), (kwargs.get("owns"),)))
        if self._fail is not None:
            raise self._fail
        return await self._real(fn, *args, **kwargs)


@pytest.mark.asyncio
async def test_a_manifest_the_disk_does_not_take_is_a_transient_failure_with_a_reason(monkeypatch, tmp_path):
    Calls(monkeypatch, fail=storage.StorageBusy("занято"))
    player = VibixPlayer(manifest_dir=tmp_path)
    catalog(player, monkeypatch, episode(1, Stream(720, "https://cdn.test/live.m3u8")))
    async with client({"live.m3u8": 200}) as http:
        with pytest.raises(VibixFailure) as raised:
            await resolve(player, link_of(PAGE, 1, 1), http)

    assert raised.value.transient and parse(parse(raised.value.reason)["params"]["cause"])["code"] == "storage_failed"


@pytest.mark.asyncio
async def test_a_stream_within_the_cap_that_does_not_play_gives_way_to_a_lower_one_and_never_to_one_above(
        monkeypatch, tmp_path):
    Calls(monkeypatch)
    player = VibixPlayer(manifest_dir=tmp_path)
    catalog(player, monkeypatch, episode(1, Stream(1080, "https://cdn.test/hls/q1080.m3u8"),
                                         Stream(720, "https://cdn.test/hls/q720.m3u8"),
                                         Stream(480, "https://cdn.test/hls/q480.m3u8")))
    async with client({"q1080.m3u8": 200, "q720.m3u8": 404, "q480.m3u8": 200}) as http:
        found = await within(player.stream(link_of(PAGE, 1, 1), http, 720, None), STEP_LIMIT_S, "the stream of Vibix")

    assert found["stream_url"].endswith("-480.m3u8")
