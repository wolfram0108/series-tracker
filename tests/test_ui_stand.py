"""The whole application in a browser (tests/ui_stand.py): the user's way through the screens on a stand whose site,
player and alignment service are models."""
import asyncio
import os

import pytest
from playwright.async_api import expect

from app_stand import STAND_LIMIT_S
from core import container as mkv
from plan_world import truth_of
from ui_stand import SERIES, state, text, ui  # noqa: F401 - the fixture

pytestmark = pytest.mark.ui


def failures_text(count: int, open: int | None = None) -> str:
    """The chip of the failed attempts: their number, and of them the ones no other source replaced (all, unless said)."""
    lacking = count if open is None else open
    return text("output.plan.failuresOpen", count=count, open=lacking) if lacking else text("output.plan.failures", count=count)


async def series_in_browser(ui, episodes=(1, 2)) -> int:
    """The site has a release of each voice-over; the user sets the service and the groups up, makes a profile for each
    source in the constructor and adds the series in its window."""
    ui.site.release("ref", "AniStar")
    ui.site.release("dub", "AnimeVost")
    for number in episodes:
        ui.site.episode("ref", number, height=720)
        ui.site.episode("dub", number, height=480)
    return await ui.make_series(["ref", "dub"])


async def test_a_series_made_in_the_browser_is_synced_by_its_button_and_its_files_are_shown(ui):
    series_id = await series_in_browser(ui)
    await ui.shot("01-added")
    await ui.open_output(SERIES)
    cards = ui.page.locator(".episode-card")
    await expect(cards).to_have_count(2)
    await ui.press_sync(series_id)
    await expect(ui.page.locator(".episode-card[data-state='done']")).to_have_count(2)
    await ui.shot("02-synced")
    view = await ui.check(series_id)
    assert len(view["assets"]) == 2
    await cards.first.dblclick()
    detail = ui.page.locator(".episode-detail")
    await detail.wait_for()
    await ui.shot("03a-opened")
    await expect(detail.locator(".detail-file")).to_have_text(view["assets"][0]["name"])
    row = detail.locator(".track-table tr[data-group='AnimeVost']")
    await expect(row.locator(".pill")).to_have_text(state("done"))
    await ui.shot("03-episode")


async def synced_in_browser(ui, episodes=(1,)) -> int:
    series_id = await series_in_browser(ui, episodes)
    await ui.open_output(SERIES)
    await ui.press_sync(series_id)
    await expect(ui.page.locator(".episode-card[data-state='done']")).to_have_count(len(episodes))
    return series_id


async def test_remake_and_download_again_wait_for_the_sync_and_can_be_taken_back(ui):
    """«Пересоздать» a voice-over and «Скачать заново» the video: each waits for the next sync in the bar of the section,
    «Отменить» takes it back; the sync does what it named and nothing else."""
    series_id = await synced_in_browser(ui)
    page = ui.page
    (asset,) = (await ui.call("GET", f"/api/series/{series_id}/plan"))["assets"]
    await page.locator(".episode-card").first.dblclick()
    detail = page.locator(".episode-detail")
    pending = page.locator(".plan-pending .plan-command")

    await detail.locator("tr[data-group='AnimeVost'] .row-action.do-redo").click()
    await expect(pending).to_have_count(1)
    await expect(pending).to_contain_text(text("output.plan.deleteTrack", voice="AnimeVost", name=asset["name"]))
    await ui.shot("10-remake-pending")
    await pending.get_by_role("button", name=text("output.plan.cancel")).click()
    await expect(pending).to_have_count(0)

    await detail.locator("tr[data-group='AnimeVost'] .row-action.do-redo").click()
    await expect(pending).to_have_count(1)
    aligned = len(ui.daemon.jobs)
    await ui.press_sync(series_id)
    await expect(pending).to_have_count(0)
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(1)
    assert len(ui.daemon.jobs) == aligned + 1  # the voice-over aligned anew, once
    await ui.check(series_id)

    await detail.locator(".row-action.do-refetch").click()
    await expect(pending).to_contain_text(text("output.plan.deleteVideo", name=asset["name"]))
    served = len(ui.host.served)
    await ui.press_sync(series_id)
    await expect(pending).to_have_count(0)
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(1)
    assert [name for name in ui.host.served[served:] if name.startswith("ref-ep1-")]  # the video, downloaded anew
    await ui.check(series_id)
    await ui.shot("11-downloaded-again")


async def test_a_download_that_failed_is_shown_and_tried_again_after_the_failures_are_reset(ui):
    """The file of the voice-over is gone from the site: the episode shows the error and the bar the count of failed
    attempts; a sync passes over it; «Сбросить неудачные попытки» and a sync, once the file is back, make it whole."""
    series_id = await series_in_browser(ui, (1,))
    ui.site.gone("dub", 1)
    page = ui.page
    await ui.open_output(SERIES)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='failed']")).to_have_count(1)
    await expect(page.locator(".plan-failures")).to_have_text(failures_text(1))
    await ui.shot_of(page.locator(".output-actions"), "33-failures-chip")
    await page.locator(".episode-card").first.dblclick()
    row = page.locator(".episode-detail tr[data-group='AnimeVost']")
    await expect(row.locator(".pill")).to_have_text(state("failed"))
    await ui.shot("20-download-failed")
    asked = len(ui.host.served)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='failed']")).to_have_count(1)
    assert not [name for name in ui.host.served[asked:] if name.startswith("dub-")]  # passed over, not asked again
    ui.site.gone("dub", 1, False)
    await page.locator(".output-retry-failed").click()
    await expect(page.locator(".plan-failures")).to_have_count(0)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(1)
    await expect(row.locator(".pill")).to_have_text(state("done"))
    await ui.check(series_id)


async def test_the_reset_of_an_episode_forgets_every_failed_attempt_of_it_and_only_of_it(ui):
    """The button in the head of an opened episode resets what failed for that episode — as the button of the series does
    for all of them; the failed attempts of the other episodes stay, and so does what the plan chose because of them."""
    series_id = await series_in_browser(ui, (1, 2))
    ui.site.gone("dub", 1)
    ui.site.gone("dub", 2)
    page = ui.page
    await ui.open_output(SERIES)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='failed']")).to_have_count(2)
    await expect(page.locator(".plan-failures")).to_have_text(failures_text(2))
    await page.locator(".episode-card[data-episode='1']").dblclick()
    button = page.locator(".episode-detail .do-retry-failed")
    await expect(button).to_have_text(text("output.retryFailed"))
    assert len(text("output.retryFailed").split()) == 1, "the button is one word"
    await expect(button).to_have_attribute("aria-label", text("output.episode.retryFailedHint"))
    # By the width: the word stays while the episode has room for it and is the icon alone when it has not — never wrapped
    # or cut — and the head does not overflow the panel at any width.
    label = button.locator(".p-button-label")
    for width in (1440, 1100, 900, 700, 520, 380):
        await page.set_viewport_size({"width": width, "height": 900})
        await ui.shot_of(page.locator(".episode-detail"), f"27-reset-of-an-episode-{width}")
        panel = await page.locator(".episode-detail").bounding_box()
        head = await page.locator(".episode-detail .detail-head").bounding_box()
        assert head["x"] >= panel["x"] - 1 and head["x"] + head["width"] <= panel["x"] + panel["width"] + 1, width
        if panel["width"] >= 56 * 16:
            await expect(label).to_be_visible()
        else:
            await expect(label).to_be_hidden()
            await expect(button).to_be_visible()
    await page.set_viewport_size({"width": 1440, "height": 1000})
    await button.click()
    await expect(page.locator(".plan-failures")).to_have_text(failures_text(1))
    await expect(page.locator(".episode-card[data-episode='2']")).to_have_attribute("data-state", "failed")
    ui.site.gone("dub", 1, False)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-episode='1']")).to_have_attribute("data-state", "done")
    await expect(page.locator(".episode-card[data-episode='2']")).to_have_attribute("data-state", "failed")
    await ui.check(series_id)


async def test_the_buttons_of_the_head_keep_their_words_while_the_tab_has_room_and_are_icons_when_it_has_not(ui):
    """The head of the output tab: its buttons are as wide as their words while the tab has room, and their icons alone when
    it has not — none wraps its word, cuts it or leaves the tab; the hints are their names. The reset of the series is one
    word, like the one of an episode."""
    await series_in_browser(ui, (1,))
    page = ui.page
    await ui.open_output(SERIES)
    row = page.locator(".output-actions")
    reset = row.locator(".output-retry-failed")
    await expect(reset).to_have_text(text("output.retryFailed"))
    assert len(text("output.retryFailed").split()) == 1, "the reset is one word"
    await expect(reset).to_have_attribute("aria-label", text("output.retryFailedHint"))
    for width in (1440, 1100, 900, 700, 520, 380):
        await page.set_viewport_size({"width": width, "height": 900})
        await ui.shot_of(row, f"28-head-buttons-{width}")
        room = (await page.locator(".status-output").bounding_box())["width"]
        box = await row.bounding_box()
        for button in await row.locator("button").all():
            where = await button.bounding_box()
            assert where["x"] >= box["x"] - 1 and where["x"] + where["width"] <= box["x"] + box["width"] + 1, width
        labels = row.locator(".p-button-label")
        for index in range(await labels.count()):
            if room >= 42 * 16:
                await expect(labels.nth(index)).to_be_visible()
            else:
                await expect(labels.nth(index)).to_be_hidden()
        for button in await row.locator("button").all():
            await expect(button).to_be_visible()
            assert await button.get_attribute("aria-label") or (await button.inner_text()).strip(), width
    # The head of a season is a place of its own: its three buttons keep their words from 66rem of the season and fold under it.
    season = page.locator(".season-block").first
    reset_season = season.locator(".do-retry-season")
    await expect(reset_season).to_have_attribute("aria-label", text("output.season.retryFailedHint"))
    for width in (1440, 1100, 900, 520):
        await page.set_viewport_size({"width": width, "height": 900})
        await ui.shot_of(season.locator(".season-head"), f"28-season-head-{width}")
        room = (await season.bounding_box())["width"]
        for label in await season.locator(".season-head .p-button-label").all():
            if room >= 66 * 16:
                await expect(label).to_be_visible()
            else:
                await expect(label).to_be_hidden()
        head = await season.locator(".season-head").bounding_box()
        tops = set()
        for button in await season.locator(".season-head button").all():
            where = await button.bounding_box()
            assert where["x"] + where["width"] <= head["x"] + head["width"] + 1, width
            tops.add(round(where["y"] / 10))
        if room >= 40 * 16:  # a head narrower than that wraps its buttons under the meters, as any row of the window does
            assert len(tops) == 1, f"the buttons of the head of a season are in more than one row at {width}: {tops}"
    # The page of the samples shows the same button in rooms of four widths: each folds at the tier it names.
    await page.set_viewport_size({"width": 1440, "height": 1000})
    await page.goto("/#gallery")
    await page.reload()  # the page of the samples is chosen by the anchor when the application starts
    demo = page.locator(".fold-demo")
    await expect(demo).to_have_count(5)
    await ui.shot_of(page.locator(".fold-demo").first, "29-gallery-fold-24")
    for index, rem in enumerate((24, 36, 48, 60, 72)):
        # Under 42rem every button folds; from 42rem the ones of the middle tier keep their words, the ones of the high tiers
        # fold until 56rem and 66rem; from 66rem all five keep them.
        shown = await demo.nth(index).locator(".p-button-label:visible").count()
        assert shown == {24: 0, 36: 0, 48: 3, 60: 4, 72: 5}[rem], (rem, shown)
    await ui.shot_of(page.locator("section", has=page.locator(".fold-demo")), "29-gallery-fold")
    # The head of a block in rooms of three widths: the long text goes to a second line, nothing goes under a button.
    heads = page.locator(".head-demo")
    await expect(heads).to_have_count(3)
    for index in range(3):
        said, does = await heads.nth(index).locator(".fold-said").bounding_box(), await heads.nth(index).locator(".fold-does").bounding_box()
        assert said["x"] + said["width"] <= does["x"] + 1 or does["y"] >= said["y"] + said["height"] - 1, index
    await ui.shot_of(page.locator("section", has=page.locator(".head-demo")), "29-gallery-head")
    # The reads of the sources, a row each, in rooms of two widths: nothing leaves its row, the speed is the hint.
    rooms = page.locator(".reads-demo")
    await expect(rooms).to_have_count(2)
    for index in range(2):
        await expect(rooms.nth(index).locator(".read")).to_have_count(3)
        assert await rooms.nth(index).locator(".reads").evaluate("e => e.scrollWidth <= e.clientWidth + 1"), index
        assert await rooms.nth(index).locator(".read").first.get_attribute("title") == text("output.reading.rate", rate="3.5")
    await ui.shot_of(page.locator("section", has=page.locator(".reads-demo")), "29-gallery-reads")


async def test_the_queues_of_an_episode_say_which_variant_its_file_holds_beside_the_one_the_order_takes_first(ui):
    """The order of a queue is a wish and the file is what is: after the sync the variant the file holds stands marked «в файле» in
    the queue of the reference and of each voice-over, whatever its place in the order."""
    series_id = await series_in_browser(ui, (1, 2))
    page = ui.page
    await ui.open_output(SERIES)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(2)
    await page.locator(".episode-card[data-episode='1']").dblclick()
    tabs = page.locator(".episode-detail .st-tabs .st-tab")
    held = text("output.queue.held")
    await tabs.nth(1).click()  # the reference
    await expect(page.locator(".episode-detail .queue-held")).to_have_count(1)
    await expect(page.locator(".episode-detail .queue-held").first).to_have_text(held)
    for index in range(3, await tabs.count()):  # a voice-over each
        await tabs.nth(index).click()
        await expect(page.locator(".episode-detail .queue-held")).to_have_count(1)
        await ui.shot_of(page.locator(".episode-detail"), f"32-held-{index}")


async def test_the_head_of_a_block_says_on_the_left_and_does_on_the_right_nothing_goes_under_a_button_and_no_button_wraps(ui):
    """The heads of a season and of an episode: what they say takes the room the buttons leave and goes to another line where
    it must; the buttons are one row at every width and never have the text under them."""
    series_id = await series_in_browser(ui, (1, 2))
    page = ui.page
    await page.add_style_tag(content=".p-toast { display: none !important; }")
    await ui.open_output(SERIES)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(2)
    await page.locator(".episode-card[data-episode='1']").dblclick()
    heads = {"season": page.locator(".season-block .season-head").first, "episode": page.locator(".episode-detail .detail-head")}
    # The words of the meters of a real season are long (every state of the tracks with its count): the same head with them.
    await heads["season"].locator(".meter-text").evaluate_all(
        "all => all.forEach((e) => { e.textContent = 'готово 1240 · в плане 37 · нет в источниках 3 · ошибок 2' })")
    await heads["episode"].locator(".detail-file").evaluate("e => { e.textContent = 'Ten Thousand Worlds s01e498 [Китайский] 1080p.mkv' }")
    for width in (1440, 1100, 1070, 1049, 900, 700, 520, 380):
        await page.set_viewport_size({"width": width, "height": 900})
        for name, head in heads.items():
            await ui.shot_of(head, f"31-{name}-head-{width}")
            said, does, box = (await head.locator(".fold-said").bounding_box(), await head.locator(".fold-does").bounding_box(),
                               await head.bounding_box())
            # The text is left of the buttons, or (a room too short for both) the buttons are under it: never the text under a button.
            assert said["x"] + said["width"] <= does["x"] + 1 or does["y"] >= said["y"] + said["height"] - 1, \
                f"the text of the {name} head goes under its buttons at {width}"
            assert does["x"] + does["width"] <= box["x"] + box["width"] + 1, f"the buttons of the {name} head leave it at {width}"
            centres = [(await each.bounding_box())["y"] + (await each.bounding_box())["height"] / 2
                       for each in await head.locator(".fold-does > *").all()]
            # One row while the room is 30rem (a phone): a narrower one may need a second line for all the icons.
            if box["width"] >= 30 * 16:
                assert max(centres) - min(centres) < 8, f"the buttons of the {name} head are in more than one row at {width}: {centres}"
            assert await head.locator(".fold-said").evaluate("e => e.scrollWidth <= e.clientWidth + 1"), \
                f"the text of the {name} head leaves its place at {width}"


async def test_the_buttons_of_the_header_of_the_application_keep_their_words_while_it_has_room_and_are_icons_when_it_has_not(ui):
    """The header of the application: add, the log and the settings are one word each while the header has room for them and
    their icons alone when it has not; the hints are their names, none leaves the header or loses its name."""
    await series_in_browser(ui, (1,))
    page = ui.page
    header = page.locator(".page-header")
    await page.add_style_tag(content=".p-toast { display: none !important; }")  # the notices of the setup must not cover the pictures
    names = {text("header.add"): text("header.addSeries"), text("header.log"): text("header.logs"),
             text("settings.title"): text("settings.title")}
    assert all(len(word.split()) == 1 for word in names), "the words of the buttons are one word each"
    for width in (1440, 940, 930, 700, 520, 380):
        await page.set_viewport_size({"width": width, "height": 900})
        await ui.shot_of(header, f"30-app-header-{width}")
        room = (await header.bounding_box())["width"]
        box = await header.bounding_box()
        for word, name in names.items():
            button = header.get_by_role("button", name=name)
            await expect(button).to_be_visible()
            await expect(button).to_have_attribute("aria-label", name)
            label = button.locator(".p-button-label")
            if room >= 56 * 16:
                await expect(label).to_have_text(word)
                await expect(label).to_be_visible()
            else:
                await expect(label).to_be_hidden()
            where = await button.bounding_box()
            assert where["x"] >= box["x"] - 1 and where["x"] + where["width"] <= box["x"] + box["width"] + 1, (width, word)
        # The row holds the title and the buttons: where the words are kept nothing wraps under the title.
        tops = {round((await button.bounding_box())["y"] / 10) for button in await header.locator(".header-actions button").all()}
        title = await header.locator(".page-title").bounding_box()
        if room >= 56 * 16:
            assert len(tops) == 1 and (await header.locator(".header-actions button").first.bounding_box())["y"] < title["y"] + title["height"], width


async def test_the_sync_of_an_episode_makes_that_episode_alone_the_series_is_busy_and_the_sync_of_the_series_makes_the_rest(ui):
    """«Синхронизировать» in the head of an opened episode is the pass of the series for that episode: its file is made and the
    files of the others are not; while it runs the button of every place is the one that stops it; the sync of the series then
    makes the rest, the file made before staying as it was."""
    series_id = await series_in_browser(ui, (1, 2))
    page = ui.page
    await ui.open_output(SERIES)
    await page.locator(".episode-card[data-episode='2']").dblclick()
    sync = page.locator(".episode-detail .episode-sync")
    await expect(sync).to_have_attribute("aria-label", text("output.episode.syncHint"))
    await expect(page.locator(".season-block .season-sync")).to_have_attribute("aria-label", text("output.season.syncHint"))
    await ui.shot("30-episode-sync")
    await ui.press(series_id, ".episode-detail .episode-sync")
    assert [name for name in ui.files() if "s01e02" in name] and not [name for name in ui.files() if "s01e01" in name], ui.files()
    await expect(page.locator(".episode-card[data-episode='2']")).to_have_attribute("data-state", "done")
    await expect(page.locator(".episode-card[data-episode='1']")).not_to_have_attribute("data-state", "done")
    made = ui.files()
    await ui.press_sync(series_id)
    assert len(ui.files()) == 2 and set(made) <= set(ui.files())
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(2)
    await ui.check(series_id)


async def test_a_download_rebuilt_on_the_next_source_is_one_card_in_the_agents_and_stays_after_the_sync(ui):
    """The voice-over is at two sources and the file of the first is gone: the plan takes the second. The window of agents
    shows one card of the need — the attempt of the first variant, «План перестроен на» the second, its attempt done —
    and the card stays once the sync has ended, until «Удалить»."""
    ui.site.release("ref", "AniStar")
    ui.site.release("dub", "AnimeVost")
    ui.site.release("dub2", "AnimeVost")
    ui.site.episode("ref", 1, height=720)
    ui.site.episode("dub", 1, height=480)
    ui.site.episode("dub2", 1, height=480)
    series_id = await ui.make_series(["ref", "dub", "dub2"])
    ui.site.gone("dub", 1)
    page = ui.page
    await ui.open_output(SERIES)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(1)
    downloads = page.locator(".work-card[data-kind='download']")
    await ui.tab(text("status.tab.agents"))  # the window of the series holds the same section, set to its series
    await expect(downloads).to_have_count(1)
    await ui.close_settings()
    await ui.settings("Агенты")
    card = downloads.filter(has=page.locator(".work-line-title", has_text=text("download.replan")))
    await expect(card).to_have_count(1)
    await expect(card).to_have_attribute("data-state", "done")
    lines = card.locator(".work-line")
    await expect(lines).to_have_count(3)
    await expect(lines.nth(0).locator(".is-failed")).to_have_count(1)
    await expect(lines.nth(1)).to_contain_text(text("download.replan"))
    await expect(lines.nth(2)).to_contain_text(text("state.done"))
    await expect(card.locator(".figure-mark").first).to_be_visible()
    await expect(downloads).to_have_count(1)  # the others went with the end of the sync
    await ui.shot("25-download-rebuilt-card")
    await card.locator(".work-action").click()
    await expect(downloads).to_have_count(0)


async def test_an_episode_is_coloured_by_the_work_the_agents_do_for_it_while_the_service_holds_its_alignment(ui):
    """While the alignment service works on the voice-over, the episode of the output is of the colour of the alignment
    and the window of the series lists the job of the service in its agents, set to the series; when the service ends the
    job the episode is ready and the job is no more the colour of the episode."""
    series_id = await series_in_browser(ui, (1,))
    ui.daemon.hold = True
    page = ui.page
    await ui.open_output(SERIES)
    ended = ui.bus.subscribe("scan.run.changed")
    await page.locator(".output-sync").click()
    await asyncio.wait_for(asyncio.to_thread(ui.daemon.made.wait), STAND_LIMIT_S)
    episode = page.locator(".episode-card[data-episode='1']")
    await expect(episode).to_have_attribute("data-state", "align")
    await ui.tab(text("status.tab.agents"))
    job = page.locator(".work-card[data-kind='conform']")
    await expect(job).to_have_count(1)
    await expect(job).to_have_attribute("data-state", "align")
    await ui.shot("26-aligning-in-agents")
    ui.daemon.hold = False
    ui.daemon.release()
    await ui._run_end(ended, series_id)
    await ui.tab(text("output.tab"))
    await expect(episode).to_have_attribute("data-state", "done")
    await ui.check(series_id)


async def test_a_voice_over_the_service_refused_is_shown_and_aligned_again_by_its_retry(ui):
    series_id = await series_in_browser(ui, (1,))
    ui.daemon.answers["Сериал s01e01 AnimeVost"] = "mismatch"
    page = ui.page
    await ui.open_output(SERIES)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='failed']")).to_have_count(1)
    await page.locator(".episode-card").first.dblclick()
    row = page.locator(".episode-detail tr[data-group='AnimeVost']")
    await expect(row.locator(".pill")).to_have_text(state("failed"))
    await ui.shot("21-refused")
    del ui.daemon.answers["Сериал s01e01 AnimeVost"]
    await row.locator(".row-action.do-retry").click()
    await expect(page.locator(".plan-failures")).to_have_count(0)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(1)
    await expect(row.locator(".pill")).to_have_text(state("done"))
    await ui.check(series_id)


async def test_an_episode_switched_off_on_its_card_hides_its_file_and_on_again_shows_it_with_no_work(ui):
    series_id = await synced_in_browser(ui, (1, 2))
    page = ui.page
    second = page.locator(".episode-card[data-episode='2']")
    # The answer to the press is held until the check is done: while it is on its way no switch of the tab fades, and a
    # press of another one goes nowhere.
    reached, release, sent = asyncio.Event(), asyncio.Event(), []

    async def held(route):
        sent.append(route.request.url)
        reached.set()
        await release.wait()
        await route.continue_()
    await page.route("**/output/switch", held)
    await second.locator(".power-switch").click()
    await asyncio.wait_for(reached.wait(), STAND_LIMIT_S)
    switches = page.locator(".power-switch")
    assert set(await switches.evaluate_all("all => all.map((each) => getComputedStyle(each).opacity)")) == {"1"}
    await ui.shot("29-switch-on-its-way")
    await page.locator(".episode-card[data-episode='1'] .power-switch").click()
    release.set()
    await expect(second).to_have_attribute("data-state", "off")
    await page.unroute("**/output/switch", held)
    assert len(sent) == 1
    await ui.press_sync(series_id)
    await expect(second).to_have_attribute("data-state", "off")
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(1)
    assert [name.endswith(".mkv.off") for name in ui.files()] == [False, True]
    await ui.check(series_id)
    await ui.shot("30-episode-off")
    jobs, served = len(ui.daemon.jobs), len(ui.host.served)
    await second.locator(".power-switch").click()
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(2)
    assert not any(name.endswith(".off") for name in ui.files())
    assert (len(ui.daemon.jobs), len(ui.host.served)) == (jobs, served)  # shown again, nothing downloaded or aligned
    await ui.check(series_id)


async def test_episodes_selected_as_in_a_file_manager_are_switched_off_by_space_in_one_command(ui):
    """A click selects a card, Shift+click the run to another, Esc lets the selection go; Space switches what is selected,
    as one command for all of it; a double click opens a card. The sync then hides the files of every one."""
    series_id = await synced_in_browser(ui, (1, 2, 3))
    page = ui.page
    card = lambda episode: page.locator(f".episode-card[data-episode='{episode}']")  # noqa: E731
    await card(1).click()
    await card(3).click(modifiers=["Shift"])
    await expect(page.locator(".episode-card.is-selected")).to_have_count(3)
    await expect(page.locator(".episode-detail")).to_have_count(0)
    await card(2).press("Escape")
    await expect(page.locator(".episode-card.is-selected")).to_have_count(0)
    await expect(page.locator(".episode-card")).to_have_count(3)  # Esc let the selection go and kept the window open
    await card(1).click()
    await card(3).click(modifiers=["Shift"])
    sent = []
    page.on("request", lambda request: sent.append(request.post_data_json) if request.url.endswith("/output/switch") else None)
    await ui.shot("44-episodes-selected")
    await page.keyboard.press(" ")
    await expect(page.locator(".episode-card[data-state='off']")).to_have_count(3)
    assert sent == [{"season": 1, "episodes": [1, 2, 3], "off": True}]
    await card(2).dblclick()
    await expect(page.locator(".episode-detail[data-episode='2']")).to_be_visible()
    await ui.press_sync(series_id)
    assert all(name.endswith(".mkv.off") for name in ui.files()) and len(ui.files()) == 3
    await ui.check(series_id)


async def test_a_voice_over_switched_off_in_the_settings_of_the_season_goes_from_its_files(ui):
    series_id = await synced_in_browser(ui, (1,))
    page = ui.page
    await page.locator(".season-settings").click()
    settings = page.locator(".level-settings")
    await page.locator(".modal-header .st-tab, .st-tab", has_text="AnimeVost").last.click()
    take = settings.locator("label.queue-take")
    await take.click()
    await expect(take.locator("input")).not_to_be_checked()
    await ui.shot("31-voice-off")
    await page.locator("button.level-footer", has_text="Закрыть").click()
    await expect(settings).to_have_count(0)
    await ui.press_sync(series_id)
    view = await ui.check(series_id)
    assert view["assets"][0]["tracks"] == []
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(1)  # switched off by the user: no part missing
    await page.locator(".episode-card").first.dblclick()
    row = page.locator(".episode-detail tr[data-group='AnimeVost']")
    await expect(row.locator(".pill")).to_have_text(state("off"))
    await expect(row.locator(".row-action")).to_have_count(0)
    await ui.shot("32-voice-gone")


async def test_a_higher_compilation_is_cut_into_its_episodes_and_shown_as_their_compilation(ui):
    """A compilation of episodes 1–3 in 2160p over single episodes in 720p: the episodes are cut out of it by the service,
    each card carries the letter of its compilation and the view of compilations takes all three."""
    ui.site.release("ref", "AniStar")
    ui.site.compilation("ref", 1, 3, height=2160)
    series_id = await synced_in_browser(ui, (1, 2, 3))
    page = ui.page
    cards = page.locator(".episode-card")
    await expect(cards.locator(".card-ref-shown .ref-letter")).to_have_count(3)  # the letter of the compilation, its height
    await expect(cards.locator(".card-ref-shown .ref-letter.presence.is-online")).to_have_count(3)  # the source gives it
    await expect(cards.locator(".card-ref-shown")).to_contain_text(["2160p"] * 3)
    view = await ui.check(series_id)
    assert sorted(asset["episode"][1] for asset in view["assets"] if asset["video"]["cut_of"]) == [1, 2, 3]
    await cards.first.dblclick()
    await expect(page.locator(".episode-detail .detail-file")).to_have_text("Serial s01e01 [AniStar] 2160p.mkv")
    await ui.shot("40-compilation-cards")
    await page.locator(".output-view-compilations").click()
    block = page.locator(".compilation-block")
    await expect(block).to_have_count(1)
    await expect(block.locator(".meter-text").first).to_have_text(text("output.meter.taken", n=3, total=3))
    await ui.shot("41-compilation-view")


async def test_a_better_reference_takes_the_place_and_the_voice_over_gone_from_its_source_is_marked_offline(ui):
    series_id = await synced_in_browser(ui, (1, 2))
    page = ui.page
    ui.site.drop("dub", 1)
    ui.site.episode("ref", 1, height=1080, truth=truth_of(1, 1))
    ui.later()
    await ui.press_sync(series_id)
    first = page.locator(".episode-card[data-episode='1']")
    await expect(first.locator(".card-ref-shown")).to_contain_text("1080p")
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(2)
    await first.dblclick()
    row = page.locator(".episode-detail tr[data-group='AnimeVost']")
    await expect(row.locator(".pill")).to_have_text(state("done"))
    await expect(row.locator(".track-offline")).to_have_text(text("output.plan.offline"))
    await ui.shot("50-better-reference")
    await ui.check(series_id)


async def test_the_window_open_through_a_restart_in_the_middle_of_a_sync_shows_its_end(ui):
    """The service stops while it works and starts again: the window reconnects by itself and shows the end of the sync
    the service carried on, with no reload by the user."""
    series_id = await series_in_browser(ui, (1, 2))
    page = ui.page
    await ui.open_output(SERIES)
    working = ui.bus.subscribe("planner.series.changed")
    await page.locator(".output-sync").click()
    while not (await asyncio.wait_for(working.queue.get(), STAND_LIMIT_S)).payload["running"]:
        pass
    await ui.restart()
    await ui.until_run_ends(series_id)
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(2, timeout=30_000)
    await ui.shot("60-after-restart")
    await ui.check(series_id)


async def pills(card) -> list[str]:
    """The statuses the card of a series shows (a hidden row of every pill only measures them)."""
    return await card.locator(".status-pill").evaluate_all(
        "els => els.filter(e => !e.closest('.status-pills-measure')).map(e => e.innerText.trim())")


async def test_the_card_of_the_series_tells_ready_error_and_the_date_of_its_new_files(ui):
    """The list of series is what the user sees first: a series with files is «Готово», with the date of its new files; a
    crash of the alignment service is «Ошибка» there and an error of the episode with its reason in the window; the sync
    that makes the episode clears the error."""
    ui.site.release("ref", "AniStar")
    ui.site.release("dub", "AnimeVost")
    for number in (1, 2):
        ui.site.episode("ref", number, height=720)
        ui.site.episode("dub", number, height=480)
    ui.daemon.answers["Сериал s01e02 AnimeVost"] = "crash"
    series_id = await ui.make_series(["ref", "dub"])
    page = ui.page
    card = page.locator(".series-card", has_text=SERIES)
    assert await card.locator(".scan-time").inner_text() == "Никогда"
    await ui.open_output()
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='failed']")).to_have_count(1)
    await expect(page.locator(".plan-problems")).to_have_text(text("output.plan.problems", count=1))
    await page.locator(".episode-card[data-episode='2']").dblclick()
    row = page.locator(".episode-detail tr[data-group='AnimeVost']")
    await expect(row.locator(".pill")).to_have_text(state("failed"))
    await expect(row.locator(".track-note")).to_contain_text("сбой")
    await ui.shot("70-crash-in-window")
    await page.get_by_role("button", name="Закрыть").last.click()
    await expect(card.locator(".scan-time")).not_to_have_text("Никогда")  # episode 1 was built: the card has its date
    shown = await pills(card)
    assert state("failed") in shown and state("done") in shown, shown
    await ui.shot("71-crash-on-the-card")
    ui.daemon.answers.clear()
    await ui.open_output()
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(2)
    await expect(page.locator(".plan-problems")).to_have_count(0)
    await page.get_by_role("button", name="Закрыть").last.click()
    await expect(card.locator(".status-pill", has_text=state("failed"))).to_have_count(0)
    shown = await pills(card)
    assert state("done") in shown and state("failed") not in shown, shown
    await ui.check(series_id)


async def test_the_sync_stopped_by_its_button_cancels_its_work_applies_nothing_and_frees_the_series(ui):
    """«Остановить синхронизацию» in the section «Выход» while the service aligns: the job of the service is cancelled,
    no file is made, the button goes, and the card of the series offers a scan again."""
    await series_in_browser(ui, (1,))
    ui.daemon.hold = True
    await ui.open_output(SERIES)
    await ui.page.locator(".output-sync").click()
    await asyncio.wait_for(asyncio.to_thread(ui.daemon.made.wait), STAND_LIMIT_S)
    stop = ui.page.locator(".output-stop")
    await expect(ui.page.locator(".output-sync")).to_have_count(0)  # the button of the sync is the one that stops it
    await ui.shot("80-sync-under-way")
    await stop.click()
    await expect(stop).to_have_count(0)
    await expect(ui.page.locator(".output-sync")).to_have_count(1)
    assert ui.daemon.jobs == {} and ui.files() == []
    await ui.shot("81-sync-stopped")
    await ui.page.locator(".modal-close, .p-dialog-close-button, button:has-text('Закрыть')").first.click()
    card = ui.page.locator(".series-card", has_text=SERIES)
    await expect(card.locator(".btn-scan")).to_be_enabled()
    await expect(card.locator(".btn-stop")).to_have_count(0)


# Every order the queue of the opened episode puts on the screen from now on (the voice-over of each row): a change of
# the page is read at the frame it is drawn in, as the user sees it, not in the middle of the task that makes it.
OBSERVE_QUEUE = """() => {
    window.__orders = []
    const body = document.querySelector('.episode-detail .queue-table tbody')
    let asked = false
    const draw = () => {
        asked = false
        window.__orders.push([...body.querySelectorAll('.variant-row td:nth-child(6)')].map((each) => each.textContent.trim()))
    }
    new MutationObserver(() => { if (!asked) { asked = true; requestAnimationFrame(draw) } })
        .observe(body, { childList: true, subtree: true, characterData: true })
}"""


async def test_a_queue_dragged_by_its_handle_holds_the_new_order_until_the_core_answers_and_a_refusal_puts_it_back(ui):
    """A row of the queue of the reference is dragged by its handle to the top, on the engine of the constructor of rules:
    the queue shows the new order at once and keeps it while the answer of the core is held on its way (no jump back
    to the old order), and a drag in that time is not taken. The core holds the order: a window opened anew shows it.
    An order the core refuses goes back to the one the core holds."""
    await synced_in_browser(ui, (1,))
    page = ui.page

    async def queue():
        await page.locator(".episode-card").first.dblclick()
        detail = page.locator(".episode-detail")
        await detail.locator(".st-tab", has_text=text("output.tab.reference")).click()
        rows = detail.locator(".queue-table .variant-row")
        await expect(rows).to_have_count(2)
        return rows, rows.locator("td:nth-child(6)")  # the voice-over of each variant (both play on one player)

    rows, groups = await queue()
    before = await groups.all_inner_texts()
    dragged = before[::-1]
    await page.evaluate(OBSERVE_QUEUE)
    sent, release = [], asyncio.get_running_loop().create_future()

    async def held(route):
        sent.append(route.request.post_data_json)
        await release
        await route.continue_()

    await page.route("**/api/series/*/output/settings", held)
    await ui.drag(rows.nth(1).locator(".order-handle"), rows.nth(0))
    await expect(groups).to_have_text(dragged)
    await ui.drag(rows.nth(1).locator(".order-handle"), rows.nth(0))  # the answer is on its way: not taken
    await expect(groups).to_have_text(dragged)
    await ui.shot("40-queue-dragged")
    # The core's queue read again after the accepted order is the end of the wait.
    async with page.expect_response(lambda response: "/output/queues" in response.url) as reread:
        release.set_result(None)
    await reread.value
    await page.unroute("**/api/series/*/output/settings")
    await page.evaluate("() => new Promise((done) => requestAnimationFrame(() => done()))")
    await expect(groups).to_have_text(dragged)
    await expect(page.locator(".output-failure")).to_have_count(0)
    assert [(body["lever"], [each["group"] for each in body["value"]]) for body in sent] == [("reference.order", dragged)]
    orders = await page.evaluate("() => window.__orders")
    assert orders and before not in orders, orders

    await page.reload()
    await ui.open_output(SERIES)
    rows, groups = await queue()
    await expect(groups).to_have_text(dragged)

    refusals, refuse = [], asyncio.get_running_loop().create_future()

    async def refused(route):
        refusals.append(route.request.post_data_json)
        await refuse
        await route.fulfill(status=409, json={"detail": "refused"})

    await page.route("**/api/series/*/output/settings", refused)
    await ui.drag(rows.nth(1).locator(".order-handle"), rows.nth(0))
    await expect(groups).to_have_text(before)  # shown while on its way
    refuse.set_result(None)
    await expect(page.locator(".output-failure")).to_have_count(1)
    await expect(groups).to_have_text(dragged)  # the core's order again
    assert [[each["group"] for each in body["value"]] for body in refusals] == [before]
    await ui.shot("41-queue-refused")


async def test_the_name_of_a_track_given_on_its_chip_is_its_title_in_the_file_after_a_sync_with_nothing_fetched(ui):
    """A press on the name of a chip of «Дорожки в контейнере» makes it a field; the name is kept for the series and, at the next
    sync, the tracks are copied into a file whose track bears it (the group stays its tag): nothing is downloaded or
    aligned. An empty name gives the track back the name of its group."""
    series_id = await synced_in_browser(ui, (1,))
    page = ui.page
    chips = page.locator(".voice-order-item")
    names = chips.locator(".voice-order-name")
    await expect(names).to_have_text(["AniStar", "AnimeVost"])
    work = (len(ui.daemon.jobs), len(ui.host.served))

    async def rename(chip: int, title: str) -> None:
        await chips.nth(chip).locator(".voice-order-name").click()
        field = chips.nth(chip).locator(".voice-title-input")
        await field.fill(title)
        await field.press("Enter")

    await rename(1, "ПМ_AnimeVost")
    await expect(names).to_have_text(["AniStar", "ПМ_AnimeVost"])
    await ui.shot("50-track-renamed")
    await page.reload()
    await ui.open_output(SERIES)
    await expect(names).to_have_text(["AniStar", "ПМ_AnimeVost"])  # the core holds the name
    await ui.press_sync(series_id)
    view = await ui.check(series_id)
    (asset,) = view["assets"]
    held = await mkv.contents(os.path.join(ui.library, asset["name"]))
    assert [(track.label, track.title) for track in held.audio] == [("AniStar", "AniStar"), ("AnimeVost", "ПМ_AnimeVost")]
    assert (len(ui.daemon.jobs), len(ui.host.served)) == work  # the tracks were copied, not fetched or aligned again

    await rename(1, "")
    await expect(names).to_have_text(["AniStar", "AnimeVost"])
    await ui.press_sync(series_id)
    view = await ui.check(series_id)
    (asset,) = view["assets"]
    held = await mkv.contents(os.path.join(ui.library, asset["name"]))
    assert [track.title for track in held.audio] == ["AniStar", "AnimeVost"]
    await expect(page.locator(".output-failure")).to_have_count(0)


async def test_the_markup_reference_set_in_its_tab_lays_the_episode_onto_it_and_its_sound_goes_last(ui):
    """Stage 14: a high picture (ПВА 2160p) and the original in the right structure (1080p). In the settings of the series
    the tab «Эталон» follows the reference until the user moves the original first: the sync cuts the picture onto the
    original's timeline, the original's sound is the last of the file and the card names the markup reference;
    «как уровнем выше» makes the markup follow the reference again and does no work but the order of the sounds."""
    ui.site.release("pva", "ПВА")
    ui.site.release("orig", "Оригинал")
    ui.site.release("dub", "AnimeVost")
    ui.site.episode("pva", 1, height=2160)
    ui.site.episode("orig", 1, height=1080)
    ui.site.episode("dub", 1, height=480)
    series_id = await ui.make_series(["pva", "orig", "dub"])
    page = ui.page
    await ui.open_output(SERIES)

    async def markup_tab():
        await page.locator(".output-series-settings").click()
        await page.locator(".st-tab", has_text=text("output.tab.markup")).last.click()
        rows = page.locator(".level-settings .queue-table .variant-row")
        await expect(rows).to_have_count(3)
        return rows, rows.locator("td:nth-child(6)")

    async def close():
        await page.locator("button.level-footer", has_text="Закрыть").click()
        await expect(page.locator(".level-settings")).to_have_count(0)

    rows, groups = await markup_tab()
    await expect(page.locator(".level-settings .queue-follows")).to_have_text(text("output.queue.followsReference"))
    await expect(groups).to_have_text(["ПВА", "Оригинал", "AnimeVost"])  # the queue of the reference itself
    async with page.expect_response(lambda response: "/output/queues" in response.url):
        await rows.nth(1).locator(".order-handle").press("ArrowUp")
    await expect(groups).to_have_text(["Оригинал", "ПВА", "AnimeVost"])
    await expect(page.locator(".level-settings .queue-follows")).to_have_count(0)
    await expect(page.locator(".level-settings .queue-back")).to_have_count(1)
    await ui.shot("60-markup-set")
    await close()

    await ui.press_sync(series_id)
    card = page.locator(".episode-card[data-episode='1']")
    await expect(card).to_have_attribute("data-state", "done")
    await expect(card.locator(".card-ref-shown .ref-markup")).to_have_count(1)
    await expect(card.locator(".card-ref-shown")).to_contain_text("2160p")
    # The window shows the sounds in the order the file holds them: the original last.
    await expect(page.locator(".voice-order-item").last).to_contain_text("Оригинал")
    await expect(page.locator(".voice-order-item.is-fixed")).to_have_count(1)
    # Every sound of the file has its cell, the own sound of the cut (ПВА) the reference's.
    await expect(card.locator("[data-state='done']")).to_have_count(3)
    await expect(card.locator("[data-state='done'].is-reference")).to_have_count(1)
    await ui.shot("61-markup-synced")
    sources = {each["url"]: each["id"] for each in (await ui.call("GET", f"/api/series/{series_id}"))["sources"]}
    view = await ui.check(series_id)
    (asset,) = view["assets"]
    video = asset["video"]
    assert video["cut_of"]["source_id"] == sources[ui.site.releases["pva"].url]
    (goal,) = view["episodes"]
    assert goal["video"]["row"]["source_id"] == sources[ui.site.releases["orig"].url]  # the timeline
    held = await mkv.contents(os.path.join(ui.library, asset["name"]))
    assert [each.label for each in held.audio][-1] == "Оригинал"
    jobs = len(ui.daemon.jobs)

    await markup_tab()
    async with page.expect_response(lambda response: "/output/queues" in response.url):
        await page.locator(".level-settings .queue-back").click()
    await expect(page.locator(".level-settings .queue-follows")).to_have_count(1)
    await close()
    await ui.press_sync(series_id)
    await expect(card.locator(".card-ref-shown .ref-markup")).to_have_count(0)
    view = await ui.check(series_id)
    (after,) = view["assets"]
    assert after["video"] == video and len(ui.daemon.jobs) == jobs  # no cut, no alignment: only the order of the sounds
    await ui.shot("62-markup-follows-again")
