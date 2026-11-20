"""The players CDN VideoHub and Kodik: what they find on a page, the lists they give and the files of one item.

The shapes of the answers are those of the snapshots (`test_sources_dunhuatv_site` reads the snapshots whole); here the lists
are small, so that each rule of the players is seen on its own: the item has no number of the player's making, a season the
player does not give is not made up, a voice-over an address limits the iframe to is the only one read.
"""
import base64
import json

import pytest

from core.reasons import parse
from modules.sources.players import kodik
from modules.sources.players.catalog import CatalogError, read_catalog
from modules.sources.players.cdnvideohub import CdnVideoHubPlayer, Rung
from modules.sources.players.contract import PlayerRef
from sources_support import STEP_LIMIT_S, Web, within

PAGE = "https://dunhuatv.ru/28-x-smotret-onlajn.html"
WIDGET = '<video-player id="myplayer" data-title-id="54437" data-publisher-id="1110"></video-player>'
VIDEO_SOURCES = {
    "v1": {"mpegLowUrl": "https://okcdn.test/v1/360", "mpegHighUrl": "https://okcdn.test/v1/720"},
    "v4k": {"mpegHighUrl": "https://okcdn.test/v4k/720", "mpeg4kUrl": "https://okcdn.test/v4k/4k"},
}


async def measured(rung: Rung) -> int | None:
    return {"mpeg4kUrl": 1440}.get(rung.key)


def signed(domain: str = "dunhuatv.ru") -> str:
    return (f'<script>var serialId = Number(50228); var domain = "{domain}"; var d_sign = "ds"; var pd = "kodikplayer.com";'
            ' var pd_sign = "ps"; var ref = "https://dunhuatv.ru/"; var ref_sign = "rs";</script>')


def voices(*pairs: tuple[str, str, str, str]) -> str:
    options = "".join(f'<option value="{tid}" data-id="{tid}" data-translation-type="voice" data-media-id="{mid}" '
                      f'data-media-hash="{mhash}" data-title="{title}">{title}</option>' for tid, mid, mhash, title in pairs)
    return f'<div class="serial-translations-box"><select>{options}</select></div>'


def seasons(*triples: tuple[str, str, str, bool]) -> str:
    options = "".join(f'<option value="{index}" data-serial-id="{mid}" data-serial-hash="{mhash}" data-title="{title}"'
                      f'{" selected=\"selected\"" if chosen else ""}>{title}</option>'
                      for index, (title, mid, mhash, chosen) in enumerate(triples, 1))
    return f'<div class="serial-seasons-box"><select>{options}</select></div>'


def items(*triples: tuple[str, str, str, str]) -> str:
    """Items of a list: (id, value, text, mark); the mark "other:<voice-over>" is the video of that voice-over, which Kodik
    shows in this list for an episode this voice-over lacks."""
    def option(item: str, value: str, text: str, mark: str) -> str:
        other = mark.startswith("other:")
        owner = f' data-translation-title="{mark.removeprefix("other:")}"' if other else ""
        return (f'<option value="{value}" data-id="{item}" data-hash="h{item}" data-title="{text}"'
                f' data-other-translation="{"true" if other else "false"}"{owner}>{text}</option>')
    options = "".join(option(*each) for each in triples)
    return f'<div class="serial-series-box"><select>{options}</select></div>'


ANIMY = ("1043", "50228", "7bbb4c6b", "Animy")
ANISTAR = ("910", "49002", "b1b36deb", "AniStar")
SEASON_1, SEASON_2 = ("1 сезон", "50228", "7bbb4c6b", True), ("2 сезон", "50229", "7bbb4c6c", False)
IFRAMES = {
    "50228": signed() + voices(ANIMY, ANISTAR) + seasons(SEASON_1, SEASON_2) + items(
        ("101", "1", "1 серия", ""), ("102", "3", "2-3 серия", ""), ("103", "4", "4 серия", "")),
    "50229": signed() + voices(ANIMY, ANISTAR) + seasons(("1 сезон", "50228", "7bbb4c6b", False),
                                                          ("2 сезон", "50229", "7bbb4c6c", True))
    + items(("201", "1", "1 серия", ""), ("202", "2", "2 серия", "")),
    "49002": signed() + voices(ANIMY, ANISTAR) + seasons(("1 сезон", "49002", "b1b36deb", True)) + items(
        ("301", "1", "1 серия", ""), ("302", "2", "2 серия", "")),
}


def obfuscate(url: str) -> str:
    """Inverse of Kodik's cipher: base64, then every letter shifted back by 18."""
    plain = base64.b64encode(url.encode()).decode()

    def shift(char: str) -> str:
        if not char.isalpha():
            return char
        first = ord("A") if char <= "Z" else ord("a")
        return chr(first + (ord(char) - first - 18) % 26)

    return "".join(shift(char) for char in plain)


def ftor_links(*heights: int) -> dict:
    return {"links": {str(h): [{"src": obfuscate(f"//cloud.kodik.test/{h}.mp4:hls:manifest.m3u8"),
                                "type": "application/x-mpegURL"}] for h in heights}}


async def ask(player, ref, http):
    """What the player reads of one ask; the ask has a limit like every wait of these tests."""
    return player.read(await within(player.fetch(ref, http, PAGE), STEP_LIMIT_S, "the ask of the player"))


# --- Kodik: the list of an iframe ---------------------------------------------------------------------------------------------

def season_page(*triples: tuple[str, str, str, str]) -> str:
    """The iframe of one season of one voice-over, as a site embeds it: no list of voices, the page names the one it plays."""
    return (signed() + '<script>var seasonId = Number(64820); var translationId = 910; var translationTitle = "AniStar";'
            "</script>" + items(*triples))


def seria_page(translation_id: str, title: str) -> str:
    """The page of one video of Kodik: it names the voice-over the video is of."""
    return f'<script>var translationId = {translation_id}; var translationTitle = "{title}";</script>'


async def test_a_list_that_shows_another_voice_overs_video_reads_it_with_that_voice_and_a_video_whose_page_names_none_is_unread():
    page = season_page(("501", "1", "1 серия", ""), ("777", "2", "2 серия (Другой перевод: Animy)", "other:Animy"))
    source = PlayerRef("kodik", "https://kodikplayer.com/season/64820/8cc4/720p?translations=false&episode=1")
    player, web = kodik.KodikPlayer(), Web()
    web.route("kodikplayer.com/season/64820/", page)
    web.route("kodikplayer.com/seria/777/", seria_page("1043", "Animy"))
    async with web.client() as http:
        found = await within(read_catalog(player, source, http, PAGE), STEP_LIMIT_S, "the catalog of the season")
    assert [(e.key, e.voice.name, e.borrowed) for e in found] == [("910:501", "AniStar", False), ("1043:777", "Animy", True)]

    silent = Web()
    silent.route("kodikplayer.com/season/64820/", page)
    silent.route("kodikplayer.com/seria/777/", "<html></html>")
    async with silent.client() as http:
        with pytest.raises(CatalogError) as refused:
            await within(read_catalog(kodik.KodikPlayer(), source, http, PAGE), STEP_LIMIT_S, "the catalog of the season")
    assert parse(refused.value.reason)["code"] == "kodik_owner_unread"


# --- Kodik: one item ----------------------------------------------------------------------------------------------------------

# --- CDN VideoHub ---------------------------------------------------------------------------------------------------------------

def playlist_web(playlist: dict | int) -> Web:
    web = Web()
    web.route("/player/sv/playlist", (playlist, "") if isinstance(playlist, int) else json.dumps(playlist))
    web.route("/player/sv/video/", lambda request: (json.dumps({"sources": VIDEO_SOURCES[str(request.url).rsplit("/", 1)[1]],
                                                                 "duration": 600})
                                                    if str(request.url).rsplit("/", 1)[1] in VIDEO_SOURCES else (404, "")))
    return web


async def cvh_entries(playlist: dict | int):
    player, web = CdnVideoHubPlayer(measured), playlist_web(playlist)
    ref = player.find(WIDGET, PAGE)
    async with web.client() as http:
        return await within(read_catalog(player, ref, http, PAGE), STEP_LIMIT_S, "the playlist")


async def test_an_empty_playlist_is_no_entries_and_a_playlist_that_is_down_is_a_catalog_that_cannot_be_read():
    assert await cvh_entries(204) == []
    with pytest.raises(CatalogError) as refused:
        await cvh_entries(503)

    assert refused.value.transient and parse(refused.value.reason)["code"] == "player_status"


# One video as the CDN gave it: the label "4k" was measured at 1440 and the label "2k" at 2160.
