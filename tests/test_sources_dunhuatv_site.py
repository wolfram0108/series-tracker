"""The site dunhuatv.ru read into the table of the page (M-8), on synthetic answers in the shape of the real ones
(`site_samples`).

The title page, the playlist of CDN VideoHub (607 items), the iframes of the three voice-overs of Kodik and the answers of
the two players about a video are the samples. The videos of the playlist that the samples do not cover answer as the
sample of one of them does, so that every item of the playlist has an answer.
"""
import json
import re

import httpx
import pytest

from core.contracts import RowState
from core.reasons import parse
from modules.sources.players.cdnvideohub import Rung
from modules.sources.players.registry import PlayerRegistry
from modules.sources.sites import ReadContext
from modules.sources.sites.dunhuatv import DunhuaTvSite
from sources_support import READ_LIMIT_S, Web, json_of, text_of, within

PAGE = "https://dunhuatv.ru/28-praktikuja-ci-sto-tysjach-let-smotret-onlajn.html"
KODIK_IFRAMES = {"50228": "dunhuatv__kodik_voice0.html", "49002": "dunhuatv__kodik_voice1.html",
                 "67748": "dunhuatv__kodik_voice2.html"}
CVH_VIDEOS = {"14244459338480": "dunhuatv__cvh_video__14244459338480.json",
              "8026395597487": "dunhuatv__cvh_video__8026395597487.json",
              "9902733417042": "dunhuatv__cvh_video__9902733417042.json"}
UNSTABLE = re.compile(r"expires=|sign=|token|\.mp4|\.m3u8|mp4_|okcdn|api_hash")


async def measured(rung: Rung) -> int | None:
    """The frame height the unlabelled rungs of the samples would be measured at."""
    return {"mpegQhdUrl": 1440, "mpeg2kUrl": 1440, "mpeg4kUrl": 2160}.get(rung.key)


def cvh_video(request: httpx.Request) -> str:
    return text_of(CVH_VIDEOS.get(str(request.url).rsplit("/", 1)[1], CVH_VIDEOS["9902733417042"]))


def kodik_iframe(request: httpx.Request) -> str | tuple[int, str]:
    name = KODIK_IFRAMES.get(str(request.url).split("/serial/")[1].split("/")[0])
    return text_of(name) if name else (404, "")


def web_of(page: str | None = None) -> Web:
    web = Web()
    web.route("dunhuatv.ru/28-", page if page is not None else text_of("dunhuatv__title.html"))
    web.route("/player/sv/playlist", text_of("dunhuatv__cvh_playlist.json"))
    web.route("/player/sv/video/", cvh_video)
    web.route("kodikplayer.com/serial/", kodik_iframe)
    web.route("kodikplayer.com/ftor", text_of("dunhuatv__kodik_ftor.json"), method="POST")
    return web


def a_site() -> DunhuaTvSite:
    return DunhuaTvSite(PlayerRegistry.standard(measure=measured))


class Run:
    def __init__(self) -> None:
        self.progress: list[tuple[str, int, int]] = []

    async def read(self, site: DunhuaTvSite, web: Web, url: str = PAGE):
        async with web.client() as http:
            return await within(site.read(url, ReadContext(http, lambda *step: self.progress.append(step))),
                                READ_LIMIT_S, "the read of the title")


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

# --- what the players do not give is not made up ------------------------------------------------------------------------------

# --- the states of a row (M-6) --------------------------------------------------------------------------------------------------

async def test_a_video_the_player_does_not_have_is_absent_a_busy_host_is_not_obtained_and_a_failed_probe_is_not_obtained():
    items = json_of("dunhuatv__cvh_playlist.json")["items"][:3]
    web = web_of(text_of("dunhuatv__title.html").replace("iframe", "x-frame"))
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


# --- a read that cannot give the whole title gives none of it ---------------------------------------------------------------------

# --- the address ---------------------------------------------------------------------------------------------------------------------
