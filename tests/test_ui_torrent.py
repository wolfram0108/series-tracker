"""A torrent source in the browser, the whole application on the stand (tests/ui_stand.py) with the model of qBittorrent and a
page of anilibria (tests/app_stand.py, Tracker), as the owner checks it: the series is there; the user adds the page as its
source in the window of the series and sees the tab «Торрент» come in that window; downloads the release and follows it in
«Агенты»; sees on the tab the files of the release, all of them without an episode since the source has no rule; writes one
rule in the tab «Файлы» and sees every episode placed. Everything the user sees comes by the events of the service."""
from __future__ import annotations

import re

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
    # Downloaded, the release stays in the client and in the monitor: ready, every file there.
    await expect(card).to_have_attribute("data-state", "done", timeout=LONG_S)
    await ui.shot("02b-agents-done")

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

    # «Озв. конт.»: the sound inside the files has no title and no language in them, so it waits for the operator; one rule
    # puts it into a folder of a voice-over and gives it its language, and nothing waits any more.
    await page.locator(".torrent-switch .tab-voice_streams").click()
    sounds = page.locator(".torrent-streams .stream-row.is-unplaced")
    await expect(sounds).to_have_count(1)  # one container: the sound of both files
    # The row is the raw line of the stream, as the rule reads it: the kind, the number, the codec; no language in the file.
    line = sounds.first.locator(".torrent-line")
    await expect(line).to_have_text(re.compile(r"^T:звук \| A:\d+ \| C:aac$"))
    await ui._rule({"name": "звук", "if": [{"type": "text", "value": "T:звук"}],
                    "then": [("assign_voice_folder", "Студия"), ("assign_language", "rus")]})
    await expect(page.locator(".torrent-streams .stream-row.is-unplaced")).to_have_count(0, timeout=LONG_S)
    folder = page.locator(".torrent-folder[data-folder='Студия']")
    await expect(folder).to_be_visible()
    # Where it comes from: the number of the rule in the list of the tab (its name in the title), in its colour.
    await expect(folder.locator(".number-mark")).to_have_text("1")
    await expect(folder.locator(".number-mark")).to_have_attribute("title", re.compile("звук"))
    # The folder is a row of the table; unfolded, it lists the track of each episode it holds: the episode and the stream as
    # the left pane names it, and the rule that placed them.
    await folder.locator(".torrent-folders-chev").click()
    tracks = page.locator(".torrent-folder-track")
    await expect(tracks.locator(".torrent-folders-place")).to_have_text(["s01e01", "s01e02"])
    await expect(tracks.locator(".torrent-folders-id")).to_have_text([re.compile(r"^A:\d+$")] * 2)
    await expect(tracks.locator(".number-mark")).to_have_text(["1", "1"])
    await ui.shot("05-tracks-by-rule")

    # What is shown is what is copied: a click takes a field whole, code and all; the whole line, as the rule reads it.
    row = page.locator(".torrent-streams .stream-row").nth(1)
    field = row.locator(".torrent-line-field").last
    await field.click()
    assert await page.evaluate("getSelection().toString()") == await field.inner_text()
    assert (await field.inner_text()).startswith("C:")
    whole = await row.locator(".torrent-line").evaluate(
        "line => { const range = document.createRange(); range.selectNodeContents(line); const pick = getSelection();"
        " pick.removeAllRanges(); pick.addRange(range); return [pick.toString(), line.title] }")
    assert whole[0] == whole[1], whole

    # A sound picked on «Озв. конт.»: each folder of a voice-over is a place to put it (the tab shows no other); the columns
    # of the folders stay where they were.
    heads = page.locator(".torrent-folders-table th")
    columns = [await each.bounding_box() for each in await heads.all()]
    await row.locator("input[type=checkbox]").check()
    await expect(page.locator(".torrent-folder .drop-folder")).to_have_count(1)
    await ui.shot("06-pick")
    assert [await each.bounding_box() for each in await heads.all()] == columns
    await page.locator(".torrent-pick button", has_text=text("torrent.select.clear")).click()
    await expect(page.locator(".torrent-folder .drop-folder")).to_have_count(0)

    # A sound put by hand in a folder the user made: the folder says it comes from a hand, and the bin of the track, unfolded,
    # gives it back to the rules — it goes back to the folder of the rule; the bin of the empty folder forgets it.
    await page.locator(".torrent-pane-tools button", has_text=text("torrent.folder.addVoice")).click()
    await page.locator(".torrent-new input").fill("Рука")
    await page.locator(".torrent-new button[type=submit]").click()
    hand = page.locator(".torrent-folder[data-folder='Рука']")
    await expect(hand.locator(".torrent-hand")).to_have_text(text("torrent.by.manual"))
    await row.locator("input[type=checkbox]").check()
    await hand.locator(".drop-folder").click()
    await expect(hand.locator(".torrent-folders-num").last).to_have_text("2", timeout=LONG_S)
    await expect(page.locator(".torrent-folder[data-folder='Студия']")).to_have_count(0)
    await hand.locator(".torrent-folders-chev").click()
    back = page.locator(".torrent-folder-track .torrent-hand")
    await expect(back).to_have_count(2)
    await ui.shot("07-by-hand")
    # «−» takes a track out of the folder — red, the hand's placement gone; «Вернуть правилам» on the left is not needed.
    for left in (1, 0):
        await page.locator(".torrent-folder-track", has=page.locator(".torrent-hand")).first.locator(".do-drop-track").click()
        await expect(back).to_have_count(left, timeout=LONG_S)
    await expect(page.locator(".torrent-streams .stream-row.is-unplaced")).to_have_count(1, timeout=LONG_S)
    await hand.locator(".do-delete-folder").click()
    await expect(hand).to_have_count(0, timeout=LONG_S)
    # «Авто» gives the sound back to the automatic layout: the rule puts it into «Студия» again.
    await page.locator(".do-auto").click()
    await expect(page.locator(".torrent-folder[data-folder='Студия'] .torrent-folders-num").last).to_have_text("2", timeout=LONG_S)
    # Nothing of the automatic layout is taken away now: «Авто» has nothing to make again.
    await expect(page.locator(".do-auto")).to_be_disabled()
    # A folder of a rule deleted: its tracks are red; «Авто» makes it again.
    await page.locator(".torrent-folder[data-folder='Студия'] .do-delete-folder").click()
    await expect(page.locator(".torrent-streams .stream-row.is-unplaced")).to_have_count(1, timeout=LONG_S)
    await page.locator(".do-auto").click()
    await expect(page.locator(".torrent-folder[data-folder='Студия']")).to_have_count(1, timeout=LONG_S)
    await expect(page.locator(".torrent-streams .stream-row.is-unplaced")).to_have_count(0)

    # Nothing jitters: the switch of the tabs, the heads of the panes and the panes keep their place and size whatever tab
    # or filter is chosen; the names of the files are text the user selects and copies.
    await page.locator(".torrent-rules-head").click()
    frames = [".torrent-switch", ".torrent-pane >> nth=0", ".torrent-pane >> nth=1", ".torrent-pane-head >> nth=0",
              ".torrent-pane-head >> nth=1"]

    async def boxes():
        return [await page.locator(frame).first.bounding_box() for frame in frames]
    first = await boxes()
    for tab in ("voices", "subtitles", "voice_streams", "subtitle_streams", "files"):
        await page.locator(f".torrent-switch .tab-{tab}").click()
        await expect(page.locator(f".torrent-switch .tab-{tab}")).to_have_class(re.compile(r"\bactive\b"))
        assert await boxes() == first, tab
        filters = page.locator(".torrent-filters .torrent-filter")
        for index in range(await filters.count()):
            await filters.nth(index).click()
            await expect(filters.nth(index)).to_have_class(re.compile(r"\bactive\b"))
            assert await boxes() == first, (tab, index)
    await page.locator(".torrent-filters .torrent-filter").first.click()  # «Все»: the files are listed again
    name = page.locator(".torrent-files .file-row .torrent-line-field.is-name").first
    await name.click()
    assert await page.evaluate("getSelection().toString()") == await name.inner_text()
    # A drag across a part of the name takes that part alone, not the whole field (a drag that starts in a selection
    # moves it, as in any page: the user drags over a name not selected yet).
    await page.evaluate("getSelection().removeAllRanges()")
    box = await name.bounding_box()
    await page.mouse.move(box["x"] + 2, box["y"] + box["height"] / 2)
    await page.mouse.down()
    await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=5)
    await page.mouse.up()
    part = await page.evaluate("getSelection().toString()")
    assert part and len(part) < len(await name.inner_text()) and (await name.inner_text()).startswith(part), part
