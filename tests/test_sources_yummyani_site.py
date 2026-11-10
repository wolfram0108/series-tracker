"""The site yummyani.me, on answers made the way its API gives them (2026-11-06).

The API gives the title and every video of it, an element for each episode of each voice-over of each player, with the
iframe that plays it: Kodik (an iframe of a season), the site's own pages of CDN VideoHub and of VK, Alloha.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import parse_qsl, urlsplit

import pytest

from core.reasons import parse
from modules.sources.players.alloha import AllohaPlayer
from modules.sources.players.cdnvideohub import CdnVideoHubPlayer
from modules.sources.players.kodik import KodikPlayer
from modules.sources.players.registry import PlayerRegistry
from modules.sources.players.vk import VkPlayer
from modules.sources.sites import ReadContext, SiteError
from modules.sources.sites.yummyani import YummyAniSite
from modules.sources.sites.yummyani.site import voice_of
from sources_support import READ_LIMIT_S, Web, within
from test_alloha_player import Browser, answer, track
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


async def read(web: Web, browser: Browser | None = None):
    browser = browser or Browser(answer(track("1")), episodes={"2": answer(track("1"))})

    async def getter():
        return browser

    players = PlayerRegistry([KodikPlayer(), CdnVideoHubPlayer(), VkPlayer(), AllohaPlayer(getter)])
    async with web.client() as http:
        return await within(YummyAniSite(players).read(URL, ReadContext(http, lambda *step: None)), READ_LIMIT_S, "the read")


def test_a_title_is_its_one_address_at_the_one_host_of_the_site():
    site = YummyAniSite(PlayerRegistry())

    assert site.canonical_url("https://yummyani.me/catalog/item/lyubov-pod-gipnozom/") == URL
    assert site.canonical_url(URL) == URL and site.match_url("https://ru.yummyani.me/catalog/item/x")
    assert not site.match_url("https://old.yummyani.me/catalog") and not site.match_url("https://old.yummyani.me/")
    with pytest.raises(ValueError):
        site.canonical_url("https://old.yummyani.me/top")


def test_a_voice_over_is_its_name_and_the_kind_the_site_puts_before_it():
    assert (voice_of("Озвучка AniStar").name, voice_of("Озвучка AniStar").kind) == ("AniStar", "Озвучка")
    assert (voice_of("Субтитры  SovetRomantica").name, voice_of("Субтитры  SovetRomantica").kind) == ("SovetRomantica", "Субтитры")
    assert (voice_of("AniMedia").name, voice_of("AniMedia").kind) == ("AniMedia", "")


async def test_the_table_has_every_player_of_the_api_each_read_as_on_any_page_that_embeds_it(caplog):
    caplog.set_level(logging.INFO, "sources")
    web, browser = site_web(), Browser(answer(track("1")), episodes={"2": answer(track("1"))})
    table = await read(web, browser)
    rows = {player: [row for row in table.rows if row.player == player] for player in ("kodik", "cvh", "vk", "alloha")}

    # Kodik: the list of the season once, however many elements and spellings of the voice-over point at it.
    assert [(row.key, row.voice.name, row.text) for row in rows["kodik"]] == [
        ("kodik:910:501", "AniStar", "1 серия"), ("kodik:910:502", "AniStar", "2 серия")]
    assert rows["kodik"][0].link == f"{URL}#kodik=season/64820/8cc4/501" and web.count("/season/64820/") == 1
    # CDN VideoHub: the widget of the site, by the number of the title at MyAnimeList, with the site's publisher.
    asked = dict(parse_qsl(urlsplit(next(url for _m, url, _b in web.asked if "/player/sv/playlist" in url)).query))
    assert (asked["id"], asked["pub"], asked["aggr"]) == ("41223", "745", "mali") and len(rows["cvh"]) == 1
    # VK: the video by its number; Alloha: a row for each element, with the voice-over as the site words it.
    assert [(row.key, row.link, row.text, row.voice.name, row.voice.kind) for row in rows["vk"]] == [
        ("vk:-35658163_163080986", "https://vk.com/video-35658163_163080986", "6 серия", "WiaDub", "Озвучка")]
    assert [(row.key, row.text, row.voice.name, row.heights) for row in rows["alloha"]] == [
        ("alloha:8bf4/158/1/1", "1 серия", "AniStar", [360, 720, 1080]),
        ("alloha:8bf4/158/1/2", "2 серия", "AniStar", [360, 720, 1080])]
    # The episodes of a season of Alloha are one open of the player, the second a switch inside it.
    assert browser.opened == 1 and browser.switches == ["2"]
    assert table.names == ["Любовь под гипнозом", "Hypnosis love", "Кохання під гіпнозом"]
    assert len({row.key for row in table.rows}) == len(table.rows) == 6
    # A player no player here knows is not read, and the log says which.
    assert any("'Плеер Sibnet'" in record.getMessage() for record in caplog.records)


async def test_the_rows_of_a_list_of_kodik_are_the_episodes_the_site_lists_of_it_not_what_else_the_list_holds():
    """The list of a voice-over at Kodik holds an episode the site does not show of that voice-over (Kodik puts there the
    episode of another one: «3 серия (Другой перевод: Animy)»); the table is what the site shows: its episodes 1 and 2."""
    web = site_web()
    web.route("kodikplayer.com/season/64820/", season_page(("501", "1", "1 серия", ""), ("502", "2", "2 серия", ""),
                                                           ("503", "3", "3 серия (Другой перевод: Animy)", "other")),
              first=True)

    table = await read(web)

    assert [(row.key, row.voice.name, row.text) for row in table.rows if row.player == "kodik"] == [
        ("kodik:910:501", "AniStar", "1 серия"), ("kodik:910:502", "AniStar", "2 серия")]


async def test_a_list_of_kodik_that_does_not_open_is_an_unread_part_named_by_its_voice_over_and_the_rest_is_read():
    broken = "//kodikplayer.com/season/117721/da5c/720p?translations=false&only_episode=true&episode={n}"
    web = site_web()
    web.route("/api/anime/700/videos", json.dumps({"response": [*VIDEOS["response"],
                                                                video("Kodik", "Озвучка Комната Диди", 1, broken)]}),
              first=True)
    web.route("kodikplayer.com/season/117721/", (500, ""), first=True)

    table = await read(web)

    assert [(part.part, part.player_label, part.names, parse(part.reason)["code"]) for part in table.unread] == [
        ("kodik:season/117721/da5c?", "Kodik", ["Комната Диди"], "player_status")]
    assert {row.player for row in table.rows} == {"kodik", "cvh", "vk", "alloha"} and len(table.rows) == 6
    assert {row.part for row in table.rows if row.player == "kodik"} == {"kodik:season/64820/8cc4?"}


@pytest.mark.parametrize("title", [{"response": {"title": "без номера"}}, (404, "")])
async def test_a_title_the_api_does_not_give_is_refused(title):
    with pytest.raises(SiteError) as refused:
        await read(site_web(title))

    assert parse(refused.value.reason)["code"] in {"no_players_empty", "site_status"}
