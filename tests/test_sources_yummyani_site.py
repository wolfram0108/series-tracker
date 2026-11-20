"""The site yummyani.me, on answers made the way its API gives them (2026-11-06).

The API gives the title and every video of it, an element for each episode of each voice-over of each player, with the
iframe that plays it: Kodik (an iframe of a season), the site's own pages of CDN VideoHub and of VK, Alloha.
"""
from __future__ import annotations

import json


from core.reasons import parse
from modules.sources.players.alloha import AllohaPlayer
from modules.sources.players.cdnvideohub import CdnVideoHubPlayer
from modules.sources.players.kodik import KodikPlayer
from modules.sources.players.registry import PlayerRegistry
from modules.sources.players.vk import VkPlayer
from modules.sources.sites import ReadContext
from modules.sources.sites.yummyani import YummyAniSite
from sources_support import READ_LIMIT_S, Web, within
from alloha_model import PROFILE, AllohaModel
from test_sources_cvh_kodik import ftor_links, season_page

URL = "https://old.yummyani.me/catalog/item/lyubov-pod-gipnozom"
TITLE = {"response": {"anime_id": 700, "anime_url": "lyubov-pod-gipnozom", "title": "Любовь под гипнозом",
                      "other_titles": ["Hypnosis love", "Кохання під гіпнозом"]}}
KODIK = "//kodikplayer.com/season/64820/8cc4/720p?translations=false&only_episode=true&episode={n}"
ALLOHA = "//alloha.yani.tv/?token_movie=8bf4&translation=158&season=1&episode={n}&token=8b55&hidden=translation"
CVH = "//ru.yummyani.me/iframeCVH.html?dubbing_code=AniStar&anime_id=41223&episode={n}&dubbing=x"
VK = "//ru.yummyani.me/iframeVK.html?id=-35658163_16308098{n}"
SIBNET = "//video.sibnet.ru/shell.php?videoid={n}"


def video(player: str, dubbing: str, number: int, iframe: str) -> dict:
    return {"video_id": number * 1000 + len(dubbing), "number": str(number), "iframe_url": iframe.format(n=number),
            "data": {"player": f"Плеер {player}", "dubbing": dubbing, "player_id": 1}, "index": number}


VIDEOS = {"response": [
    *(video("Kodik", "Озвучка AniStar", n, KODIK) for n in (1, 2)),
    # The site lists one voice-over twice, under two spellings, with the same iframes: one list, read once.
    *(video("Kodik", "Озвучка Anistar", n, KODIK) for n in (1, 2)),
    *(video("CVH", "Озвучка AniStar", n, CVH) for n in (1, 2)),
    *(video("Alloha", "Озвучка AniStar", n, ALLOHA) for n in (1, 2)),
    video("VK", "Озвучка WiaDub", 6, VK),
    video("Sibnet", "Субтитры Kansai", 1, SIBNET),
]}
CVH_PLAYLIST = {"items": [{"cvhId": "c1", "vkId": "v1", "voiceStudio": "AniStar", "voiceType": "Многоголосый", "season": 1,
                           "episode": 1}]}


def site_web(title: dict | tuple = TITLE) -> Web:
    web = Web()
    web.route("/api/anime/700/videos", json.dumps(VIDEOS))
    web.route("/api/anime/lyubov-pod-gipnozom", title if isinstance(title, tuple) else json.dumps(title))
    web.route("kodikplayer.com/season/64820/", season_page(("501", "1", "1 серия", ""), ("502", "2", "2 серия", "")))
    web.route("kodikplayer.com/ftor", lambda request: json.dumps(ftor_links(360, 720)), method="POST")
    web.route("/player/sv/playlist", json.dumps(CVH_PLAYLIST))
    web.route("/player/sv/video/", json.dumps({"sources": {"mpegHighUrl": "https://okcdn.test/v1/720"}}))
    return web


async def read(web: Web, model: AllohaModel | None = None):
    # The host of Alloha: the player has both episodes of the voice-over of the site.
    model = model or AllohaModel(voices={"158": {1: [1, 2]}})
    players = PlayerRegistry([KodikPlayer(), CdnVideoHubPlayer(), VkPlayer(), AllohaPlayer(PROFILE, client=model.client)])
    async with web.client() as http:
        return await within(YummyAniSite(players).read(URL, ReadContext(http, lambda *step: None)), READ_LIMIT_S, "the read")


async def test_a_list_of_kodik_that_does_not_open_is_an_unread_part_named_by_its_voice_over_and_the_rest_is_read():
    broken = "//kodikplayer.com/season/117721/da5c/720p?translations=false&only_episode=true&episode={n}"
    web = site_web()
    web.route("/api/anime/700/videos", json.dumps({"response": [*VIDEOS["response"],
                                                                video("Kodik", "Озвучка Комната Диди", 1, broken)]}),
              first=True)
    web.route("kodikplayer.com/season/117721/", (500, ""), first=True)

    table = await read(web)

    assert [(part.part, part.player_label, part.names, parse(part.reason)["code"]) for part in table.unread] == [
        ("kodik:season/117721/da5c?", "Kodik", ("Комната Диди",), "player_status")]
    assert {row.player for row in table.rows} == {"kodik", "cvh", "vk", "alloha"} and len(table.rows) == 6
    assert {row.part for row in table.rows if row.player == "kodik"} == {"kodik:season/64820/8cc4?"}
