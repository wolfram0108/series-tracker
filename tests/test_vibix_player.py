"""The Vibix player: the catalog is captured once for a series, an episode reads from its best live manifest."""
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from core import storage
from core.reasons import parse
from modules.sources.players import vibix
from modules.sources.players.contract import Kind, PlayerRef
from modules.sources.players.vibix import VibixFailure, VibixPlayer, link_of, parse_link
from sources_support import STEP_LIMIT_S, within
from modules.sources.players.vibix_protocol import (Episode, Stream, VibixUnavailable, encode_envelope,
                                                    parse_playlist)

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


def test_the_link_names_the_season_and_the_episode_and_nothing_else_parses():
    assert parse_link(link_of(PAGE, 2, 7)) == (2, 7)
    assert parse_link(PAGE) is None and parse_link(PAGE + "#cvh=1") is None


def test_an_entry_of_the_catalog_has_a_position_and_the_voice_the_catalog_names_and_no_number_of_its_own():
    folder = [{"title": "Серия 14", "file": "[480p]{RuChiMe}https://cdn.test/a.m3u8,[1080p]{RuChiMe}https://cdn.test/b.m3u8"},
              {"title": "Фильм", "file": "[720p]https://cdn.test/c.m3u8"}]
    first, second = parse_playlist({"data": {"playlist": [{"title": "Сезон 2", "folder": folder}]}})

    assert (first.position, first.season_index, first.title, first.season) == (1, 1, "Серия 14", "Сезон 2")
    assert [(stream.quality, stream.voice) for stream in first.streams] == [(1080, "RuChiMe"), (480, "RuChiMe")]
    assert first.voice == "RuChiMe" and second.voice == ""
    assert (second.position, second.title) == (2, "Фильм")
    assert not hasattr(first, "number")


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
    monkeypatch.setattr(player, "_key_store", SimpleNamespace(resolve=lambda **_: (KEY, None), path=Path("key.json")))
    async with client({"live.m3u8": 200}) as http:
        for number in (1, 2, 3):
            assert (await read(player, http, link_of(PAGE, 1, number))).kind is Kind.PLAYABLE
    assert captures == [PAGE]


def test_a_link_is_a_vibix_link_by_the_entry_it_names():
    assert VibixPlayer.owns(link_of(PAGE, 1, 3)) and not VibixPlayer.owns(PAGE)


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
async def test_the_key_file_is_read_and_written_through_the_storage_helper(monkeypatch, tmp_path):
    calls = Calls(monkeypatch)
    api = encode_envelope(json.dumps({"data": {"playlist": [{"title": "Сезон 1", "folder": [
        {"title": "Серия 1", "file": "[720p]{RuChiMe}https://cdn.test/1.m3u8?expires=9999999999"}]}]}}).encode(), KEY)

    async def capture(browser, page_url):
        return SimpleNamespace(body=api, manifest_body=None)

    async def browser():
        return object()

    monkeypatch.setattr(vibix, "capture_with_browser", capture)
    key_path = tmp_path / "keys" / "vibix-key.json"
    player = VibixPlayer(browser_getter=browser, key_path=key_path)
    player._key_store.save(KEY, 2_000_000_000)  # a key younger than its life: the capture takes it from the file

    playlist, key = await within(player.playlist(PAGE), STEP_LIMIT_S, "the capture of the catalog")

    assert key == KEY and playlist[0].voice == "RuChiMe"
    assert calls.sent == [("resolve", (str(key_path),))]


@pytest.mark.asyncio
async def test_a_stalled_key_file_makes_the_capture_unavailable_with_a_reason(monkeypatch, tmp_path):
    Calls(monkeypatch, fail=storage.StorageTimeout("хранилище не ответило"))

    async def capture(browser, page_url):
        return SimpleNamespace(body=b"api", manifest_body=None)

    async def browser():
        return object()

    monkeypatch.setattr(vibix, "capture_with_browser", capture)
    player = VibixPlayer(browser_getter=browser, key_path=tmp_path / "key.json")

    with pytest.raises(VibixUnavailable) as raised:
        await within(player.playlist(PAGE), STEP_LIMIT_S, "the capture of the catalog")

    assert parse(str(raised.value))["code"] == "storage_failed"


@pytest.mark.asyncio
async def test_a_manifest_is_written_through_the_storage_helper_to_the_path_it_owns(monkeypatch, tmp_path):
    calls = Calls(monkeypatch)
    player = VibixPlayer(manifest_dir=tmp_path / "manifests")
    catalog(player, monkeypatch, episode(2, Stream(720, "https://cdn.test/hls/live.m3u8")))
    async with client({"live.m3u8": 200}) as http:
        resolved = await resolve(player, link_of(PAGE, 1, 2), http)

    path = Path(resolved["stream_url"].removeprefix("file://"))
    assert path == (tmp_path / "manifests" / "vibix" / "test-s1-0002-720.m3u8").absolute()
    assert path.read_text(encoding="utf-8").startswith("#EXTM3U") and "https://cdn.test/hls/seg.ts" in path.read_text()
    assert calls.sent == [("_write_manifest", (str(path),))]


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
async def test_the_entry_the_link_names_is_the_entry_that_resolves_and_a_missing_one_is_never_swapped(monkeypatch, tmp_path):
    player = VibixPlayer(manifest_dir=tmp_path)
    catalog(player, monkeypatch,
            episode(1, Stream(720, "https://cdn.test/s1.m3u8"), season=1),
            episode(1, Stream(480, "https://cdn.test/s2.m3u8"), season=2))
    async with client({"s1.m3u8": 200, "s2.m3u8": 200}) as http:
        second = await resolve(player, link_of(PAGE, 2, 1), http)
        with pytest.raises(VibixFailure) as missing:
            await resolve(player, link_of(PAGE, 3, 1), http)
        with pytest.raises(VibixFailure) as foreign:
            await resolve(player, PAGE, http)

    assert second["stream_url"].endswith("-s2-0001-480.m3u8")
    assert parse(parse(missing.value.reason)["params"]["cause"])["code"] == "vibix_no_episode"
    assert parse(parse(foreign.value.reason)["params"]["cause"])["code"] == "link_no_episode"


@pytest.mark.asyncio
@pytest.mark.parametrize("cap, qualities, expected", [
    (720, (1080, 720), 720), (1080, (1080, 720), 1080), (None, (720, 1080), 1080), (0, (720, 1080), 1080),
    (300, (720, 1080, 360), 360), (720, (480, 1080, 720, 360), 720)])
async def test_the_stream_is_the_best_not_above_the_cap_and_the_lowest_above_it_when_nothing_fits(
        cap, qualities, expected, monkeypatch, tmp_path):
    Calls(monkeypatch)
    player = VibixPlayer(manifest_dir=tmp_path)
    catalog(player, monkeypatch, episode(1, *[Stream(quality, f"https://cdn.test/hls/q{quality}.m3u8")
                                              for quality in qualities]))
    async with client({f"q{quality}.m3u8": 200 for quality in qualities}) as http:
        found = await within(player.stream(link_of(PAGE, 1, 1), http, cap, None), STEP_LIMIT_S, "the stream of Vibix")

    assert found["stream_url"].endswith(f"-{expected}.m3u8")


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
