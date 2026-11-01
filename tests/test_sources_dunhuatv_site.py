"""The site dunhuatv.ru read into the table of the page (M-8), on the snapshots of the real answers in `raw/`.

The title page, the playlist of CDN VideoHub (607 items), the iframes of the three voice-overs of Kodik and the answers of
the two players about a video are the snapshots. The videos of the playlist that the snapshots do not cover answer as the
snapshot of one of them does, so that every item of the playlist has an answer.
"""
import json
import re
from collections import Counter

import httpx
import pytest

from core.contracts import RowState
from core.reasons import parse
from modules.sources.players.cdnvideohub import Rung
from modules.sources.players.registry import PlayerRegistry
from modules.sources.sites import (PHASE_CATALOG, PHASE_PLAYERS, PHASE_RELEASE, ReadContext, SiteError, SiteRegistry)
from modules.sources.sites.dunhuatv import DunhuaTvSite
from sources_support import Web, json_of, text_of

PAGE = "https://dunhuatv.ru/28-praktikuja-ci-sto-tysjach-let-smotret-onlajn.html"
KODIK_IFRAMES = {"50228": "run1/dunhuatv__kodik_voice0.html", "49002": "run1/dunhuatv__kodik_voice1.html",
                 "67748": "run1/dunhuatv__kodik_voice2.html"}
CVH_VIDEOS = {"14244459338480": "run1/dunhuatv__cvh_video__14244459338480.json",
              "8026395597487": "run1/dunhuatv__cvh_video__8026395597487.json",
              "9902733417042": "run1/dunhuatv__cvh_video__9902733417042.json"}
UNSTABLE = re.compile(r"expires=|sign=|token|\.mp4|\.m3u8|mp4_|okcdn|api_hash")


async def measured(rung: Rung) -> int | None:
    """The frame height the unlabelled rungs of the snapshots would be measured at."""
    return {"mpegQhdUrl": 1440, "mpeg2kUrl": 1440, "mpeg4kUrl": 2160}.get(rung.key)


def cvh_video(request: httpx.Request) -> str:
    return text_of(CVH_VIDEOS.get(str(request.url).rsplit("/", 1)[1], CVH_VIDEOS["9902733417042"]))


def kodik_iframe(request: httpx.Request) -> str | tuple[int, str]:
    name = KODIK_IFRAMES.get(str(request.url).split("/serial/")[1].split("/")[0])
    return text_of(name) if name else (404, "")


def web_of(page: str | None = None) -> Web:
    web = Web()
    web.route("dunhuatv.ru/28-", page if page is not None else text_of("run1/dunhuatv__title.html"))
    web.route("/player/sv/playlist", text_of("run1/dunhuatv__cvh_playlist.json"))
    web.route("/player/sv/video/", cvh_video)
    web.route("kodikplayer.com/serial/", kodik_iframe)
    web.route("kodikplayer.com/ftor", text_of("run1/dunhuatv__kodik_ftor.json"), method="POST")
    return web


def a_site() -> DunhuaTvSite:
    return DunhuaTvSite(PlayerRegistry.standard(measure=measured))


class Run:
    def __init__(self) -> None:
        self.progress: list[tuple[str, int, int]] = []

    async def read(self, site: DunhuaTvSite, web: Web, url: str = PAGE):
        async with web.client() as http:
            return await site.read(url, ReadContext(http, lambda *step: self.progress.append(step)))


_READ: list = []


@pytest.fixture
async def whole():
    """The one read of the whole title that the tests below look at from different sides."""
    if not _READ:
        run, web = Run(), web_of()
        _READ.append((await run.read(a_site(), web), run.progress, web))
    return _READ[0]


def players(draft, name: str):
    return [row for row in draft.rows if row.player == name]


# --- the whole title: every player, every voice-over, every item (M-8) -------------------------------------------------------

async def test_the_head_of_the_table_has_the_names_of_the_page_and_no_voice(whole):
    draft, *_ = whole

    assert draft.names == ["Практикуя ци сто тысяч лет", "Lian Qi Shi Wan Nian"] and draft.site_voice == ""


async def test_the_table_has_every_item_of_cdn_videohub_and_of_the_three_voices_of_kodik(whole):
    draft, *_ = whole

    assert len(players(draft, "cvh")) == len(json_of("run1/dunhuatv__cvh_playlist.json")["items"]) == 607
    assert Counter(row.voice.name for row in players(draft, "kodik")) == {
        "Animy": 362, "AniStar": 287, "Студия Константина Исаева": 17}
    assert len({row.key for row in draft.rows}) == len(draft.rows) == 607 + 666


async def test_a_cvh_row_is_an_item_of_the_playlist_with_the_voice_the_player_names_and_the_season_it_gives(whole):
    draft, *_ = whole
    cvh = players(draft, "cvh")
    first = json_of("run1/dunhuatv__cvh_playlist.json")["items"][0]
    row = cvh[0]

    assert row.key == f"cvh:{first['cvhId']}" and row.link.endswith(f"#cvh={first['vkId']}")
    assert (row.voice.name, row.voice.kind, row.group, row.text) == ("AniMy / RuChiMe", "Многоголосый", "1 сезон", "1 серия")
    assert row.player_label == "CVH" and row.site_fields["season"] == 1 and row.site_fields["episode"] == "1"
    # The studio is the name of the voice-over; the same studio with another type is another voice-over; no studio is none.
    assert Counter((row.voice.name, row.voice.kind) for row in cvh) == {
        ("AniMy / RuChiMe", "Многоголосый"): 330, ("AniStar", "Многоголосый"): 238,
        ("AniStar", "Неизвестный"): 27, ("", "Многоголосый"): 12}


async def test_a_cvh_row_that_skips_a_number_of_a_compilation_is_what_the_player_gives_and_nothing_is_guessed(whole):
    draft, *_ = whole
    animy = [row.text for row in players(draft, "cvh") if row.voice.name == "AniMy / RuChiMe"]

    # The player gives no item "109" and one "110" (the file that holds both): the table shows exactly that.
    assert "109 серия" not in animy and "110 серия" in animy and "108 серия" in animy


async def test_a_kodik_row_is_an_item_of_the_list_with_its_text_verbatim_and_no_number_of_the_value(whole):
    draft, *_ = whole
    animy = [row for row in players(draft, "kodik") if row.voice.name == "Animy"]
    compilation = next(row for row in animy if row.text == "109-110 серия")
    isaev = [row for row in players(draft, "kodik") if row.voice.name == "Студия Константина Исаева"]

    assert animy[0].key.startswith("kodik:1043:") and (animy[0].text, animy[0].group) == ("1 серия", "1 сезон")
    assert (animy[0].voice.kind, animy[0].player_label) == ("voice", "Kodik")
    # The value of the item is the last number of a compilation: a note on the row, and the text keeps the range.
    assert compilation.site_fields["value"] == "110" and compilation.site_fields["translation_id"] == "1043"
    assert [row.text for row in isaev[:3]] == ["1-3 серия", "4-6 серия", "7-9 серия"]
    assert sum(1 for row in animy if "-" in row.text) == 26


async def test_the_heights_of_a_row_are_the_steps_the_player_gives(whole):
    draft, *_ = whole
    with_full_hd = next(row for row in players(draft, "cvh") if row.link.endswith("#cvh=14244459338480"))

    # The snapshots of the videos list rungs up to 720p, and one of them up to 1080p; none has a rung above it.
    assert {tuple(row.heights) for row in players(draft, "cvh")} == {(144, 240, 360, 480, 720), (144, 240, 360, 480, 720, 1080)}
    assert with_full_hd.heights == [144, 240, 360, 480, 720, 1080]
    assert {tuple(row.heights) for row in players(draft, "kodik")} == {(360, 480, 720)}
    assert {row.state for row in draft.rows} == {RowState.PLAYABLE}


async def test_a_link_is_the_stable_address_of_the_item_and_never_a_signed_stream_or_a_token(whole):
    draft, *_ = whole

    assert not [row.link for row in draft.rows if UNSTABLE.search(row.link) or not row.link]
    for row in draft.rows:
        assert re.fullmatch(re.escape(PAGE) + (r"#cvh=\d+" if row.player == "cvh" else r"#kodik=\d+/[0-9a-f]+/\d+"), row.link)


async def test_two_reads_of_the_same_answers_give_the_same_link_in_every_row(whole):
    first, *_ = whole

    second = await Run().read(a_site(), web_of())

    assert [(row.key, row.link) for row in second.rows] == [(row.key, row.link) for row in first.rows]


async def test_the_read_reports_the_page_the_catalogs_and_the_asks_in_that_order(whole):
    _draft, progress, _web = whole

    assert list(dict.fromkeys(phase for phase, _current, _total in progress)) == [PHASE_RELEASE, PHASE_CATALOG, PHASE_PLAYERS]
    assert (PHASE_CATALOG, 2, 2) in progress and progress[-1] == (PHASE_PLAYERS, 1273, 1273)


# --- what the players do not give is not made up ------------------------------------------------------------------------------

async def test_an_item_without_a_season_has_no_group_and_nothing_puts_the_first_season_in_its_place():
    playlist = {"items": [{"cvhId": "c1", "vkId": "v1", "voiceStudio": "A", "voiceType": "x", "episode": 3},
                          {"cvhId": "c2", "vkId": "v2", "voiceStudio": "A", "voiceType": "x", "season": 2, "episode": 4},
                          {"cvhId": "c3", "vkId": "v3", "voiceStudio": "A", "voiceType": "x", "season": 2, "episode": 4.5},
                          {"cvhId": "c4", "vkId": "v4", "voiceStudio": "A", "voiceType": "x", "season": 2}]}
    web = web_of(text_of("run1/dunhuatv__title.html").replace("iframe", "x-frame"))
    web.route("/player/sv/playlist", json.dumps(playlist), first=True)

    draft = await Run().read(a_site(), web)

    assert [(row.group, row.text) for row in draft.rows] == [("", "3 серия"), ("2 сезон", "4 серия"),
                                                           ("2 сезон", "4.5 серия"), ("2 сезон", "")]


async def test_two_uploads_of_one_episode_are_two_rows_none_is_dropped():
    playlist = {"items": [{"cvhId": "old", "vkId": "v1", "voiceStudio": "A", "season": 1, "episode": 1},
                          {"cvhId": "new", "vkId": "v1b", "voiceStudio": "A", "season": 1, "episode": 1}]}
    web = web_of(text_of("run1/dunhuatv__title.html").replace("iframe", "x-frame"))
    web.route("/player/sv/playlist", json.dumps(playlist), first=True)

    draft = await Run().read(a_site(), web)

    assert [row.key for row in draft.rows] == ["cvh:old", "cvh:new"]


# --- the states of a row (M-6) --------------------------------------------------------------------------------------------------

async def test_a_video_the_player_does_not_have_is_absent_a_busy_host_is_not_obtained_and_a_failed_probe_is_not_obtained():
    items = json_of("run1/dunhuatv__cvh_playlist.json")["items"][:3]
    web = web_of(text_of("run1/dunhuatv__title.html").replace("iframe", "x-frame"))
    web.route("/player/sv/playlist", json.dumps({"items": items}), first=True)
    web.route(f"/video/{items[0]['vkId']}", (404, ""), first=True)
    web.route(f"/video/{items[1]['vkId']}", (503, "busy"), first=True)
    site = a_site()
    cvh = site.players.get("cvh")
    fetch = cvh.fetch

    async def fetch_or_break(ref, http, page_url):
        if ref.url.endswith(items[2]["vkId"]):
            raise RuntimeError("the markup changed")
        return await fetch(ref, http, page_url)

    cvh.fetch = fetch_or_break

    draft = await Run().read(site, web)

    assert [row.state for row in draft.rows] == [RowState.ABSENT, RowState.NOT_OBTAINED, RowState.NOT_OBTAINED]
    assert [parse(row.reason)["code"] for row in draft.rows] == ["player_video_missing", "player_status", "player_failed"]


async def test_an_item_kodik_has_no_answer_for_is_absent():
    web = web_of(text_of("run1/dunhuatv__title.html").replace("video-player", "x-player"))
    web.route("kodikplayer.com/ftor", (404, ""), first=True, method="POST")

    draft = await Run().read(a_site(), web)

    assert {row.state for row in draft.rows} == {RowState.ABSENT} and len(draft.rows) == 666


# --- a read that cannot give the whole title gives none of it ---------------------------------------------------------------------

@pytest.mark.parametrize("route, code", [
    (("dunhuatv.ru/28-", (404, "")), "site_status"),
    (("dunhuatv.ru/28-", "<h1>Пусто</h1>"), "no_players_empty"),
    (("/player/sv/playlist", (503, "")), "player_status"),
    (("kodikplayer.com/serial/", (500, "")), "player_status"),
    (("kodikplayer.com/serial/", "<html>no list</html>"), "kodik_no_episode_list"),
])
async def test_a_page_or_a_catalog_that_cannot_be_read_refuses_the_table_with_a_reason(route, code):
    web = web_of()
    web.route(route[0], route[1], first=True)

    with pytest.raises(SiteError) as refused:
        await Run().read(a_site(), web)

    assert parse(refused.value.reason)["code"] == code


async def test_a_host_that_does_not_answer_is_a_refusal_with_a_reason():
    def down(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(SiteError) as refused:
        await Run().read(a_site(), Web().route("dunhuatv.ru/28-", down))

    assert parse(refused.value.reason)["code"] == "site_unreachable"


# --- the address ---------------------------------------------------------------------------------------------------------------------

def test_only_the_title_pages_are_the_pages_of_the_site_and_the_address_is_the_page_alone():
    site = DunhuaTvSite()
    registry = SiteRegistry([site])

    assert site.canonical_url("https://www.dunhuatv.ru/28-x.html?player=cvh&voice=A#cvh=1", {"vibix_season": "2"}) == (
        "https://dunhuatv.ru/28-x.html")
    assert site.canonical_url(PAGE) == PAGE and site.canonical_url(site.canonical_url(PAGE)) == PAGE
    assert registry.find(PAGE + "#cvh=5") is site and registry.find("https://www.dunhuatv.ru/5-x.html") is site
    assert registry.find("https://dunhuatv.ru/page/2/") is None and registry.find("https://dunhuatv.ru/o-nas.html") is None
    assert registry.find("https://1.ruchime.org/release/x") is None
    with pytest.raises(ValueError):
        site.canonical_url("https://dunhuatv.ru/page/2/")
