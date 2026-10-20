"""Unit tests for RuchimeProvider, VideoProviderRegistry, and sources bus topics."""
import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from core import BaseModule, Bus, Envelope
from core.db import Database
from modules.sources.module import SourcesModule
from modules.sources.video_providers.base import BaseVideoProvider
from modules.sources.video_providers.registry import VideoProviderRegistry
from modules.sources.video_providers.ruchime import RuchimeProvider

SAMPLE_RELEASE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Несравненный боевой дух 2 серия — Ruchime</title>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "TVSeries",
        "name": "Несравненный боевой дух",
        "alternateName": "Peerless Martial Spirit",
        "numberOfEpisodes": 43
    }
    </script>
</head>
<body>
    <script>
    const ALL_EPS = [
        {"id": 102702, "number": 2, "compilation_from": null, "compilation_to": null, "title": "Серия 2", "date": "22 Aug 2026"},
        {"id": 102703, "number": 3, "compilation_from": null, "compilation_to": null, "title": "Серия 3", "date": "23 Aug 2026"},
        {"id": 102704, "number": 4, "compilation_from": 1, "compilation_to": 10, "title": "Серии 1-10", "date": "24 Aug 2026"},
        {"id": 102705, "number": 5, "compilation_from": null, "compilation_to": null, "title": "Серия 5", "date": "25 Aug 2026"}
    ];
    </script>
</body>
</html>
"""

SAMPLE_EPISODE_2_VK_HTML = """
<!DOCTYPE html>
<html>
<body>
    <button class="source-btn on" data-player="vk" onclick="switchSource('https://vkvideo.ru/video_ext.php?oid=-170515668&amp;id=456247993&amp;hash=19aabc72095d4117',this)">VK</button>
</body>
</html>
"""

SAMPLE_VK_EMBED_1080_HTML = """
<!DOCTYPE html>
<html>
<body>
    <script>
    var playerParams = {"params": [{"mp4_720": "https://vk.com/720.mp4", "mp4_1080": "https://vk.com/1080.mp4"}]};
    </script>
</body>
</html>
"""

SAMPLE_EPISODE_3_ALLVIDEO_HTML = """
<!DOCTYPE html>
<html>
<body>
    <button class="source-btn on" data-player="allvideo" onclick="switchSource('https://fsst.online/embed/992227/',this)">AllVideo</button>
</body>
</html>
"""

SAMPLE_ALLVIDEO_EMBED_HTML = """
<!DOCTYPE html>
<html>
<head><title>Player</title></head>
<body>
    <script>
    var player = new Playerjs({
        file: "[360p]https://www.incvideo1.online/get_file/360.mp4/,[720p]https://www.incvideo1.online/get_file/720.mp4/,[1080p]https://www.incvideo1.online/get_file/1080.mp4/"
    });
    </script>
</body>
</html>
"""

SAMPLE_EPISODE_4_4K_HTML = """
<!DOCTYPE html>
<html>
<body>
    <button class="source-btn on" data-player="allvideo" onclick="switchSource('https://fsst.online/embed/992228/',this)">AllVideo</button>
</body>
</html>
"""

SAMPLE_ALLVIDEO_4K_EMBED_HTML = """
<!DOCTYPE html>
<html>
<body>
    <script>
    var player = new Playerjs({
        file: "[1080p]https://www.incvideo1.online/get_file/1080.mp4/,[4k]https://www.incvideo1.online/get_file/2160.mp4/"
    });
    </script>
</body>
</html>
"""

SAMPLE_EPISODE_5_NO_PLAYER_HTML = """
<!DOCTYPE html>
<html>
<body>
    <div>No players available yet</div>
</body>
</html>
"""


def test_ruchime_match_url():
    provider = RuchimeProvider()
    assert provider.match_url("https://1.ruchime.org/release/nesravnennyi-boevoi-dux/episode-2")
    assert provider.match_url("https://ruchime.org/release/nesravnennyi-boevoi-dux")
    assert provider.match_url("http://1.ruchime.org/release/test")
    assert not provider.match_url("https://1.ruchime.org/catalog")
    assert not provider.match_url("https://vkvideo.ru/@channel")
    assert not provider.match_url("https://rutracker.org/forum/viewtopic.php?t=1")


def test_video_provider_registry():
    registry = VideoProviderRegistry()
    provider = RuchimeProvider()
    registry.register(provider)

    assert registry.find_provider("https://1.ruchime.org/release/test/episode-1") is provider
    assert registry.find_provider("https://kinozal.tv/details.php?id=1") is None
    assert registry.get_provider("ruchime") is provider
    assert registry.get_provider("nonexistent") is None


def test_ruchime_date_deterministic():
    provider = RuchimeProvider()
    assert provider._parse_date("22 Aug 2026") == "2026-08-22T12:00:00Z"
    assert provider._parse_date("5 сен 2024") == "2024-09-05T12:00:00Z"
    assert provider._parse_date(None) == "1970-01-01T12:00:00Z"
    assert provider._parse_date("invalid date string") == "1970-01-01T12:00:00Z"


@pytest.mark.asyncio
async def test_ruchime_parse_release():
    provider = RuchimeProvider()
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    def side_effect(url, headers=None, follow_redirects=True, **kwargs):
        resp = AsyncMock(spec=httpx.Response)
        resp.status_code = 200
        if "episode-2" in url:
            resp.text = SAMPLE_EPISODE_2_VK_HTML
        elif "video_ext.php" in url:
            resp.text = SAMPLE_VK_EMBED_1080_HTML
        elif "episode-3" in url:
            resp.text = SAMPLE_EPISODE_3_ALLVIDEO_HTML
        elif "992227" in url:
            resp.text = SAMPLE_ALLVIDEO_EMBED_HTML
        elif "episode-4" in url:
            resp.text = SAMPLE_EPISODE_4_4K_HTML
        elif "992228" in url:
            resp.text = SAMPLE_ALLVIDEO_4K_EMBED_HTML
        elif "episode-5" in url:
            resp.text = SAMPLE_EPISODE_5_NO_PLAYER_HTML
        else:
            resp.text = SAMPLE_RELEASE_HTML
        return resp

    mock_client.get.side_effect = side_effect

    result = await provider.parse_release("https://1.ruchime.org/release/nesravnennyi-boevoi-dux/episode-2", mock_client)

    assert result["source_type"] == "web_video"
    assert result["provider"] == "ruchime"
    assert result["canonical_url"] == "https://1.ruchime.org/release/nesravnennyi-boevoi-dux"
    assert result["title"]["ru"] == "Несравненный боевой дух"
    assert result["title"]["en"] == "Peerless Martial Spirit"
    assert len(result["titles"]) == 4
    assert result["titles"][0] == "Несравненный боевой дух - 2 серия"

    # The release page has no preview player here; its per-episode qualities
    # are intentionally not probed during parse anymore.
    assert result["resolution"] is None
    assert len(result["videos"]) == 4
    assert all(video["resolution"] is None for video in result["videos"])
    assert result["players"]["vk"]["present"] is False
    assert result["players"]["vibix"]["present"] is False


@pytest.mark.asyncio
async def test_ruchime_scan_episodes_cascade_and_filtering():
    provider = RuchimeProvider()
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    def side_effect(url, headers=None, follow_redirects=True, **kwargs):
        resp = AsyncMock(spec=httpx.Response)
        resp.status_code = 200
        if "episode-2" in url:
            resp.text = SAMPLE_EPISODE_2_VK_HTML
        elif "video_ext.php" in url:
            resp.text = SAMPLE_VK_EMBED_1080_HTML
        elif "episode-3" in url:
            resp.text = SAMPLE_EPISODE_3_ALLVIDEO_HTML
        elif "992227" in url:
            resp.text = SAMPLE_ALLVIDEO_EMBED_HTML
        elif "episode-4" in url:
            resp.text = SAMPLE_EPISODE_4_4K_HTML
        elif "992228" in url:
            resp.text = SAMPLE_ALLVIDEO_4K_EMBED_HTML
        elif "episode-5" in url:
            resp.text = SAMPLE_EPISODE_5_NO_PLAYER_HTML
        else:
            resp.text = SAMPLE_RELEASE_HTML
        return resp

    mock_client.get.side_effect = side_effect

    result = await provider.scan_episodes(
        "https://1.ruchime.org/release/nesravnennyi-boevoi-dux",
        mock_client,
        {"name": "Несравненный боевой дух"}
    )
    facts = result["videos"]

    # Episode 5 had no player and should be filtered out (not dead HTML URL)
    assert len(facts) == 3

    # Episode 2: VK primary
    assert facts[0]["url"] == "https://vk.com/video-170515668_456247993"
    assert facts[0]["title"] == "Несравненный боевой дух - 2 серия"
    assert facts[0]["publication_date"] == "2026-08-22T12:00:00Z"
    assert facts[0]["resolution"] == 1080

    # Episode 3: AllVideo secondary
    assert facts[1]["url"] == "https://fsst.online/embed/992227/"
    assert facts[1]["title"] == "Несравненный боевой дух - 3 серия"
    assert facts[1]["publication_date"] == "2026-08-23T12:00:00Z"
    assert facts[1]["resolution"] == 1080

    # Episode 4: AllVideo 4K
    assert facts[2]["url"] == "https://fsst.online/embed/992228/"
    assert facts[2]["title"] == "Несравненный боевой дух - 1-10 серия"
    assert facts[2]["publication_date"] == "2026-08-24T12:00:00Z"
    assert facts[2]["resolution"] == 2160

    report = result["players"]
    assert report["episodes"] == 4
    assert report["sources"] == {"vk": 1, "allvideo": 2}
    assert report["players"]["vk"]["available"] == 1
    assert report["players"]["allvideo"]["available"] == 2
    assert report["players"]["vibix"]["present"] == 0
    assert report["holes"] == [{"episode": 5, "reason": ""}]


@pytest.mark.asyncio
async def test_ruchime_resolve_stream():
    provider = RuchimeProvider()
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    # 1. 1080p AllVideo embed
    resp_1080 = AsyncMock(spec=httpx.Response)
    resp_1080.status_code = 200
    resp_1080.text = SAMPLE_ALLVIDEO_EMBED_HTML
    mock_client.get.return_value = resp_1080

    stream = await provider.resolve_stream("https://fsst.online/embed/992227/", mock_client)
    assert stream is not None
    assert stream["stream_url"] == "https://www.incvideo1.online/get_file/1080.mp4/"
    assert stream["headers"]["Referer"] == "https://fsst.online/"

    # 2. 4K AllVideo embed
    resp_4k = AsyncMock(spec=httpx.Response)
    resp_4k.status_code = 200
    resp_4k.text = SAMPLE_ALLVIDEO_4K_EMBED_HTML
    mock_client.get.return_value = resp_4k

    stream_4k = await provider.resolve_stream("https://fsst.online/embed/992228/", mock_client)
    assert stream_4k is not None
    assert stream_4k["stream_url"] == "https://www.incvideo1.online/get_file/2160.mp4/"

    # 3. Non-AllVideo URL returns None
    assert await provider.resolve_stream("https://vk.com/video-1_2", mock_client) is None


class DummyClientModule(BaseModule):
    name = "test_client"


@pytest.mark.asyncio
async def test_sources_module_bus_parse_and_scan():
    bus = Bus()
    db = Database(":memory:")
    await db.execute("""
        CREATE TABLE trackers (
            id INTEGER PRIMARY KEY,
            canonical_name TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            mirrors TEXT,
            parser_class TEXT NOT NULL,
            auth_type TEXT NOT NULL,
            ui_features TEXT
        )
    """)

    sources = SourcesModule(bus, db)
    client = DummyClientModule(bus)
    await sources.start()
    await client.start()

    try:
        with patch.object(sources._http, "get") as mock_get:
            resp = AsyncMock(spec=httpx.Response)
            resp.status_code = 200
            resp.text = SAMPLE_RELEASE_HTML
            mock_get.return_value = resp

            # 1. sources.parse
            parsed = await client.request("sources.parse", {
                "url": "https://1.ruchime.org/release/nesravnennyi-boevoi-dux/episode-2"
            })
            assert parsed["source_type"] == "web_video"
            assert parsed["service"] == "ruchime"
            assert parsed["title"]["ru"] == "Несравненный боевой дух"
            assert len(parsed["titles"]) == 4

        with patch.object(RuchimeProvider, "scan_episodes") as mock_scan:
            mock_scan.return_value = [
                {"title": "Эп 1", "url": "https://vk.com/video-1_2", "publication_date": "2026-08-22T12:00:00Z", "resolution": 1080}
            ]

            # 2. sources.video.scan
            scan_reply = await client.request("sources.video.scan", {
                "url": "https://1.ruchime.org/release/nesravnennyi-boevoi-dux",
                "series": {"name": "Несравненный боевой дух"}
            })
            assert "videos" in scan_reply
            assert len(scan_reply["videos"]) == 1
            assert scan_reply["videos"][0]["url"] == "https://vk.com/video-1_2"

        with patch.object(RuchimeProvider, "resolve_stream") as mock_stream:
            mock_stream.return_value = {
                "stream_url": "https://cdn.example.com/stream.mp4",
                "headers": {"Referer": "https://fsst.online/"}
            }

            # 3. sources.video.stream
            stream_reply = await client.request("sources.video.stream", {
                "url": "https://fsst.online/embed/992227/"
            })
            assert stream_reply["handled"] is True
            assert stream_reply["stream_url"] == "https://cdn.example.com/stream.mp4"
            assert stream_reply["headers"]["Referer"] == "https://fsst.online/"
    finally:
        await client.stop()
        await sources.stop()


def test_allvideo_resolution_extraction():
    provider = RuchimeProvider()

    # 1. Playerjs quality brackets [360p] ... [1080p]
    html_playerjs = """
    file:"[360p]https://www.incvideo1.online/get_file/360p.mp4/,[720p]https://www.incvideo1.online/get_file/720p.mp4/,[1080p]https://www.incvideo1.online/get_file/1080p.mp4/"
    """
    assert provider._extract_allvideo_resolution(html_playerjs) == 1080

    # 2. 4K quality
    html_4k = """
    file:"[720p]url,[1080p]url,[4k]url"
    """
    assert provider._extract_allvideo_resolution(html_4k) == 2160

    # 3. Filename patterns in URL
    html_url_res = """file:"https://video.host/stream_720p.mp4" """
    assert provider._extract_allvideo_resolution(html_url_res) == 720

    # 4. Fallback to title
    html_title = "<title>Watch video 1080p HD</title>"
    assert provider._extract_allvideo_resolution(html_title) == 1080
