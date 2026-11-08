"""The whole application in a browser (tests/ui_stand.py): the user's way through the screens on a stand whose site,
player and alignment service are models."""
import asyncio
import re

import pytest
from playwright.async_api import expect

from app_stand import STAND_LIMIT_S
from plan_world import truth_of
from ui_stand import SERIES, text, ui  # noqa: F401 - the fixture

pytestmark = pytest.mark.ui


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
    await expect(ui.page.locator(".episode-card.is-ready")).to_have_count(2)
    await ui.shot("02-synced")
    view = await ui.check(series_id)
    assert len(view["assets"]) == 2
    await cards.first.click()
    detail = ui.page.locator(".episode-detail")
    await detail.wait_for()
    await ui.shot("03a-opened")
    await expect(detail.locator(".detail-file")).to_have_text(view["assets"][0]["name"])
    row = detail.locator(".track-table tr[data-group='AnimeVost']")
    await expect(row.locator(".pill")).to_have_text(text("output.track.state.done"))
    await ui.shot("03-episode")


async def synced_in_browser(ui, episodes=(1,)) -> int:
    series_id = await series_in_browser(ui, episodes)
    await ui.open_output(SERIES)
    await ui.press_sync(series_id)
    await expect(ui.page.locator(".episode-card.is-ready")).to_have_count(len(episodes))
    return series_id


async def test_remake_and_download_again_wait_for_the_sync_and_can_be_taken_back(ui):
    """«Пересоздать» a voice-over and «Скачать заново» the video: each waits for the next sync in the bar of the section,
    «Отменить» takes it back; the sync does what it named and nothing else."""
    series_id = await synced_in_browser(ui)
    page = ui.page
    (asset,) = (await ui.call("GET", f"/api/series/{series_id}/plan"))["assets"]
    await page.locator(".episode-card").first.click()
    detail = page.locator(".episode-detail")
    pending = page.locator(".plan-pending .plan-command")

    await detail.locator("tr[data-group='AnimeVost'] .track-action.do-redo").click()
    await expect(pending).to_have_count(1)
    await expect(pending).to_contain_text(text("output.plan.deleteTrack", voice="AnimeVost", name=asset["name"]))
    await ui.shot("10-remake-pending")
    await pending.get_by_role("button", name=text("output.plan.cancel")).click()
    await expect(pending).to_have_count(0)

    await detail.locator("tr[data-group='AnimeVost'] .track-action.do-redo").click()
    await expect(pending).to_have_count(1)
    aligned = len(ui.daemon.jobs)
    await ui.press_sync(series_id)
    await expect(pending).to_have_count(0)
    await expect(page.locator(".episode-card.is-ready")).to_have_count(1)
    assert len(ui.daemon.jobs) == aligned + 1  # the voice-over aligned anew, once
    await ui.check(series_id)

    await detail.locator(".track-action.do-refetch").click()
    await expect(pending).to_contain_text(text("output.plan.deleteVideo", name=asset["name"]))
    served = len(ui.host.served)
    await ui.press_sync(series_id)
    await expect(pending).to_have_count(0)
    await expect(page.locator(".episode-card.is-ready")).to_have_count(1)
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
    await expect(page.locator(".episode-card.is-error")).to_have_count(1)
    await expect(page.locator(".plan-failures")).to_have_text(text("output.plan.failures", count=1))
    await page.locator(".episode-card").first.click()
    row = page.locator(".episode-detail tr[data-group='AnimeVost']")
    await expect(row.locator(".pill")).to_have_text(text("output.track.state.error"))
    await ui.shot("20-download-failed")
    asked = len(ui.host.served)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card.is-error")).to_have_count(1)
    assert not [name for name in ui.host.served[asked:] if name.startswith("dub-")]  # passed over, not asked again
    ui.site.gone("dub", 1, False)
    await page.locator(".output-retry-failed").click()
    await expect(page.locator(".plan-failures")).to_have_count(0)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card.is-ready")).to_have_count(1)
    await expect(row.locator(".pill")).to_have_text(text("output.track.state.done"))
    await ui.check(series_id)


async def test_a_voice_over_the_service_refused_is_shown_and_aligned_again_by_its_retry(ui):
    series_id = await series_in_browser(ui, (1,))
    ui.daemon.answers["Сериал s01e01 AnimeVost"] = "mismatch"
    page = ui.page
    await ui.open_output(SERIES)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card.is-error")).to_have_count(1)
    await page.locator(".episode-card").first.click()
    row = page.locator(".episode-detail tr[data-group='AnimeVost']")
    await expect(row.locator(".pill")).to_have_text(text("output.track.state.error"))
    await ui.shot("21-refused")
    del ui.daemon.answers["Сериал s01e01 AnimeVost"]
    await row.locator(".track-action.do-retry").click()
    await expect(page.locator(".plan-failures")).to_have_count(0)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card.is-ready")).to_have_count(1)
    await expect(row.locator(".pill")).to_have_text(text("output.track.state.done"))
    await ui.check(series_id)


async def test_an_episode_switched_off_on_its_card_hides_its_file_and_on_again_shows_it_with_no_work(ui):
    series_id = await synced_in_browser(ui, (1, 2))
    page = ui.page
    second = page.locator(".episode-card[data-episode='2']")
    await second.locator(".power-switch").click()
    await expect(second).to_have_class(re.compile(r"\bis-off\b"))
    await ui.press_sync(series_id)
    await expect(second).to_have_class(re.compile(r"\bis-off\b"))
    await expect(page.locator(".episode-card.is-ready")).to_have_count(1)
    assert [name.endswith(".mkv.off") for name in ui.files()] == [False, True]
    await ui.check(series_id)
    await ui.shot("30-episode-off")
    jobs, served = len(ui.daemon.jobs), len(ui.host.served)
    await second.locator(".power-switch").click()
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card.is-ready")).to_have_count(2)
    assert not any(name.endswith(".off") for name in ui.files())
    assert (len(ui.daemon.jobs), len(ui.host.served)) == (jobs, served)  # shown again, nothing downloaded or aligned
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
    await expect(page.locator(".episode-card.is-ready")).to_have_count(1)  # switched off by the user: no part missing
    await page.locator(".episode-card").first.click()
    row = page.locator(".episode-detail tr[data-group='AnimeVost']")
    await expect(row.locator(".pill")).to_have_text(text("output.track.state.off"))
    await expect(row.locator(".track-action")).to_have_count(0)
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
    await cards.first.click()
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
    await expect(page.locator(".episode-card.is-ready")).to_have_count(2)
    await first.click()
    row = page.locator(".episode-detail tr[data-group='AnimeVost']")
    await expect(row.locator(".pill")).to_have_text(text("output.track.state.done"))
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
    await expect(page.locator(".episode-card.is-ready")).to_have_count(2, timeout=30_000)
    await ui.shot("60-after-restart")
    await ui.check(series_id)
