"""A torrent source in the browser, the whole application on the stand (tests/ui_stand.py) with the model of qBittorrent and a
page of anilibria (tests/app_stand.py, Tracker), as the owner checks it: the series is there; the user adds the page as its
source in the window of the series and sees the tab «Торрент» come in that window; downloads the release and follows it in
«Агенты»; sees on the tab the files of the release, all of them without an episode since the source has no rule; writes one
rule in the tab «Файлы» and sees every episode placed. Everything the user sees comes by the events of the service."""
from __future__ import annotations

import pytest
from playwright.async_api import expect

from ui_stand import SERIES, UiStand, state, text

pytestmark = [pytest.mark.ui, pytest.mark.asyncio]

EPISODE = [{"type": "text", "value": "-"}, {"type": "whitespace"}, {"type": "number"}]
LONG_S = 60_000  # the download, the read of the files by ffprobe and the read of the table: events of the service, not a pause


@pytest.fixture
async def ui(tmp_path):
    made = UiStand(str(tmp_path), qbit=True)
    await made.start()
    await made.open()
    yield made
    await made.shut()
    await made.stop()
    made.close()


async def test_a_release_added_to_a_series_is_downloaded_seen_in_the_agents_and_placed_by_one_rule(ui):
    page = ui.page
    ui.site.release("ref", "AniStar")
    ui.site.episode("ref", 1, height=720)
    await ui.make_series(["ref"])

    await ui.settings(text("settings.tab.sources"))
    field = page.get_by_label(text("settings.sources.staging.label"))
    await field.fill(ui.staging)
    async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/torrent_staging")) as saved:
        await field.press("Enter")
    assert (await saved.value).ok
    await ui.close_settings()

    tracker = ui.torrent_page("show", "Сериал", {"Show - 01.mkv": 1, "Show - 02.mkv": 2}, hold=True)
    # The series is open; the source is added in its properties, and the tab «Торрент» comes in this very window.
    await page.locator(".series-card", has_text=SERIES).first.locator("[title='Статус']").click()
    torrent_tab = page.locator(f".st-tab[title='{text('status.tab.torrent')}']")
    await expect(torrent_tab).to_have_count(0)
    await ui.tab("Свойства")
    cards = page.locator(".source-card")
    await expect(cards).to_have_count(1)
    await page.get_by_label(text("sources.add.label")).fill(tracker.url)
    await page.locator(".source-add button.btn-add", has_text=text("sources.add.button")).click()
    await expect(cards).to_have_count(2)
    await expect(torrent_tab).to_be_visible()
    await ui.shot("01-tab-came")

    await torrent_tab.click()
    head = page.locator(".torrent-head")
    await expect(head.locator(".torrent-state")).to_have_text(state("notLoaded"))
    await head.locator(".do-download").click()
    await expect(head.locator(".torrent-state")).to_have_text(state("download"), timeout=LONG_S)

    # «Агенты»: the release downloads, a card of this series in the monitor of qBittorrent.
    await ui.tab(text("status.tab.agents"))
    card = page.locator(".work-card[data-kind='monitor']")
    await expect(card).to_have_count(1, timeout=LONG_S)
    await expect(card).to_have_attribute("data-state", "download")
    await ui.shot("02-agents-download")
    ui.qbit.deliver(tracker.infohash)
    await expect(card).to_have_count(0, timeout=LONG_S)

    # The tab: the release is downloaded and read; its files have no episode, since the source has no rule yet.
    await torrent_tab.click()
    await expect(head.locator(".torrent-state")).to_have_text(state("done"), timeout=LONG_S)
    files = page.locator(".torrent-files .file-row")
    await expect(files).to_have_count(2)
    await expect(page.locator(".torrent-files .file-row.is-unplaced")).to_have_count(2)
    await expect(page.locator(".torrent-files .torrent-season")).to_have_count(0)
    await ui.shot("03-no-rule")

    # One rule in the tab «Файлы» places every episode.
    await ui._rule({"name": "серия", "if": EPISODE, "then": [("extract_single", EPISODE)]})
    await expect(page.locator(".torrent-files .file-row.is-unplaced")).to_have_count(0, timeout=LONG_S)
    season = page.locator(".torrent-files .torrent-season")
    await expect(season).to_have_count(1)
    await expect(season.locator(".torrent-episode-name")).to_have_text(["s01e01", "s01e02"])
    await ui.shot("04-one-rule")
