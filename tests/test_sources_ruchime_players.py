from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from core.reasons import parse
from provider_contract import check_release
from modules.sources.video_providers.base import StreamUnavailable
from modules.sources.video_providers.ruchime import RuchimeProvider
from modules.sources.video_providers.vibix import (Episode, Stream, VibixUnavailable,
                                                    encode_envelope)

VK_URL = "https://vkvideo.ru/video_ext.php?oid=-1&amp;id=100&amp;hash=abc"
ALLVIDEO_URL = "https://fsst.online/embed/77/"
VK_OPEN = '<script>{"mp4_720":"a","mp4_1080":"b"}</script>'
VK_OPEN_4K = '<script>{"mp4_1080":"b","mp4_2160":"c"}</script>'
VK_CLOSED = '<script>{"error_msg":"Access denied"}</script>'
VK_OPEN_DENIED = VK_OPEN + "<div>Access denied</div>"
VK_AMBIGUOUS = "<html><body>no player data, no refusal</body></html>"
VK_EMBED_STREAMS = ('<script>{"mp4_1080":"https:\\/\\/cdn.test\\/1080.mp4",'
                    '"mp4_2160":"https:\\/\\/cdn.test\\/2160.mp4"}</script>')
AV_EMBED_1080 = '<script>file:"[360p]https://cdn.test/360.mp4/,[1080p]https://cdn.test/1080.mp4/"</script>'
AV_EMBED_4K = '<script>file:"[1080p]https://cdn.test/1080.mp4/,[4k]https://cdn.test/2160.mp4/"</script>'
AV_EMBED_EMPTY = "<html><body>no player</body></html>"
AV_EMBED_360 = '<script>file:"[360p]https://cdn.test/360.mp4/"</script>'
VIBIX_KEY = b"k" * 32
VIBIX_MANIFEST = (b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.000,\n"
                  b"https://cdn.test/seg-1.ts\n#EXT-X-ENDLIST\n")


def release_html(numbers: list[int], players: str = "") -> str:
    items = ", ".join(
        '{"id": %d, "number": %d, "compilation_from": null, '
        '"compilation_to": null, "title": "Серия %d", "date": "01 Oct 2026"}' % (n, n, n)
        for n in numbers)
    return ("<html><head><title>Тест</title></head><body>"
            f"<script>const ALL_EPS = [{items}];</script>{players}</body></html>")


def page_html(vk: bool = False, allvideo: bool = False, vibix: bool = False) -> str:
    parts: list[str] = []
    if vk:
        parts.append(f'<button data-player="vk" '
                     f'onclick="switchSource(\'{VK_URL}\',this)">VK</button>')
    if allvideo:
        parts.append(f'<button data-player="allvideo" '
                     f'onclick="switchSource(\'{ALLVIDEO_URL}\',this)">AllVideo</button>')
    if vibix:
        parts.append('<button data-player="vibix" '
                     'onclick="switchSource(\'vibix-embed\',this)">Vibix</button>')
    return "<html><body>" + "".join(parts) + "</body></html>"


async def scan(provider: RuchimeProvider, client) -> list[dict]:
    result = await provider.scan_episodes(
        "https://1.ruchime.org/release/test", client, {"name": "Тест"})
    return result["videos"]


def make_client(routes: dict[str, str], counts: dict[str, int] | None = None):
    async def side_effect(url, headers=None, follow_redirects=True, **kwargs):
        if counts is not None:
            for key in counts:
                if key in url:
                    counts[key] += 1
        response = AsyncMock(spec=httpx.Response)
        response.status_code = 200
        for key, body in routes.items():
            if key in url:
                response.text = body
                return response
        response.status_code = 404
        response.text = ""
        return response

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = side_effect
    return client


@pytest.mark.asyncio
async def test_closed_vk_falls_back_to_allvideo():
    provider = RuchimeProvider()
    client = make_client({
        "episode-1": page_html(vk=True, allvideo=True),
        "video_ext.php": VK_CLOSED,
        "fsst.online/embed/77": AV_EMBED_1080,
        "release/test": release_html([1]),
    })

    facts = await scan(provider, client)

    assert len(facts) == 1
    assert facts[0]["url"] == ALLVIDEO_URL
    assert facts[0]["resolution"] == 1080


@pytest.mark.asyncio
async def test_vk_priority_does_not_touch_allvideo_at_ceiling():
    provider = RuchimeProvider()
    counts = {"fsst.online": 0}
    client = make_client({
        "episode-1": page_html(vk=True, allvideo=True),
        "video_ext.php": VK_OPEN_4K,
        "fsst.online/embed/77": AV_EMBED_1080,
        "release/test": release_html([1]),
    }, counts)

    facts = await scan(provider, client)

    assert facts[0]["url"] == "https://vk.com/video-1_100"
    assert facts[0]["download_url"] == VK_URL.replace("&amp;", "&")
    assert facts[0]["resolution"] == 2160
    assert counts["fsst.online"] == 0


@pytest.mark.asyncio
async def test_higher_resolution_wins_over_priority_tie_break():
    provider = RuchimeProvider()
    client = make_client({
        "episode-1": page_html(vk=True, allvideo=True),
        "video_ext.php": VK_OPEN,
        "fsst.online/embed/77": AV_EMBED_4K,
        "release/test": release_html([1]),
    })

    facts = await scan(provider, client)

    assert facts[0]["url"] == ALLVIDEO_URL
    assert facts[0]["resolution"] == 2160


@pytest.mark.asyncio
async def test_unplayable_players_are_not_candidates():
    provider = RuchimeProvider()
    client = make_client({
        "episode-1": page_html(vk=True, allvideo=True),
        "video_ext.php": VK_CLOSED,
        "fsst.online/embed/77": AV_EMBED_EMPTY,
        "release/test": release_html([1]),
    })

    facts = await scan(provider, client)

    assert facts == []


@pytest.mark.asyncio
async def test_vibix_fallback_maps_episode_numbers(monkeypatch):
    provider = RuchimeProvider()
    calls: list[str] = []

    async def fake_playlist(page_url: str):
        calls.append(page_url)
        return ([Episode(1, "Серия 1", "Сезон 1",
                         (Stream(1080, "https://cdn/1.m3u8"),
                          Stream(720, "https://cdn/1_720.m3u8"))),
                 Episode(2, "Серия 2", "Сезон 1",
                         (Stream(480, "https://cdn/2.m3u8"),))], b"k" * 32)

    monkeypatch.setattr(provider, "_vibix_playlist", fake_playlist)
    client = manifest_client({"cdn/": 200}, {
        "episode-1": page_html(vibix=True),
        "episode-2": page_html(vibix=True),
        "release/test": release_html([1, 2]),
    })

    facts = await scan(provider, client)

    assert len(facts) == 2
    assert facts[0]["url"] == "https://1.ruchime.org/release/test/episode-1"
    assert facts[0]["resolution"] == 1080
    assert facts[1]["resolution"] == 480
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_vibix_skips_episode_missing_from_playlist(monkeypatch):
    provider = RuchimeProvider()

    async def fake_playlist(page_url: str):
        return ([Episode(1, "Серия 1", "Сезон 1", (Stream(720, "https://cdn/1.m3u8"),))],
                b"k" * 32)

    monkeypatch.setattr(provider, "_vibix_playlist", fake_playlist)
    client = manifest_client({"cdn/": 200}, {
        "episode-1": page_html(vibix=True),
        "episode-2": page_html(vibix=True),
        "release/test": release_html([1, 2]),
    })

    facts = await scan(provider, client)

    assert [fact["url"] for fact in facts] == [
        "https://1.ruchime.org/release/test/episode-1"]


@pytest.mark.asyncio
async def test_resolve_vibix_writes_absolute_manifest(monkeypatch, tmp_path: Path):
    provider = RuchimeProvider(manifest_dir=tmp_path)
    key = b"Z" * 32
    manifest = (b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.000,\n"
                b"./720.mp4:hls:seg-1-v1-a1.ts\n#EXT-X-ENDLIST\n")
    stream = Stream(1080, "https://cdn.test/hls/a/b/b_1080p.mp4/index.m3u8?expires=1&sign=x")

    async def fake_playlist(page_url: str):
        return ([Episode(387, "Серия 387", "Сезон 1", (stream,))], key)

    monkeypatch.setattr(provider, "_vibix_playlist", fake_playlist)
    response = AsyncMock(spec=httpx.Response)
    response.status_code = 200
    response.content = encode_envelope(manifest, key)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = response

    resolved = await provider.resolve_stream(
        "https://1.ruchime.org/release/test/episode-387", client)

    assert resolved is not None
    assert resolved["stream_url"].startswith("file://")
    written = Path(resolved["stream_url"][len("file://"):]).read_text(encoding="utf-8")
    assert "https://cdn.test/hls/a/b/b_1080p.mp4/720.mp4:hls:seg-1-v1-a1.ts" in written
    assert written.startswith("#EXTM3U")


@pytest.mark.asyncio
async def test_unconfirmed_vk_falls_back_to_allvideo():
    provider = RuchimeProvider()
    counts = {"fsst.online": 0}
    client = make_client({
        "episode-1": page_html(vk=True, allvideo=True),
        "video_ext.php": VK_AMBIGUOUS,
        "fsst.online/embed/77": AV_EMBED_1080,
        "release/test": release_html([1]),
    }, counts)

    result = await provider.scan_episodes(
        "https://1.ruchime.org/release/test", client, {"name": "Тест"})

    assert len(result["videos"]) == 1
    assert result["videos"][0]["url"] == ALLVIDEO_URL
    assert result["videos"][0]["resolution"] == 1080
    assert counts["fsst.online"] == 1
    assert result["players"]["players"]["vk"]["unverified"] == 1


@pytest.mark.asyncio
async def test_allvideo_referer_uses_final_host():
    provider = RuchimeProvider()
    response = httpx.Response(
        200, text='<script>file:"[1080p]https://cdn.test/1080.mp4/"</script>',
        request=httpx.Request("GET", "https://incvideo1.online/embed/77/"))
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = response

    resolved = await provider.resolve_stream("https://fsst.online/embed/77/", client)

    assert resolved is not None
    assert resolved["headers"]["Referer"] == "https://incvideo1.online/"


@pytest.mark.asyncio
async def test_resolve_vibix_returns_absolute_file_url(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    provider = RuchimeProvider(manifest_dir=Path("manifests"))
    key = b"Z" * 32
    manifest = (b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.000,\n"
                b"https://cdn.test/seg-1.ts\n#EXT-X-ENDLIST\n")
    stream = Stream(1080, "https://cdn.test/1.m3u8")

    async def fake_playlist(page_url: str):
        return ([Episode(1, "Серия 1", "Сезон 1", (stream,))], key)

    monkeypatch.setattr(provider, "_vibix_playlist", fake_playlist)
    response = AsyncMock(spec=httpx.Response)
    response.status_code = 200
    response.content = encode_envelope(manifest, key)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = response

    resolved = await provider.resolve_stream(
        "https://1.ruchime.org/release/test/episode-1", client)

    assert resolved is not None
    path = Path(resolved["stream_url"][len("file://"):])
    assert path.is_absolute()
    assert path.exists()


@pytest.mark.asyncio
async def test_parse_release_reports_preview_players():
    provider = RuchimeProvider()
    players = (f'<button data-player="vk" onclick="switchSource(\'{VK_URL}\',this)">VK</button>'
               f'<button data-player="allvideo" '
               f'onclick="switchSource(\'{ALLVIDEO_URL}\',this)">AllVideo</button>'
               '<button data-player="vibix" onclick="switchSource(\'vibix-embed\',this)">Vibix</button>')
    client = make_client({
        "video_ext.php": VK_CLOSED,
        "fsst.online/embed/77": AV_EMBED_1080,
        "release/test": release_html([1, 2], players),
    })

    result = await provider.parse_release(
        "https://1.ruchime.org/release/test/episode-1", client)

    check_release(provider, result)
    assert result["episodes_count"] == 2
    assert result["players"]["vk"]["present"] is True
    assert result["players"]["vk"]["available"] is False
    assert parse(result["players"]["vk"]["reason"])["code"] == "video_private"
    assert result["players"]["allvideo"]["available"] is True
    assert result["players"]["allvideo"]["resolution"] == 1080
    assert result["players"]["vibix"]["present"] is True
    assert result["players"]["vibix"]["available"] is None
    assert result["resolution"] is None
    # Per-episode quality is a scan fact; the parse does not invent it.
    assert all(video["resolution"] is None for video in result["videos"])
    assert [episode["number"] for episode in result["episodes"]] == [1, 2]


@pytest.mark.asyncio
async def test_scan_episodes_can_limit_page_fetches():
    provider = RuchimeProvider()
    counts = {"episode-1": 0, "episode-2": 0}
    client = make_client({
        "episode-1": page_html(vk=True),
        "episode-2": page_html(vk=True),
        "video_ext.php": VK_OPEN,
        "release/test": release_html([1, 2]),
    }, counts)

    result = await provider.scan_episodes(
        "https://1.ruchime.org/release/test", client, {"name": "Тест"},
        episodes=[2])

    assert [fact["title"] for fact in result["videos"]] == ["Тест - 2 серия"]
    assert counts["episode-1"] == 0
    assert counts["episode-2"] == 1


@pytest.mark.asyncio
async def test_scan_episodes_reports_progress():
    provider = RuchimeProvider()
    client = make_client({
        "episode-1": page_html(vk=True),
        "episode-2": page_html(vk=True),
        "video_ext.php": VK_OPEN,
        "release/test": release_html([1, 2]),
    })
    calls: list[tuple[int, int, str]] = []

    await provider.scan_episodes(
        "https://1.ruchime.org/release/test", client, {"name": "Тест"},
        progress=lambda current, total, phase: calls.append((current, total, phase)))

    assert calls[-1] == (2, 2, "done")
    assert (2, 2, "players") in calls
    pages = [call for call in calls if call[2] == "pages"]
    assert pages and pages[-1] == (2, 2, "pages")


@pytest.mark.asyncio
async def test_vk_refusal_wins_over_markers():
    provider = RuchimeProvider()
    client = make_client({
        "episode-1": page_html(vk=True),
        "video_ext.php": VK_OPEN_DENIED,
        "release/test": release_html([1]),
    })

    result = await provider.scan_episodes(
        "https://1.ruchime.org/release/test", client, {"name": "Тест"})

    assert result["videos"] == []
    assert result["players"]["players"]["vk"]["available"] == 0


@pytest.mark.asyncio
async def test_resolve_vk_returns_best_fresh_mp4():
    provider = RuchimeProvider()
    client = make_client({"video_ext.php": VK_EMBED_STREAMS})
    raw = "https://vkvideo.ru/video_ext.php?oid=-1&id=100&hash=abc"

    resolved = await provider.resolve_stream(raw, client)

    assert resolved["stream_url"] == "https://cdn.test/2160.mp4"
    assert resolved["headers"]["Referer"] == "https://vk.com/"


def manifest_client(routes: dict[str, int], page_routes: dict[str, str] | None = None,
                    hits: list[str] | None = None):
    """Client whose manifest URLs answer with the given status (200 = valid manifest)."""
    pages = make_client(page_routes or {})

    async def side_effect(url, headers=None, follow_redirects=True, **kwargs):
        for key, status in routes.items():
            if key in url:
                if hits is not None:
                    hits.append(url)
                response = AsyncMock(spec=httpx.Response)
                response.status_code = status
                response.content = (encode_envelope(VIBIX_MANIFEST, VIBIX_KEY)
                                    if status == 200 else b"<html>error</html>")
                return response
        return await pages.get(url, headers=headers, follow_redirects=follow_redirects,
                               **kwargs)

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = side_effect
    return client


def fake_vibix(provider: RuchimeProvider, monkeypatch, streams: tuple[Stream, ...]):
    async def fake_playlist(page_url: str):
        return [Episode(1, "Серия 1", "Сезон 1", streams)], VIBIX_KEY

    monkeypatch.setattr(provider, "_vibix_playlist", fake_playlist)


@pytest.mark.asyncio
async def test_vibix_dead_manifest_is_not_a_candidate(monkeypatch):
    provider = RuchimeProvider()
    fake_vibix(provider, monkeypatch, (Stream(480, "https://cdn.test/dead.m3u8"),))
    client = manifest_client({"dead.m3u8": 400}, {
        "episode-1": page_html(allvideo=True, vibix=True),
        "fsst.online/embed/77": AV_EMBED_360,
        "release/test": release_html([1]),
    })

    result = await provider.scan_episodes(
        "https://1.ruchime.org/release/test", client, {"name": "Тест"})

    assert result["videos"][0]["url"] == ALLVIDEO_URL
    assert result["videos"][0]["resolution"] == 360
    vibix = result["players"]["players"]["vibix"]
    assert vibix["available"] == 0 and vibix["selected"] == 0
    assert "400" in vibix["reason"]


@pytest.mark.asyncio
async def test_vibix_falls_back_to_lower_live_stream(monkeypatch):
    provider = RuchimeProvider()
    fake_vibix(provider, monkeypatch, (Stream(1080, "https://cdn.test/dead.m3u8"),
                                       Stream(720, "https://cdn.test/live.m3u8")))
    client = manifest_client({"dead.m3u8": 400, "live.m3u8": 200}, {
        "episode-1": page_html(vibix=True),
        "release/test": release_html([1]),
    })

    facts = await scan(provider, client)

    assert facts[0]["url"] == "https://1.ruchime.org/release/test/episode-1"
    assert facts[0]["resolution"] == 720


@pytest.mark.asyncio
async def test_vibix_is_not_probed_when_it_cannot_outrank(monkeypatch):
    provider = RuchimeProvider()
    fake_vibix(provider, monkeypatch, (Stream(720, "https://cdn.test/live.m3u8"),))
    hits: list[str] = []
    client = manifest_client({"live.m3u8": 200}, {
        "episode-1": page_html(allvideo=True, vibix=True),
        "fsst.online/embed/77": AV_EMBED_1080,
        "release/test": release_html([1]),
    }, hits)

    facts = await scan(provider, client)

    assert facts[0]["url"] == ALLVIDEO_URL
    assert hits == []


@pytest.mark.asyncio
async def test_resolve_vibix_dead_manifest_is_explicit_error(monkeypatch, tmp_path: Path):
    provider = RuchimeProvider(manifest_dir=tmp_path)
    fake_vibix(provider, monkeypatch, (Stream(480, "https://cdn.test/dead.m3u8"),))
    client = manifest_client({"dead.m3u8": 400})

    with pytest.raises(StreamUnavailable) as caught:
        await provider.resolve_stream("https://1.ruchime.org/release/test/episode-1", client)

    assert "400" in caught.value.reason
    assert caught.value.transient is False


@pytest.mark.asyncio
async def test_resolve_vibix_takes_best_live_stream(monkeypatch, tmp_path: Path):
    provider = RuchimeProvider(manifest_dir=tmp_path)
    fake_vibix(provider, monkeypatch, (Stream(1080, "https://cdn.test/dead.m3u8"),
                                       Stream(720, "https://cdn.test/live.m3u8")))
    client = manifest_client({"dead.m3u8": 503, "live.m3u8": 200})

    resolved = await provider.resolve_stream(
        "https://1.ruchime.org/release/test/episode-1", client)

    assert resolved["stream_url"].endswith("test-0001-720.m3u8")


@pytest.mark.asyncio
async def test_resolve_vibix_playlist_failure_is_explicit_and_transient(monkeypatch):
    provider = RuchimeProvider()

    async def broken_playlist(page_url: str):
        raise VibixUnavailable("Vibix API не ответил")

    monkeypatch.setattr(provider, "_vibix_playlist", broken_playlist)

    with pytest.raises(StreamUnavailable) as caught:
        await provider.resolve_stream(
            "https://1.ruchime.org/release/test/episode-1", AsyncMock(spec=httpx.AsyncClient))

    assert caught.value.transient is True
