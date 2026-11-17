"""A torrent source in the browser, the whole application on the stand (tests/ui_stand.py) with the model of qBittorrent and a
page of anilibria (tests/app_stand.py, Tracker): the user sets the folder of the releases in the settings of the sources,
makes the rule of the lines of a release in the constructor, adds the series with the page as its source, downloads the
release on the tab «Торрент» of the window of the series and sees it read and laid in seasons and episodes, then syncs the
series in «Выход». What the user sees comes by the events of the service; the library is checked on disk."""
from __future__ import annotations

import pytest
from playwright.async_api import expect

from ui_stand import SERIES, SERIES_EN, UiStand, state, text

pytestmark = [pytest.mark.ui, pytest.mark.asyncio]

VOICE = "AniLibria"
EPISODE = [{"type": "text", "value": "E"}, {"type": "number"}]
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


async def test_a_release_is_added_downloaded_read_and_synced_through_the_screens(ui):
    page = ui.page
    await ui.first_run()
    await ui.set_up([VOICE])

    await ui.settings(text("settings.tab.sources"))
    field = page.get_by_label(text("settings.sources.staging.label"))
    await field.fill(ui.staging)
    async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/torrent_staging")) as saved:
        await field.press("Enter")
    assert (await saved.value).ok
    await ui.close_settings()

    await ui.profile("серии раздачи", [{"name": "серия", "if": EPISODE,
                                         "then": [("extract_single", EPISODE), ("assign_voiceover", VOICE)]}])
    tracker = ui.torrent_page("show", "Сериал", {"Show - 01.mkv": 1, "Show - 02.mkv": 2})
    series_id = await ui.add_series(SERIES, SERIES_EN, [(tracker.url, "серии раздачи")])
    await ui.shot("01-added")

    await page.locator(".series-card", has_text=SERIES).first.locator("[title='Статус']").click()
    await ui.tab(text("status.tab.torrent"))
    head = page.locator(".torrent-head")
    await expect(head.locator(".torrent-state")).to_have_text(state("notLoaded"))
    await ui.shot("02-not-loaded")
    await head.locator(".do-download").click()
    await expect(head.locator(".torrent-state")).to_have_text(state("done"), timeout=LONG_S)
    await ui.shot("03-ready")

    # The preset of the torrents laid both files in season 1, episodes 1 and 2: the draft says so. The sound inside each
    # video lies in no folder of a voice-over: the two tracks wait for the operator on «Контейнеры» (the rule names them).
    await expect(head.locator(".torrent-switch .st-tab")).to_have_count(4)
    draft = page.locator(".torrent-files .torrent-season")
    await expect(draft).to_have_count(1)
    await expect(draft.locator(".episode-row")).to_have_count(2)
    await expect(head.locator(".torrent-waiting")).to_have_text(text("torrent.progress.waiting", n=2))
    await head.locator(".torrent-switch .tab-streams").click()
    await expect(page.locator(".torrent-streams .stream-row.is-unplaced")).to_have_count(1)  # one container: a0 of both files
    await ui.shot("04-containers")
    # At the width of a phone the tab keeps to the window: its panes stack, nothing runs off to the side.
    await page.set_viewport_size({"width": 390, "height": 900})
    await ui.shot("04b-narrow")
    tab = page.locator(".torrent-tab")
    assert await tab.evaluate("each => each.scrollWidth <= each.clientWidth"), "the tab runs off the window"
    await page.set_viewport_size({"width": 1440, "height": 1000})

    await ui.tab(text("output.tab"))
    await expect(page.locator(".episode-card")).to_have_count(2, timeout=LONG_S)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(2)
    await ui.shot("05-synced")
    view = await ui.check(series_id)
    assert sorted(each["episode"] for each in view["assets"]) == [[1, 1], [1, 2]]
