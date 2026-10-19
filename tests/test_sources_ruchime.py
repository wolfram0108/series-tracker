"""Тесты для провайдера Ruchime и реестра VideoProviderRegistry."""
import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from core import Bus, Envelope
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
        {"id": 102704, "number": 4, "compilation_from": 1, "compilation_to": 10, "title": "Серии 1-10", "date": "24 Aug 2026"}
    ];
    </script>
</body>
</html>
"""

SAMPLE_EPISODE_VK_HTML = """
<!DOCTYPE html>
<html>
<body>
    <button class="source-btn on" data-player="vk" onclick="switchSource('https://vkvideo.ru/video_ext.php?oid=-170515668&amp;id=456247993&amp;hash=19aabc72095d4117',this)">VK</button>
</body>
</html>
"""

SAMPLE_EPISODE_ALLVIDEO_HTML = """
<!DOCTYPE html>
<html>
<body>
    <button class="source-btn on" data-player="allvideo" onclick="switchSource('https://fsst.online/embed/992227/',this)">AllVideo</button>
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


@pytest.mark.asyncio
async def test_ruchime_parse_release():
    provider = RuchimeProvider()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = SAMPLE_RELEASE_HTML
    mock_client.get.return_value = mock_resp

    result = await provider.parse_release("https://1.ruchime.org/release/nesravnennyi-boevoi-dux/episode-2", mock_client)

    assert result["source_type"] == "web_video"
    assert result["provider"] == "ruchime"
    assert result["canonical_url"] == "https://1.ruchime.org/release/nesravnennyi-boevoi-dux"
    assert result["title"]["ru"] == "Несравненный боевой дух"
    assert result["title"]["en"] == "Peerless Martial Spirit"
    assert len(result["titles"]) == 3
    assert result["titles"][0] == "Несравненный боевой дух - 2 серия"
    assert result["titles"][1] == "Несравненный боевой дух - 3 серия"
    assert result["titles"][2] == "Несравненный боевой дух - 1-10 серия"


@pytest.mark.asyncio
async def test_ruchime_scan_episodes_cascade():
    provider = RuchimeProvider()

    mock_client = AsyncMock(spec=httpx.AsyncClient)

    def side_effect(url, headers=None):
        resp = AsyncMock(spec=httpx.Response)
        resp.status_code = 200
        if "episode-2" in url:
            resp.text = SAMPLE_EPISODE_VK_HTML
        elif "episode-3" in url:
            resp.text = SAMPLE_EPISODE_ALLVIDEO_HTML
        else:
            resp.text = SAMPLE_RELEASE_HTML
        return resp

    mock_client.get.side_effect = side_effect

    facts = await provider.scan_episodes(
        "https://1.ruchime.org/release/nesravnennyi-boevoi-dux",
        mock_client,
        {"name": "Несравненный боевой дух"}
    )

    assert len(facts) == 3
    # Эпизод 2: найден VK
    assert facts[0]["url"] == "https://vk.com/video-170515668_456247993"
    assert facts[0]["title"] == "Несравненный боевой дух - 2 серия"
    assert facts[0]["publication_date"] == "2026-08-22T12:00:00Z"
    assert facts[0]["resolution"] == 1080

    # Эпизод 3: VK нет, фолбэк на AllVideo
    assert facts[1]["url"] == "https://fsst.online/embed/992227/"
    assert facts[1]["title"] == "Несравненный боевой дух - 3 серия"
    assert facts[1]["publication_date"] == "2026-08-23T12:00:00Z"

    # Эпизод 4 (компиляция)
    assert facts[2]["title"] == "Несравненный боевой дух - 1-10 серия"


from core import BaseModule, Bus, Envelope


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
        # Мокаем http-клиент в провайдере
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
            assert len(parsed["titles"]) == 3

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
    finally:
        await client.stop()
        await sources.stop()


def test_allvideo_and_vk_resolution_extraction():
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

