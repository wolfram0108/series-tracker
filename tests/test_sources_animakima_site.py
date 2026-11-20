"""The site animakima.ru and its own player, on answers made the way the site and its CDN give them (2026-11-05).

The page holds the buttons of the players and the list of episodes; the API gives the view of each button. The own player
lists files under labels that are not frame heights.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qsl

import httpx
import pytest

from core.reasons import parse
from modules.sources.players.animakima import AnimakimaPlayer
from modules.sources.players.cdnvideohub import CdnVideoHubPlayer
from modules.sources.players.kodik import KodikPlayer
from modules.sources.players.registry import PlayerRegistry
from modules.sources.players.streams import resolve_stream
from modules.sources.sites import ReadContext, SiteError
from modules.sources.sites.animakima import AnimakimaSite
from core.contracts import RowState
from sources_support import READ_LIMIT_S, STEP_LIMIT_S, Web, within
from test_sources_cvh_kodik import IFRAMES, ftor_links

URL = "https://animakima.ru/pages/vechnaya-volya-4-tv-4/"
AID = "8167"
EDGE = "https://edge04.cdn.linglongstrim.ru/s/sa1.k1.TOKEN/v/auto-v1/videos/8167"
PLAIN = "https://linglongstrim.ru/videos/8167"


def page(views=("cdnvh", "main", "kodik"), sids=(("182980", "Эпизод 1"), ("182981", "Эпизод 2"))) -> str:
    buttons = "".join(f"<button class='btn btn--player-select' data-player='{view}' type='button'><span>{view}</span></button>"
                      for view in views)
    listed = "".join(f"<button class='btn btn--select-video' type='button' data-sid='{sid}'>{label}</button>"
                     for sid, label in sids)
    return ("<html><h1>Вечная воля (4 сезон)</h1><div class=\"expand-list-content\"><span>Момент вечности 4</span>"
            "<span>A Will Eternal Final Season</span></div>"
            f'<title-player class="player" view="cdnvh" aid="{AID}" navbar="main proton rutube vk">'
            f"<div class='player-select'>{buttons}</div><div class='chapters-select-list'>{listed}</div></title-player></html>")


def files(path: str, episode: str) -> str:
    return ", ".join(f"[{label}p]{path}/{episode}/{label}/index.m3u8" for label in (360, 720, 1080))


VIEWS = {
    "cdnvh": {"success": True, "data": '<video-player\n\tid="myPlayer"\n\tdata-title-id="62248"\n\tdata-publisher-id="1126"'
                                       '\n\tdata-aggregator="mali"\n\t></video-player>'},
    "kodik": {"success": True, "data": "<iframe src='//kodikplayer.com/serial/50228/7bbb4c6b/720p' frameborder='0'></iframe>"},
    "alloha": {"success": True, "data": "<iframe src='https://alloha.test/embed/1'></iframe>"},
}
EPISODES = {"182980": {"success": True, "data": files(EDGE, "01")},
            "182981": {"success": True, "data": files(PLAIN, "02")},
            "999": {"success": True, "data": "https://animakima.fun/public/intro.mp4", "message": "link invalid, sqlResult is null"}}
PLAYLIST = {"items": [{"cvhId": "c1", "vkId": "v1", "voiceStudio": "AniStar", "voiceType": "Многоголосый", "season": 4,
                       "episode": 1}]}


async def heights_by_label(url: str) -> int | None:
    """Every file of the site measured at 720 except the 1080 file of episode 2 (a frame the label does state)."""
    return 1080 if url.endswith("/02/1080/index.m3u8") else 720


def api(request: httpx.Request) -> str | tuple[int, str]:
    form = dict(parse_qsl(request.content.decode()))
    if form.get("action") != "get_player_view" or form.get("aid") != AID:
        return json.dumps({"success": False, "data": "", "message": "Error: response is null"})
    found = EPISODES.get(form["sid"]) if form["type"] == "main" else VIEWS.get(form["type"])
    return json.dumps(found or {"success": False, "data": "", "message": "Error: response is null"})


def site_web(text: str | None = None) -> Web:
    web = Web().route("/pages/", text if text is not None else page())
    web.route("animakima.ru/api/", api, method="POST")
    web.route("/player/sv/playlist", json.dumps(PLAYLIST))
    web.route("/player/sv/video/", json.dumps({"sources": {"mpegHighUrl": "https://okcdn.test/v1/720"}}))
    web.route("kodikplayer.com/serial/", lambda request: IFRAMES.get(str(request.url).split("/serial/")[1].split("/")[0],
                                                                     (404, "")))
    web.route("kodikplayer.com/ftor", lambda request: json.dumps(ftor_links(360, 720)), method="POST")
    return web


def registry(measure=heights_by_label) -> PlayerRegistry:
    return PlayerRegistry([CdnVideoHubPlayer(), KodikPlayer(), AnimakimaPlayer(measure)])


async def read(web: Web, players: PlayerRegistry | None = None, recent: int | None = None):
    site = AnimakimaSite(players or registry())
    async with web.client() as http:
        return await within(site.read(URL, ReadContext(http, lambda *step: None, recent)), READ_LIMIT_S, "the read")


# --- the address and the page ---------------------------------------------------------------------------------------------------

def test_a_title_page_is_its_one_address_and_nothing_else_is_a_title_page():
    site = AnimakimaSite(registry())

    assert site.canonical_url("https://animakima.ru/pages/vechnaya-volya-4-tv-4?x=1") == URL
    assert site.canonical_url(URL) == URL and site.match_url(URL)
    assert not site.match_url("https://animakima.ru/catalog/") and not site.match_url("https://animakima.ru/pages/a/b/")
    with pytest.raises(ValueError):
        site.canonical_url("https://animakima.ru/")


# --- the table -------------------------------------------------------------------------------------------------------------------

async def test_an_episode_the_site_has_no_file_for_is_absent_and_an_answer_it_does_not_word_is_not_obtained():
    web = site_web(page(views=("main",), sids=(("999", "Эпизод 3"), ("555", "Эпизод 4"))))

    rows = {row.text: row for row in (await read(web)).rows}

    assert rows["Эпизод 3"].state is RowState.ABSENT
    assert rows["Эпизод 4"].state is RowState.NOT_OBTAINED and rows["Эпизод 4"].reason


async def test_only_the_last_episodes_are_read_when_the_read_is_told_so():
    table = await read(site_web(), recent=1)

    assert [row.text for row in table.rows if row.player == "animakima"] == ["Эпизод 2"]


async def test_a_page_none_of_whose_views_the_api_gives_refuses_the_table():
    web = site_web(page(views=("cdnvh", "kodik")))
    web.route("animakima.ru/api/", (403, "forbidden"), method="POST", first=True)

    with pytest.raises(SiteError) as refused:
        await read(web)

    assert parse(refused.value.reason)["code"] == "site_status"


# --- the own player ---------------------------------------------------------------------------------------------------------------

async def stream(link: str, web: Web, players: PlayerRegistry, cap: int | None = None) -> dict:
    async with web.client() as http:
        return await within(resolve_stream(players, link, http, cap), STEP_LIMIT_S, "the stream")
