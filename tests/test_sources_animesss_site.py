"""The site animesss.com, on answers made the way the site gives them (2026-11-06).

The page holds the number of the title; the playlist comes from a request of its own and names its sources: the widget of
CDN VideoHub by the numbers of the title, and an iframe of Kodik limited to each voice-over.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import parse_qsl, urlsplit

import pytest

from core.reasons import parse
from modules.sources.players.cdnvideohub import CdnVideoHubPlayer
from modules.sources.players.kodik import KodikPlayer
from modules.sources.players.registry import PlayerRegistry
from modules.sources.sites import ReadContext, SiteError
from modules.sources.sites.animesss import AnimesssSite
from modules.sources.sites.animesss.site import donors, news_id, title_names
from sources_support import READ_LIMIT_S, Web, within
from test_sources_cvh_kodik import IFRAMES, ftor_links

URL = "https://animesss.com/aniserials/video/action/1171-protivostojaschij-nebesam.html"
BREADCRUMB = {"@context": "https://schema.org", "@graph": [{"@type": "BreadcrumbList", "itemListElement": [
    {"@type": "ListItem", "position": 1, "item": {"@id": "https://animesss.com/", "name": "Аниме"}},
    {"@type": "ListItem", "position": 2, "item": {"@id": URL, "name": "Противостоящий небесам"}}]}]}


def page(number: str | None = "1171") -> str:
    player = (f'<div id="kodik_player_ajax" data-news_id="{number}" data-has_cache="no" data-player_cookie=\'0\'></div>'
              if number else "")
    return (f'<html><head><script type="application/ld+json">{json.dumps(BREADCRUMB, ensure_ascii=False)}</script></head>'
            f'<body><h1 itemprop="name">Противостоящий небесам аниме</h1>{player}</body></html>')


def videohub(voice: str, label: str) -> str:
    return (f'<li class="b-translator__item" data-this-translator="videohub_videohub_{voice}" data-player-query="provider='
            f'videohub&amp;title_id=49448&amp;publisher_id=1319&amp;aggregator=mali&amp;voice={voice}&amp;season=1'
            f'&amp;episode=1">{label}</li>')


def kodik(translation: str, media: str, media_hash: str, label: str) -> str:
    return (f'<li class="b-translator__item" data-this-translator="kodik_{translation}" data-this-link="//kodikplayer.com/'
            f'serial/{media}/{media_hash}/720p?translations=false&amp;only_translations={translation}">{label}</li>')


PLAYLIST = (
    '<div class="b-donors__block"><ul id="donors-list" class="b-donors__list">'
    '<li class="b-donor__item active" data-donor="videohub">Новый плеер</li><li class="b-donor__item" data-donor="kodik">'
    'Kodik</li><li class="b-donor__item" data-donor="sibnet">Sibnet</li></ul></div>'
    '<div class="animesss-donor-content" data-donor-block="videohub"><ul class="b-translators__list" data-donor="videohub">'
    + videohub("pvashow", "ПВА ШОУ") + videohub("animy", "AniMy / RuChiMe") + "</ul></div>"
    '<div class="animesss-donor-content" data-donor-block="kodik"><ul class="b-translators__list" data-donor="kodik">'
    + kodik("1043", "50228", "7bbb4c6b", "Animy") + kodik("910", "49002", "b1b36deb", "AniStar") + "</ul></div>"
    '<div class="animesss-donor-content" data-donor-block="sibnet"><ul class="b-translators__list" data-donor="sibnet">'
    '<li class="b-translator__item" data-this-translator="sibnet_1" data-this-link="https://video.sibnet.ru/shell.php?'
    'videoid=1">Sibnet</li></ul></div>')
CVH_PLAYLIST = {"items": [
    {"cvhId": "c1", "vkId": "v1", "voiceStudio": "ПВА ШОУ", "voiceType": "Многоголосый", "season": 1, "episode": 1},
    {"cvhId": "c2", "vkId": "v2", "voiceStudio": "AniMy / RuChiMe", "voiceType": "Многоголосый", "season": 1, "episode": 1}]}


def site_web(text: str | None = None, playlist: str = PLAYLIST) -> Web:
    web = Web().route("animesss.com/aniserials/", text if text is not None else page())
    web.route("animesss.com/index.php", playlist)
    web.route("/player/sv/playlist", json.dumps(CVH_PLAYLIST))
    web.route("/player/sv/video/", json.dumps({"sources": {"mpegHighUrl": "https://okcdn.test/v1/720"}}))
    web.route("kodikplayer.com/serial/", lambda request: IFRAMES.get(str(request.url).split("/serial/")[1].split("/")[0],
                                                                     (404, "")))
    web.route("kodikplayer.com/ftor", lambda request: json.dumps(ftor_links(360, 720)), method="POST")
    return web


async def read(web: Web):
    site = AnimesssSite(PlayerRegistry([CdnVideoHubPlayer(), KodikPlayer()]))
    async with web.client() as http:
        return await within(site.read(URL, ReadContext(http, lambda *step: None)), READ_LIMIT_S, "the read")


def test_a_title_page_is_its_one_address_and_nothing_else_is_a_title_page():
    site = AnimesssSite(PlayerRegistry())

    assert site.canonical_url(URL + "?from=main") == URL and site.canonical_url(URL) == URL and site.match_url(URL)
    assert not site.match_url("https://animesss.com/") and not site.match_url("https://animesss.com/aniserials/video/")
    with pytest.raises(ValueError):
        site.canonical_url("https://animesss.com/tags/x/")


def test_the_page_gives_the_names_and_the_number_and_the_playlist_names_its_sources():
    assert title_names(page()) == ["Противостоящий небесам", "Противостоящий небесам аниме"]
    assert news_id(page()) == "1171" and news_id(page(None)) is None
    assert [(donor, item["data-this-translator"]) for donor, item in donors(PLAYLIST)] == [
        ("videohub", "videohub_videohub_pvashow"), ("videohub", "videohub_videohub_animy"), ("kodik", "kodik_1043"),
        ("kodik", "kodik_910"), ("sibnet", "sibnet_1")]


async def test_the_table_has_the_playlist_of_cdn_videohub_and_the_voice_of_each_iframe_of_kodik(caplog):
    caplog.set_level(logging.INFO, "sources")
    web = site_web()
    table = await read(web)

    assert [(row.player, row.voice.name, row.text) for row in table.rows if row.player == "cvh"] == [
        ("cvh", "ПВА ШОУ", "1 серия"), ("cvh", "AniMy / RuChiMe", "1 серия")]
    # Each iframe is limited to its voice-over: the voice-overs are the ones the playlist lists, each read once.
    kodik_rows = [row for row in table.rows if row.player == "kodik"]
    assert {row.voice.name for row in kodik_rows} == {"Animy", "AniStar"}
    assert len({row.key for row in table.rows}) == len(table.rows) == 2 + 5 + 2
    # The widget is one playlist however many voice-overs the site lists under it.
    assert web.count("/player/sv/playlist") == 1
    asked = dict(parse_qsl(urlsplit(next(url for _m, url, _b in web.asked if "index.php" in url)).query))
    assert asked == {"controller": "ajax", "mod": "animesss_playlist", "news_id": "1171", "action": "load_player"}
    assert table.names[0] == "Противостоящий небесам"
    # A source no player knows is not read, and the log says which.
    assert any("'sibnet'" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("text, playlist", [(page(None), PLAYLIST), (page(), "<div>Плеер недоступен</div>")])
async def test_a_page_with_no_number_or_a_playlist_with_no_source_is_refused(text, playlist):
    with pytest.raises(SiteError) as refused:
        await read(site_web(text, playlist))

    assert parse(refused.value.reason)["code"] == "no_players_empty"
