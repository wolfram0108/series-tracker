"""A read told to take the recent episodes asks a site for a few of them, not for hundreds (ReadContext.recent)."""
import pytest

from modules.sources.players.registry import PlayerRegistry
from modules.sources.sites import ReadContext
from modules.sources.sites.recent import last_of_each
from modules.sources.sites.ruchime import RuchimeSite
from sources_support import READ_LIMIT_S, Web, json_of, within
from modules.sources.sites.vk_channel import VkChannelSite
from test_sources_dunhuatv_site import PAGE, a_site, web_of as dunhuatv_web
from test_sources_ruchime_site import RELEASE, SLUG, catalog_of, web_of
from test_sources_vk_channel import CHANNEL, SEARCH, Api


@pytest.mark.parametrize("items,n,expected", [
    ([1, 2, 3, 4, 5], 2, [4, 5]),
    ([1, 2, 3], 5, [1, 2, 3]),
    ([1, 2, 3], None, [1, 2, 3]),
    ([], 3, []),
    ([1, 2, 3], 0, []),
])
def test_the_last_of_one_group_keeps_the_order(items, n, expected):
    assert last_of_each(items, lambda _item: None, n) == expected


def test_the_last_of_each_voice_is_kept_in_the_order_the_site_listed_them():
    rows = [("a", 1), ("b", 1), ("a", 2), ("b", 2), ("a", 3), ("b", 3), ("a", 4)]
    assert last_of_each(rows, lambda row: row[0], 2) == [("b", 2), ("a", 3), ("b", 3), ("a", 4)]


async def test_a_read_of_a_release_of_hundreds_of_episodes_asks_the_pages_of_the_last_ten_only():
    site, web = RuchimeSite(PlayerRegistry.standard()), web_of()
    catalog_of(site, SLUG)
    async with web.client() as http:
        draft = await within(site.read(RELEASE, ReadContext(http, lambda *step: None, recent=10)), READ_LIMIT_S,
                             "the read of the last ten episodes")
    listed = json_of("run1/ruchime__ALL_EPS.json")
    last = [item["number"] for item in listed][-10:]
    assert sorted({row.site_fields.get("number") for row in draft.rows if row.player == "vk"}) == sorted(last)
    assert web.count(f"/release/{SLUG}/episode-") == 10  # the whole read asks 390
    assert len(web.asked) < 200  # the whole read asks 1532


async def test_a_read_of_a_title_with_hundreds_of_cvh_videos_asks_the_last_ten_of_each_voice_only():
    web = dunhuatv_web()
    async with web.client() as http:
        draft = await within(a_site().read(PAGE, ReadContext(http, lambda *step: None, recent=10)), READ_LIMIT_S,
                             "the read of the last ten episodes of each voice-over")
    voices = {}
    for row in draft.rows:
        voices.setdefault((row.player, row.voice.name, row.voice.kind, row.group), []).append(row)
    assert voices and all(len(rows) <= 10 for rows in voices.values())
    assert web.count("/player/sv/video/") <= 10 * len({key for key in voices if key[0] == "cvh"})  # the whole read asks 607


async def test_a_read_of_a_channel_takes_the_newest_videos_by_their_dates():
    api, listed = Api(), json_of(SEARCH)["response"]["items"]
    async with Web().client() as http:
        draft = await within(VkChannelSite(PlayerRegistry.standard(), api).read(
            f"{CHANNEL}|Непревзойденный на пути алхимии|search", ReadContext(http, lambda *step: None, recent=3)),
            READ_LIMIT_S, "the read of the newest videos")
    ours = sorted((video for video in listed if video["owner_id"] == -170515668), key=lambda video: video.get("date") or 0)
    assert [row.key for row in draft.rows] == [f"{video['owner_id']}_{video['id']}" for video in ours[-3:]]
