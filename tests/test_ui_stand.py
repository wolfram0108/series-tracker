"""The whole application in a browser (tests/ui_stand.py): the user's way through the screens on a stand whose site,
player and alignment service are models."""
import asyncio
import re
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
    await page.locator(".episode-detail .st-tab", has_text="AnimeVost").click()
    await expect(page.locator(".episode-detail .queue-failed")).to_have_text(text("output.queue.failed.download"))
    await page.locator(".episode-detail .st-tab", has_text=text("output.tab.tracks")).click()
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


async def pills(card) -> list[str]:
    """The statuses the card of a series shows (a hidden row of every pill only measures them)."""
    return await card.locator(".status-pill").evaluate_all(
        "els => els.filter(e => !e.closest('.status-pills-measure')).map(e => e.innerText.trim())")


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


def batches_of(page) -> list:
    """What the window sends for the boxes of a queue: every PUT to the levers of the series, as (the path after the series, the body)."""
    sent: list = []
    page.on("request", lambda request: sent.append((request.url.split("/output/", 1)[1], request.post_data_json))
            if request.method == "PUT" and "/output/settings" in request.url else None)
    return sent


async def reference_queue(ui):
    page = ui.page
    await page.locator(".episode-card").first.dblclick()
    detail = page.locator(".episode-detail")
    await detail.locator(".st-tab", has_text=text("output.tab.reference")).click()
    rows = detail.locator(".queue-table .variant-row")
    await expect(rows).to_have_count(2)
    return detail, rows.locator("input[type=checkbox]")
