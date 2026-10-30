from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from core.reasons import parse
from http_fakes import SHOW_DENIED, fake_client, fake_response
from provider_contract import check_release
from modules.sources import policy
from modules.sources.players.vk import VkPlayer
from modules.sources.video_providers.base import StreamUnavailable
from modules.sources.video_providers.ruchime import RuchimeProvider
from modules.sources.players.vibix_protocol import (Episode, Stream, VibixUnavailable,
                                                    encode_envelope)

VK_URL = "https://vkvideo.ru/video_ext.php?oid=-1&amp;id=100&amp;hash=abc"
ALLVIDEO_URL = "https://fsst.online/embed/77/"
VK_OPEN = '<script>{"mp4_720":"a","mp4_1080":"b"}</script>'
VK_OPEN_4K = '<script>{"mp4_1080":"b","mp4_2160":"c"}</script>'
VK_CLOSED = '<html><head><title>Video embed</title></head><body><div id="video_ext_msg">Access denied</div></body></html>'
VK_EMBED = "https://vkvideo.ru/video_ext.php?oid=-1&id=100&hash=abc"
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


def make_client(routes: dict[str, str], counts: dict[str, int] | None = None, shows: dict[str, str] | None = None):
    async def side_effect(url, headers=None, follow_redirects=True, **kwargs):
        if counts is not None:
            for key in counts:
                if key in url:
                    counts[key] += 1
        response = fake_response()
        response.status_code = 200
        for key, body in routes.items():
            if key in url:
                response.text = body
                return response
        response.status_code = 404
        response.text = ""
        return response

    client = fake_client(shows)
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
    assert facts[0]["download_url"] == VK_EMBED
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


def manifest_client(routes: dict[str, int], page_routes: dict[str, str] | None = None,
                    hits: list[str] | None = None):
    """Client whose manifest URLs answer with the given status (200 = valid manifest)."""
    pages = make_client(page_routes or {})

    async def side_effect(url, headers=None, follow_redirects=True, **kwargs):
        for key, status in routes.items():
            if key in url:
                if hits is not None:
                    hits.append(url)
                response = fake_response()
                response.status_code = status
                response.content = (encode_envelope(VIBIX_MANIFEST, VIBIX_KEY)
                                    if status == 200 else b"<html>error</html>")
                return response
        return await pages.get(url, headers=headers, follow_redirects=follow_redirects,
                               **kwargs)

    client = fake_client()
    client.get.side_effect = side_effect
    return client


def fake_vibix(provider: RuchimeProvider, monkeypatch, streams: tuple[Stream, ...]):
    async def fake_playlist(page_url: str):
        return [Episode(1, "Серия 1", "Сезон 1", streams)], VIBIX_KEY

    monkeypatch.setattr(provider.vibix, "playlist", fake_playlist)


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

    monkeypatch.setattr(provider.vibix, "playlist", fake_playlist)
    client = manifest_client({"cdn/": 200}, {
        "episode-1": page_html(vibix=True),
        "episode-2": page_html(vibix=True),
        "release/test": release_html([1, 2]),
    })

    facts = await scan(provider, client)

    assert len(facts) == 2
    assert facts[0]["url"] == "https://1.ruchime.org/release/test/episode-1#vibix=1/1"
    assert facts[0]["resolution"] == 1080
    assert facts[1]["resolution"] == 480
    assert set(calls) == {"https://1.ruchime.org/release/test/episode-1", "https://1.ruchime.org/release/test/episode-2"}


@pytest.mark.asyncio
async def test_vibix_skips_episode_missing_from_playlist(monkeypatch):
    provider = RuchimeProvider()

    async def fake_playlist(page_url: str):
        return ([Episode(1, "Серия 1", "Сезон 1", (Stream(720, "https://cdn/1.m3u8"),))],
                b"k" * 32)

    monkeypatch.setattr(provider.vibix, "playlist", fake_playlist)
    client = manifest_client({"cdn/": 200}, {
        "episode-1": page_html(vibix=True),
        "episode-2": page_html(vibix=True),
        "release/test": release_html([1, 2]),
    })

    facts = await scan(provider, client)

    assert [fact["url"] for fact in facts] == [
        "https://1.ruchime.org/release/test/episode-1#vibix=1/1"]


@pytest.mark.asyncio
async def test_resolve_vibix_writes_absolute_manifest(monkeypatch, tmp_path: Path):
    provider = RuchimeProvider(manifest_dir=tmp_path)
    key = b"Z" * 32
    manifest = (b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.000,\n"
                b"./720.mp4:hls:seg-1-v1-a1.ts\n#EXT-X-ENDLIST\n")
    stream = Stream(1080, "https://cdn.test/hls/a/b/b_1080p.mp4/index.m3u8?expires=1&sign=x")

    async def fake_playlist(page_url: str):
        return ([Episode(387, "Серия 387", "Сезон 1", (stream,))], key)

    monkeypatch.setattr(provider.vibix, "playlist", fake_playlist)
    response = fake_response()
    response.status_code = 200
    response.content = encode_envelope(manifest, key)
    client = fake_client()
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
    client = fake_client()
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

    monkeypatch.setattr(provider.vibix, "playlist", fake_playlist)
    response = fake_response()
    response.status_code = 200
    response.content = encode_envelope(manifest, key)
    client = fake_client()
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

    assert facts[0]["url"] == "https://1.ruchime.org/release/test/episode-1#vibix=1/1"
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

    monkeypatch.setattr(provider.vibix, "playlist", broken_playlist)

    with pytest.raises(StreamUnavailable) as caught:
        await provider.resolve_stream(
            "https://1.ruchime.org/release/test/episode-1", fake_client())

    assert caught.value.transient is True


def two_seasons(provider: RuchimeProvider, monkeypatch) -> None:
    """One Vibix record that holds two seasons whose episode numbers repeat."""
    async def fake_playlist(page_url: str):
        return ([Episode(1, "Серия 1", "Сезон 1", (Stream(480, "https://cdn.test/s1-1.m3u8"),), 1),
                 Episode(2, "Серия 2", "Сезон 1", (Stream(480, "https://cdn.test/s1-2.m3u8"),), 1),
                 Episode(1, "Серия 1", "Сезон 2", (Stream(1080, "https://cdn.test/s2-1.m3u8"),), 2),
                 Episode(2, "Серия 2", "Сезон 2", (Stream(720, "https://cdn.test/s2-2.m3u8"),), 2)],
                VIBIX_KEY)

    monkeypatch.setattr(provider.vibix, "playlist", fake_playlist)


def vibix_pages() -> dict[str, str]:
    return {"episode-1": page_html(vibix=True), "episode-2": page_html(vibix=True),
            "release/test": release_html([1, 2])}


@pytest.mark.asyncio
async def test_vibix_takes_the_chosen_season_and_names_its_entry(monkeypatch):
    provider = RuchimeProvider()
    two_seasons(provider, monkeypatch)
    client = manifest_client({"cdn.test/": 200}, vibix_pages())

    result = await provider.scan_episodes(
        "https://1.ruchime.org/release/test?vibix=2", client, {"name": "Тест"})

    assert [(fact["url"], fact["resolution"]) for fact in result["videos"]] == [
        ("https://1.ruchime.org/release/test/episode-1#vibix=2/1", 1080),
        ("https://1.ruchime.org/release/test/episode-2#vibix=2/2", 720)]


@pytest.mark.asyncio
async def test_vibix_with_several_seasons_and_no_choice_is_refused(monkeypatch):
    provider = RuchimeProvider()
    two_seasons(provider, monkeypatch)
    client = manifest_client({"cdn.test/": 200}, vibix_pages())

    result = await provider.scan_episodes(
        "https://1.ruchime.org/release/test", client, {"name": "Тест"})

    assert result["videos"] == []
    vibix = result["players"]["players"]["vibix"]
    assert vibix["available"] == 0
    assert parse(vibix["reason"])["code"] == "vibix_season_required"
    assert parse(result["players"]["holes"][0]["reason"])["code"] == "vibix_season_required"


@pytest.mark.asyncio
async def test_a_chosen_season_the_catalog_lacks_is_refused(monkeypatch):
    provider = RuchimeProvider()
    two_seasons(provider, monkeypatch)
    client = manifest_client({"cdn.test/": 200}, vibix_pages())

    result = await provider.scan_episodes(
        "https://1.ruchime.org/release/test?vibix=3", client, {"name": "Тест"})

    assert result["videos"] == []
    refusal = parse(result["players"]["players"]["vibix"]["reason"])
    assert refusal["code"] == "vibix_season_missing" and refusal["params"]["season"] == 3


@pytest.mark.asyncio
async def test_a_disabled_player_is_not_probed():
    provider = RuchimeProvider()
    counts = {"video_ext.php": 0}
    client = make_client({
        "episode-1": page_html(vk=True, allvideo=True),
        "video_ext.php": VK_OPEN,
        "fsst.online/embed/77": AV_EMBED_1080,
        "release/test": release_html([1]),
    }, counts)

    result = await provider.scan_episodes(
        "https://1.ruchime.org/release/test?off=vk", client, {"name": "Тест"})

    assert result["videos"][0]["url"] == ALLVIDEO_URL
    assert counts["video_ext.php"] == 0
    vk = result["players"]["players"]["vk"]
    assert vk["available"] == 0 and parse(vk["reason"])["code"] == "source_disabled"


@pytest.mark.asyncio
async def test_with_every_player_disabled_no_episode_page_is_read():
    provider = RuchimeProvider()
    counts = {"episode-1": 0}
    client = make_client({"episode-1": page_html(vk=True), "release/test": release_html([1])}, counts)

    result = await provider.scan_episodes(
        "https://1.ruchime.org/release/test?off=vk,allvideo,vibix", client, {"name": "Тест"})

    assert result["videos"] == []
    assert counts["episode-1"] == 0
    assert {parse(entry["reason"])["code"] for entry in result["players"]["players"].values()} == {"source_disabled"}


@pytest.mark.asyncio
async def test_vibix_alone_serves_when_vk_and_allvideo_are_disabled(monkeypatch):
    provider = RuchimeProvider()
    two_seasons(provider, monkeypatch)
    client = manifest_client({"cdn.test/": 200}, {
        **vibix_pages(), "episode-1": page_html(vk=True, allvideo=True, vibix=True)})

    result = await provider.scan_episodes(
        "https://1.ruchime.org/release/test?vibix=2&off=vk,allvideo", client, {"name": "Тест"})

    assert [fact["url"] for fact in result["videos"]] == [
        "https://1.ruchime.org/release/test/episode-1#vibix=2/1",
        "https://1.ruchime.org/release/test/episode-2#vibix=2/2"]


@pytest.mark.asyncio
async def test_resolve_vibix_takes_the_entry_the_link_names(monkeypatch, tmp_path: Path):
    provider = RuchimeProvider(manifest_dir=tmp_path)
    two_seasons(provider, monkeypatch)
    hits: list[str] = []
    client = manifest_client({"cdn.test/": 200}, hits=hits)

    resolved = await provider.resolve_stream(
        "https://1.ruchime.org/release/test/episode-1#vibix=2/1", client)

    assert hits == ["https://cdn.test/s2-1.m3u8"]
    assert resolved["stream_url"].endswith("test-0001-1080.m3u8")


@pytest.mark.asyncio
async def test_resolve_vibix_link_without_a_season_is_refused_when_the_catalog_has_several(monkeypatch):
    provider = RuchimeProvider()
    two_seasons(provider, monkeypatch)

    with pytest.raises(StreamUnavailable) as caught:
        await provider.resolve_stream(
            "https://1.ruchime.org/release/test/episode-1", manifest_client({"cdn.test/": 200}))

    assert "vibix_season_required" in caught.value.reason
    assert caught.value.transient is False


@pytest.mark.asyncio
async def test_resolve_vibix_never_swaps_the_season_of_a_missing_entry(monkeypatch):
    provider = RuchimeProvider()
    two_seasons(provider, monkeypatch)
    hits: list[str] = []

    with pytest.raises(StreamUnavailable) as caught:
        await provider.resolve_stream(
            "https://1.ruchime.org/release/test/episode-9#vibix=2/9",
            manifest_client({"cdn.test/": 200}, hits=hits))

    assert "vibix_no_episode" in caught.value.reason
    assert hits == []


@pytest.mark.asyncio
async def test_layout_lists_the_seasons_of_the_catalog(monkeypatch):
    provider = RuchimeProvider()
    two_seasons(provider, monkeypatch)

    layout = await provider.layout("https://1.ruchime.org/release/test", make_client(vibix_pages()))

    assert [(s["player"], s["studio"], s["count"], s["ranges"]) for s in layout["sources"]] == [
        ("vibix", "Сезон 1", 2, "1-2"), ("vibix", "Сезон 2", 2, "1-2")]
    assert [s["url"] for s in layout["sources"]] == [
        "https://1.ruchime.org/release/test?vibix=1", "https://1.ruchime.org/release/test?vibix=2"]
    assert layout["sources"][1]["resolutions"] == [
        {"resolution": 1080, "ranges": "1", "count": 1}, {"resolution": 720, "ranges": "2", "count": 1}]
    assert layout["selected_source"] is None and layout["reason"] == ""


@pytest.mark.asyncio
async def test_layout_selects_the_season_the_address_chose(monkeypatch):
    provider = RuchimeProvider()
    two_seasons(provider, monkeypatch)

    layout = await provider.layout(
        "https://1.ruchime.org/release/test?vibix=2&off=vk", make_client(vibix_pages()))

    assert layout["selected_source"] == "https://1.ruchime.org/release/test?vibix=2&off=vk"
    assert layout["sources"][0]["url"] == "https://1.ruchime.org/release/test?vibix=1&off=vk"


@pytest.mark.asyncio
async def test_layout_of_a_single_season_needs_no_choice(monkeypatch):
    provider = RuchimeProvider()
    fake_vibix(provider, monkeypatch, (Stream(720, "https://cdn.test/live.m3u8"),))

    layout = await provider.layout("https://1.ruchime.org/release/test", make_client(vibix_pages()))

    assert [s["studio"] for s in layout["sources"]] == ["Сезон 1"]
    assert layout["selected_source"] == "https://1.ruchime.org/release/test?vibix=1"


@pytest.mark.asyncio
async def test_layout_does_not_read_the_catalog_of_a_disabled_player(monkeypatch):
    provider = RuchimeProvider()

    async def never(page_url: str):
        raise AssertionError("the catalog of a disabled player must not be read")

    monkeypatch.setattr(provider.vibix, "playlist", never)

    layout = await provider.layout("https://1.ruchime.org/release/test?off=vibix", make_client(vibix_pages()))

    assert layout == {"sources": [], "selected_source": None, "reason": ""}


@pytest.mark.asyncio
async def test_layout_names_a_page_without_the_player(monkeypatch):
    provider = RuchimeProvider()
    two_seasons(provider, monkeypatch)
    client = make_client({"episode-1": page_html(vk=True), "release/test": release_html([1])})

    layout = await provider.layout("https://1.ruchime.org/release/test", client)

    assert layout["sources"] == []
    assert parse(layout["reason"])["code"] == "page_no_player"


@pytest.mark.asyncio
async def test_layout_names_an_unreadable_catalog(monkeypatch):
    provider = RuchimeProvider()

    async def broken(page_url: str):
        raise VibixUnavailable("Vibix API не ответил")

    monkeypatch.setattr(provider.vibix, "playlist", broken)

    layout = await provider.layout("https://1.ruchime.org/release/test", make_client(vibix_pages()))

    assert layout["sources"] == [] and layout["selected_source"] is None
    assert parse(layout["reason"])["code"] == "player_failed"


@pytest.mark.asyncio
async def test_parse_release_carries_the_choice_in_the_address():
    provider = RuchimeProvider()
    client = make_client({"release/test": release_html([1, 2])})

    result = await provider.parse_release(
        "https://1.ruchime.org/release/test/episode-1?vibix=2&off=allvideo", client)

    check_release(provider, result)
    assert result["canonical_url"] == "https://1.ruchime.org/release/test?vibix=2&off=allvideo"
    players = result["players"]
    assert players["vk"]["enabled"] is True and players["allvideo"]["enabled"] is False
    assert players["allvideo"]["toggle"] == "https://1.ruchime.org/release/test?vibix=2"
    assert players["vk"]["toggle"] == "https://1.ruchime.org/release/test?vibix=2&off=vk,allvideo"


def player_page(vk: int | None = None, allvideo: int | None = None) -> str:
    parts = []
    if vk is not None:
        parts.append('<button data-player="vk" onclick="switchSource(\'https://vkvideo.ru/video_ext.php'
                     f'?oid=-1&amp;id={vk}&amp;hash=abc\',this)">VK</button>')
    if allvideo is not None:
        parts.append(f'<button data-player="allvideo" onclick="switchSource(\'https://fsst.online/embed/{allvideo}/\','
                     'this)">AllVideo</button>')
    return "<html><body>" + "".join(parts) + "</body></html>"


@pytest.mark.asyncio
async def test_coverage_probes_every_player_of_every_episode():
    provider = RuchimeProvider()
    client = make_client({
        "episode-1": player_page(vk=101, allvideo=77), "episode-2": player_page(vk=102, allvideo=77),
        "episode-3": player_page(allvideo=77),
        "id=101&hash": VK_OPEN_4K, "id=102&hash": VK_OPEN, "fsst.online/embed/77": AV_EMBED_1080,
        "release/test": release_html([1, 2, 3]),
    })

    result = await provider.coverage("https://1.ruchime.org/release/test", client)

    vk = result["players"]["vk"]
    assert (vk["present"], vk["count"], vk["ranges"]) == (2, 2, "1-2")
    assert vk["resolutions"] == [{"resolution": 2160, "ranges": "1", "count": 1},
                                 {"resolution": 1080, "ranges": "2", "count": 1}]
    # A scan would skip AllVideo where VK is at the ceiling; the coverage must not.
    allvideo = result["players"]["allvideo"]
    assert (allvideo["count"], allvideo["ranges"]) == (3, "1-3")
    assert allvideo["resolutions"] == [{"resolution": 1080, "ranges": "1-3", "count": 3}]
    assert "vibix" not in result["players"] and result["reason"] == ""


@pytest.mark.asyncio
async def test_coverage_counts_only_the_episodes_a_player_really_plays():
    provider = RuchimeProvider()
    client = make_client({
        "episode-1": player_page(vk=101), "episode-2": player_page(vk=102),
        "id=101&hash": VK_OPEN, "id=102&hash": VK_CLOSED,
        "release/test": release_html([1, 2]),
    })

    vk = (await provider.coverage("https://1.ruchime.org/release/test", client))["players"]["vk"]

    assert (vk["present"], vk["available"], vk["count"], vk["ranges"]) == (2, 1, 1, "1")


@pytest.mark.asyncio
async def test_coverage_logs_why_a_player_does_not_play(caplog):
    provider = RuchimeProvider()
    client = make_client({
        "episode-1": player_page(vk=101), "episode-2": player_page(vk=102),
        "id=101&hash": VK_OPEN, "id=102&hash": VK_CLOSED, "release/test": release_html([1, 2]),
    })

    with caplog.at_level("INFO", logger="sources.video.ruchime"):
        await provider.coverage("https://1.ruchime.org/release/test", client)

    assert "vk shows on 2 episodes and plays on 1 (video_private)" in caplog.text


@pytest.mark.asyncio
async def test_coverage_does_not_depend_on_the_choice_of_the_address():
    provider = RuchimeProvider()
    routes = {"episode-1": player_page(vk=101, allvideo=77), "id=101&hash": VK_OPEN,
              "fsst.online/embed/77": AV_EMBED_1080, "release/test": release_html([1])}

    plain = await provider.coverage("https://1.ruchime.org/release/test", make_client(routes))
    chosen = await provider.coverage("https://1.ruchime.org/release/test?vibix=2&off=vk,allvideo", make_client(routes))

    assert chosen == plain
    assert set(plain["players"]) == {"vk", "allvideo"} and plain["players"]["vk"]["count"] == 1


@pytest.mark.asyncio
async def test_coverage_names_an_unreadable_episode_page_and_gives_no_partial_answer():
    provider = RuchimeProvider()
    client = make_client({"episode-1": player_page(vk=101), "id=101&hash": VK_OPEN,
                          "release/test": release_html([1, 2])})
    answer = client.get.side_effect

    async def get(url, headers=None, follow_redirects=True, **kwargs):
        if "episode-2" in url:
            gone = fake_response()
            gone.status_code, gone.text = 404, ""
            return gone
        return await answer(url, headers=headers, follow_redirects=follow_redirects, **kwargs)

    client.get.side_effect = get

    result = await provider.coverage("https://1.ruchime.org/release/test", client)

    assert result["players"] == {}
    assert parse(result["reason"])["code"] == "pages_unread"


@pytest.mark.asyncio
async def test_coverage_reports_progress_by_pages_and_then_by_players():
    provider = RuchimeProvider()
    client = make_client({"episode-1": player_page(vk=101), "episode-2": player_page(vk=102),
                          "id=10": VK_OPEN, "release/test": release_html([1, 2])})
    calls: list[tuple[int, int, str]] = []

    await provider.coverage("https://1.ruchime.org/release/test", client,
                            progress=lambda current, total, phase: calls.append((current, total, phase)))

    assert (2, 2, "pages") in calls and calls[-1] == (2, 2, "players")


PAGE = "https://1.ruchime.org/release/test"
SURVEY_ROUTES = {
    "episode-1": player_page(vk=101, allvideo=77), "episode-2": player_page(vk=102, allvideo=77),
    "id=101&hash": VK_OPEN, "id=102&hash": VK_OPEN_4K, "fsst.online/embed/77": AV_EMBED_1080,
    "release/test": release_html([1, 2]),
}
SURVEY_COUNTS = {"episode-1": 0, "episode-2": 0, "id=101&hash": 0, "id=102&hash": 0, "fsst.online": 0}


@pytest.mark.asyncio
async def test_a_scan_right_after_the_coverage_reads_nothing_again():
    provider, counts = RuchimeProvider(), dict(SURVEY_COUNTS)
    client = make_client(SURVEY_ROUTES, counts)
    await provider.coverage(PAGE, client)
    read = dict(counts)

    scanned = await provider.scan_episodes(PAGE, client, {"name": "Тест"})

    assert counts == read
    fresh = await RuchimeProvider().scan_episodes(PAGE, make_client(SURVEY_ROUTES), {"name": "Тест"})
    assert scanned["videos"] == fresh["videos"]


@pytest.mark.asyncio
async def test_a_scan_after_the_coverage_still_leaves_out_the_disabled_players():
    provider, counts = RuchimeProvider(), dict(SURVEY_COUNTS)
    client = make_client(SURVEY_ROUTES, counts)
    await provider.coverage(PAGE, client)
    read = dict(counts)

    scanned = await provider.scan_episodes(f"{PAGE}?off=vk", client, {"name": "Тест"})

    assert counts == read
    assert [fact["url"] for fact in scanned["videos"]] == [ALLVIDEO_URL, ALLVIDEO_URL]
    assert parse(scanned["players"]["players"]["vk"]["reason"])["code"] == "source_disabled"


@pytest.mark.asyncio
async def test_a_scan_reads_again_an_episode_the_coverage_could_not_be_sure_of():
    provider, counts = RuchimeProvider(), dict(SURVEY_COUNTS)
    base = make_client(SURVEY_ROUTES, counts)
    answer, state = base.get.side_effect, {"vk_down": True}

    async def get(url, headers=None, follow_redirects=True, **kwargs):
        if state["vk_down"] and "id=101&hash" in url:
            down = fake_response()
            down.status_code, down.text = 403, ""
            return down
        return await answer(url, headers=headers, follow_redirects=follow_redirects, **kwargs)

    base.get.side_effect = get
    await provider.coverage(PAGE, base)
    read = dict(counts)
    state["vk_down"] = False

    scanned = await provider.scan_episodes(PAGE, base, {"name": "Тест"})

    assert counts["id=101&hash"] > read["id=101&hash"]
    assert (counts["episode-1"], counts["episode-2"], counts["id=102&hash"]) == (
        read["episode-1"], read["episode-2"], read["id=102&hash"])
    assert scanned["videos"][0]["url"] == "https://vk.com/video-1_101"


@pytest.mark.asyncio
async def test_a_scan_does_not_trust_what_the_coverage_read_long_ago():
    provider, counts = RuchimeProvider(), dict(SURVEY_COUNTS)
    client = make_client(SURVEY_ROUTES, counts)
    await provider.coverage(PAGE, client)
    read = dict(counts)
    provider._tables["test"].expires_at = 0.0

    await provider.scan_episodes(PAGE, client, {"name": "Тест"})

    assert counts["episode-1"] == read["episode-1"] + 1


def refuse(client, marker: str, status: int, text: str, times: int) -> dict[str, int]:
    """Make the first `times` requests whose address has `marker` come back refused; count the refusals."""
    answer, served = client.get.side_effect, {"refused": 0}

    async def get(url, headers=None, follow_redirects=True, **kwargs):
        if marker in url and served["refused"] < times:
            served["refused"] += 1
            refused = fake_response()
            refused.status_code, refused.text = status, text
            return refused
        return await answer(url, headers=headers, follow_redirects=follow_redirects, **kwargs)

    client.get.side_effect = get
    return served


REFUSALS = [(403, ""), (200, VK_AMBIGUOUS)]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,text", REFUSALS)
async def test_coverage_does_not_take_one_refusal_of_the_host_for_a_video_it_cannot_play(status, text):
    client = make_client(SURVEY_ROUTES)
    refuse(client, "id=101&hash", status, text, times=1)

    vk = (await RuchimeProvider().coverage(PAGE, client))["players"]["vk"]

    assert (vk["present"], vk["available"], vk["ranges"]) == (2, 2, "1-2")


@pytest.mark.asyncio
async def test_a_refusal_that_does_not_go_away_is_asked_again_only_as_often_as_allowed(monkeypatch):
    monkeypatch.setattr(VkPlayer, "read_batch", AsyncMock(return_value={}))
    client = make_client(SURVEY_ROUTES, shows={"-1_101": SHOW_DENIED, "-1_102": SHOW_DENIED})
    served = refuse(client, "id=101&hash", 403, "", times=99)

    vk = (await RuchimeProvider().coverage(PAGE, client))["players"]["vk"]

    assert (vk["present"], vk["available"], vk["ranges"]) == (2, 1, "2")
    assert served["refused"] == policy.DEFAULT.attempts


@pytest.mark.asyncio
@pytest.mark.parametrize("status,text", REFUSALS)
async def test_the_quick_report_does_not_take_one_refusal_of_the_host_for_an_unavailable_player(status, text):
    players = f'<button data-player="vk" onclick="switchSource(\'{VK_URL}\',this)">VK</button>'
    client = make_client({"video_ext.php": VK_OPEN, "release/test": release_html([1, 2], players)})
    refuse(client, "video_ext.php", status, text, times=1)

    result = await RuchimeProvider().parse_release("https://1.ruchime.org/release/test", client)

    assert result["players"]["vk"]["available"] is True and result["players"]["vk"]["resolution"] == 1080


@pytest.mark.asyncio
@pytest.mark.parametrize("status,text", REFUSALS)
async def test_a_scan_does_not_take_one_refusal_of_the_host_for_a_missing_source(status, text):
    client = make_client(SURVEY_ROUTES)
    refuse(client, "id=101&hash", status, text, times=1)

    scanned = await RuchimeProvider().scan_episodes(PAGE, client, {"name": "Тест"})

    assert scanned["videos"][0]["url"] == "https://vk.com/video-1_101"


@pytest.mark.asyncio
async def test_a_video_goes_to_the_downloader_as_the_player_link_with_its_key():
    client = make_client({"episode-1": page_html(vk=True), "video_ext.php": VK_OPEN_4K, "release/test": release_html([1])})

    facts = await scan(RuchimeProvider(), client)

    assert (facts[0]["url"], facts[0]["resolution"]) == ("https://vk.com/video-1_100", 2160)
    assert facts[0]["download_url"] == VK_EMBED


@pytest.mark.asyncio
async def test_a_video_the_page_call_refuses_is_a_candidate_when_its_player_page_lists_files():
    client = make_client({"episode-1": page_html(vk=True, allvideo=True), "video_ext.php": VK_OPEN,
                          "fsst.online/embed/77": AV_EMBED_1080, "release/test": release_html([1])},
                         shows={"-1_100": SHOW_DENIED})

    facts = await scan(RuchimeProvider(), client)

    assert facts[0]["url"] == "https://vk.com/video-1_100" and facts[0]["download_url"] == VK_EMBED
    assert facts[0]["resolution"] == 1080


@pytest.mark.asyncio
async def test_a_player_without_a_better_address_leaves_the_download_to_its_own_link():
    client = make_client({"episode-1": page_html(allvideo=True), "fsst.online/embed/77": AV_EMBED_1080,
                          "release/test": release_html([1])})

    facts = await scan(RuchimeProvider(), client)

    assert facts[0]["download_url"] is None


SHELL_TOKEN = ('<html><head><title>VK</title></head><body><script>init = {webToken: {"access_token": "tok-1", '
               '"expired_at": 4102444800}, apiConfigDomains: {"apiDomain": "api.vkvideo.ru"}};</script></body></html>')
PLAYER_PARAMS = ('<html><head><title>Video embed</title></head><body><script>\nvar playerParams = {"params": [{"vid": 100, '
                 '"mp4_720": "https://cdn.test/720.mp4", "mp4_2160": "https://cdn.test/2160.mp4"}]};\n</script></body></html>')
EMBED_100 = "https://vkvideo.ru/video_ext.php?oid=-1&id=100&hash=abc"


def with_api(client, api) -> list[dict]:
    """Answer the `video.get` calls of the client by `api(form)` and note their forms."""
    calls, previous = [], client.post.side_effect

    async def post(url, data=None, headers=None, **kwargs):
        if "/method/video.get" not in url:
            return await previous(url, data=data, headers=headers, **kwargs)
        calls.append(dict(data))
        response = fake_response()
        response.status_code, response.text = 200, api(data)
        return response

    client.post.side_effect = post
    return calls


def api_items(*videos: tuple[int, tuple[int, ...]]) -> str:
    import json
    return json.dumps({"response": {"items": [
        {"owner_id": -1, "id": video, "files": {f"mp4_{height}": f"https://cdn.test/{video}/{height}.mp4" for height in heights}}
        for video, heights in videos]}})


@pytest.mark.asyncio
async def test_a_series_is_asked_in_one_batch_and_only_what_the_batch_leaves_out_is_asked_one_by_one():
    counts = {"id=101&hash": 0, "id=102&hash": 0, "id=103&hash": 0}
    client = make_client({
        "episode-1": player_page(vk=101), "episode-2": player_page(vk=102), "episode-3": player_page(vk=103),
        "id=101&hash": SHELL_TOKEN, "id=102&hash": SHELL_TOKEN, "id=103&hash": VK_OPEN,
        "release/test": release_html([1, 2, 3]),
    }, counts)
    calls = with_api(client, lambda form: api_items((101, (720, 1080)), (102, (2160,))))

    vk = (await RuchimeProvider().coverage("https://1.ruchime.org/release/test", client))["players"]["vk"]

    assert [call["videos"] for call in calls] == ["-1_101_abc,-1_102_abc,-1_103_abc", "-1_103_abc"]
    assert (vk["present"], vk["count"], vk["ranges"]) == (3, 3, "1-3")
    assert vk["resolutions"] == [{"resolution": 2160, "ranges": "2", "count": 1}, {"resolution": 1080, "ranges": "1, 3", "count": 2}]
    assert counts == {"id=101&hash": 1, "id=102&hash": 0, "id=103&hash": 1}


@pytest.mark.asyncio
async def test_a_scan_takes_the_series_from_the_batch_and_hands_the_player_link_to_the_downloader():
    client = make_client({
        "episode-1": player_page(vk=101), "episode-2": player_page(vk=102),
        "id=101&hash": SHELL_TOKEN, "id=102&hash": SHELL_TOKEN, "release/test": release_html([1, 2]),
    })
    with_api(client, lambda form: api_items((101, (720, 1080)), (102, (2160,))))

    facts = await scan(RuchimeProvider(), client)

    assert [(fact["url"], fact["resolution"], fact["download_url"]) for fact in facts] == [
        ("https://vk.com/video-1_101", 1080, "https://vkvideo.ru/video_ext.php?oid=-1&id=101&hash=abc"),
        ("https://vk.com/video-1_102", 2160, "https://vkvideo.ru/video_ext.php?oid=-1&id=102&hash=abc")]


@pytest.mark.asyncio
async def test_a_video_the_batch_does_not_return_and_the_player_page_calls_private_is_closed_not_unknown():
    client = make_client({
        "episode-1": player_page(vk=101), "episode-2": player_page(vk=102),
        "id=101&hash": SHELL_TOKEN, "id=102&hash": VK_CLOSED, "release/test": release_html([1, 2]),
    })
    with_api(client, lambda form: api_items((101, (720,))))

    vk = (await RuchimeProvider().coverage("https://1.ruchime.org/release/test", client))["players"]["vk"]

    assert (vk["present"], vk["count"], vk["ranges"]) == (2, 1, "1")


@pytest.mark.asyncio
async def test_a_player_link_with_a_key_resolves_to_one_fresh_file_asked_for_in_parts():
    client = make_client({"video_ext.php": PLAYER_PARAMS})

    stream = await RuchimeProvider().resolve_stream(EMBED_100, client, 1080)

    assert stream == {"stream_url": "https://cdn.test/720.mp4", "headers": {}, "progressive": True}


@pytest.mark.asyncio
async def test_without_a_cap_the_best_file_is_resolved():
    client = make_client({"video_ext.php": PLAYER_PARAMS})

    stream = await RuchimeProvider().resolve_stream(EMBED_100, client)

    assert stream["stream_url"] == "https://cdn.test/2160.mp4"


@pytest.mark.asyncio
async def test_a_private_video_has_no_stream_and_is_not_asked_again():
    client = make_client({"video_ext.php": VK_CLOSED})

    with pytest.raises(StreamUnavailable) as raised:
        await RuchimeProvider().resolve_stream(EMBED_100, client)

    assert raised.value.transient is False and parse(raised.value.reason)["code"] == "video_private"


@pytest.mark.asyncio
async def test_a_player_page_that_gives_nothing_is_a_stream_that_may_come_later():
    client = make_client({"video_ext.php": VK_AMBIGUOUS}, shows={"-1_100": SHOW_DENIED})

    with pytest.raises(StreamUnavailable) as raised:
        await RuchimeProvider().resolve_stream(EMBED_100, client)

    assert raised.value.transient is True


@pytest.mark.asyncio
async def test_a_link_to_a_video_without_a_key_is_not_a_player_link_of_this_provider():
    client = make_client({"video_ext.php": PLAYER_PARAMS})

    assert await RuchimeProvider().resolve_stream("https://vkvideo.ru/video-1_100", client) is None
    assert await RuchimeProvider().resolve_stream("https://vkvideo.ru/video_ext.php?oid=-1&id=100", client) is None
