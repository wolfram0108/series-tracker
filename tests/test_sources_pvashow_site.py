"""The site anime.pvashow.com, on answers made the way the site and its playlist give them (2026-11-06).

The page embeds a playlist of AllVideo; the playlist lists every episode with the files of its video by quality, so the
table is read of the playlist alone: no embed of a video is asked.
"""
from __future__ import annotations

import json

import pytest

from core.contracts import RowState
from modules.sources.players.allvideo import AllVideoPlayer
from modules.sources.players.registry import PlayerRegistry
from modules.sources.sites import ReadContext
from modules.sources.sites.pvashow import PvaShowSite
from sources_support import READ_LIMIT_S, Web, within

URL = "https://anime.pvashow.com/4-boevoj-master.html"
FILES = "https://www.incvideo1.online/get_file/20/{sign}/655000/{number}/{number}{suffix}.mp4"


def files(number: int, heights=(1080, 720, 360)) -> str:
    return ",".join(f"[{height}p]" + FILES.format(sign=f"s{height}", number=number, suffix="" if height == 1080 else f"_{height}p")
                    for height in heights)


def page(playlist: bool = True) -> str:
    frame = ('<iframe src="https://csst.online/playlist_iframe/22771/" width="610" height="370" allowfullscreen></iframe>'
             if playlist else "")
    return ('<html><div class="short-header"><h1 class="short-title fx-1">Боевой мастер</h1></div>'
            '<ul class="short-list"> <li><span>Озвучено:</span>ПВА ШОУ</li> <li><span>Год:</span>2020</li></ul>'
            f"{frame}</html>")


ELEMENTS = [
    {"comment": "Боевой Мастер - 1 серия", "file": files(655613), "poster": "p.jpg"},
    {"comment": "Боевой мастер - 677 серия", "file": files(1029408, (360,)), "poster": "p.jpg"},
    {"comment": "Боевой мастер - 677 серия", "file": files(1029408, (360,)), "poster": "p.jpg"},
    {"comment": "Без файлов", "file": "", "poster": "p.jpg"},
    {"title": "Спецвыпуски", "folder": [{"comment": "Спецвыпуск 1", "file": files(700001, (720, 360))}]},
]


def playlist(elements=ELEMENTS) -> str:
    return (f"<html><body><div id=\"Playlist22771\"></div><script>var player = new Playerjs({{ id: 'Playlist22771', "
            f"file: \n{json.dumps(elements, ensure_ascii=False)}, autoplay: 0 }});</script></body></html>")


def site_web(text: str | None = None, listed: str | None = None) -> Web:
    web = Web().route("anime.pvashow.com/4-", text if text is not None else page())
    web.route("csst.online/playlist_iframe/22771", listed if listed is not None else playlist())
    return web


async def read(web: Web, recent: int | None = None):
    site = PvaShowSite(PlayerRegistry([AllVideoPlayer()]))
    async with web.client() as http:
        return await within(site.read(URL, ReadContext(http, lambda *step: None, recent)), READ_LIMIT_S, "the read")


def test_a_title_page_is_its_one_address_and_nothing_else_is_a_title_page():
    site = PvaShowSite(PlayerRegistry())

    assert site.canonical_url(URL + "#comments") == URL and site.match_url(URL)
    assert not site.match_url("https://anime.pvashow.com/page/2/")
    with pytest.raises(ValueError):
        site.canonical_url("https://anime.pvashow.com/")


async def test_a_row_is_a_video_of_allvideo_with_the_text_and_the_heights_the_playlist_gives_and_no_embed_is_asked():
    web = site_web()
    table = await read(web)

    assert [(row.key, row.text, row.group, row.voice.name, row.heights, row.state) for row in table.rows] == [
        ("allvideo:655613", "Боевой Мастер - 1 серия", "", "ПВА ШОУ", [360, 720, 1080], RowState.PLAYABLE),
        ("allvideo:1029408", "Боевой мастер - 677 серия", "", "ПВА ШОУ", [360], RowState.PLAYABLE),
        ("allvideo:700001", "Спецвыпуск 1", "Спецвыпуски", "ПВА ШОУ", [360, 720], RowState.PLAYABLE)]
    # The link is the embed of the video at the host of the playlist: the one AllVideo resolves a stream of.
    assert table.rows[0].link == "https://csst.online/embed/655613/"
    assert AllVideoPlayer.video_of(table.rows[0].link) == "655613" and AllVideoPlayer.owns(table.rows[0].link)
    assert web.count("/embed/") == 0 and table.site_voice == "ПВА ШОУ"
