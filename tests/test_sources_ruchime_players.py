from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from modules.sources.video_providers.ruchime import RuchimeProvider
from modules.sources.video_providers.vibix import Episode, Stream, encode_envelope

VK_URL = "https://vkvideo.ru/video_ext.php?oid=-1&amp;id=100&amp;hash=abc"
ALLVIDEO_URL = "https://fsst.online/embed/77/"
VK_OPEN = '<script>{"mp4_720":"a","mp4_1080":"b"}</script>'
VK_OPEN_4K = '<script>{"mp4_1080":"b","mp4_2160":"c"}</script>'
VK_CLOSED = '<script>{"error_msg":"Access denied"}</script>'
VK_AMBIGUOUS = "<html><body>no player data, no refusal</body></html>"
AV_EMBED_1080 = '<script>file:"[360p]https://cdn.test/360.mp4/,[1080p]https://cdn.test/1080.mp4/"</script>'
AV_EMBED_4K = '<script>file:"[1080p]https://cdn.test/1080.mp4/,[4k]https://cdn.test/2160.mp4/"</script>'
AV_EMBED_EMPTY = "<html><body>no player</body></html>"


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
    client = make_client({
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
    client = make_client({
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
async def test_unconfirmed_vk_does_not_switch_source():
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

    assert result["videos"] == []
    assert counts["fsst.online"] == 0
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

    assert result["episodes_count"] == 2
    assert result["players"]["vk"]["present"] is True
    assert result["players"]["vk"]["available"] is False
    assert "приватности" in result["players"]["vk"]["reason"]
    assert result["players"]["allvideo"]["available"] is True
    assert result["players"]["allvideo"]["resolution"] == 1080
    assert result["players"]["vibix"]["present"] is True
    assert result["players"]["vibix"]["available"] is None
    assert result["resolution"] == 1080
    assert all(video["resolution"] == 1080 for video in result["videos"])


def test_merge_reports_prefers_best_resolution():
    from modules.sources.video_providers.ruchime import _merge_reports

    first = {"vk": {"present": True, "available": True,
                    "resolution": 1080, "reason": ""}}
    second = {"vk": {"present": True, "available": True,
                     "resolution": 2160, "reason": ""},
              "vibix": {"present": True, "available": None,
                        "resolution": None, "reason": "проверяется при сканировании"}}
    merged = _merge_reports(first, second)
    assert merged["vk"]["resolution"] == 2160
    assert merged["vibix"]["present"] is True
    assert merged["vibix"]["available"] is None
