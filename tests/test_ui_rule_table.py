"""A rule «Таблица» made in a browser (tests/ui_stand.py): the user reads the page of each source in the test of the
profile, fills the table of voice-overs by the quick template «V:», adds the rule of the episodes, and syncs a series
whose two sources share the profile. Every file holds both voice-overs, each named by its row of the table."""
import os
import re

import pytest
from playwright.async_api import expect

from core import container as mkv

from ui_stand import SERIES, SERIES_EN, SINGLE, text, ui  # noqa: F401 - the fixture

pytestmark = pytest.mark.ui

PROFILE = "серии и озвучки"
VOICES = {"dub": ("AnimeVost", 720), "alt": ("AniStar", 480)}


async def test_a_table_filled_from_the_pages_by_a_template_names_the_voice_overs_of_both_sources(ui):
    for slug, (voice, height) in VOICES.items():
        ui.site.release(slug, voice)
        for episode in (1, 2):
            ui.site.episode(slug, episode, height=height)
    await ui.first_run()
    await ui.set_up([voice for voice, _height in VOICES.values()])

    page = ui.page
    await ui.settings("Фильтры VK")
    if not await page.locator("#new-profile-name").is_visible():
        await page.locator("button.accordion-button").first.click()
    await page.locator("#new-profile-name").fill(PROFILE)
    await page.locator("button.btn-add[title='Создать']").click()
    rules_step = page.get_by_text(text("rules.step.rules", profile=PROFILE))
    await rules_step.wait_for()

    await page.get_by_role("button", name=text("rules.table.add")).click()
    card = page.locator(".rule-table-card")
    await card.locator(".rule-name-input").fill("Озвучки")
    for slug in VOICES:
        await page.get_by_text(text("rules.step.test")).click()
        await page.get_by_label(text("rulesTest.field.address")).fill(ui.site.releases[slug].url)
        await page.get_by_role("button", name=text("rulesTest.fetch.page")).click()
        await expect(page.locator(".parser-test-textarea")).to_have_value(re.compile(r"V:"), timeout=60_000)
        await rules_step.click()
        await card.locator(".template-chip", has_text="V:").click()
    await expect(card.locator("tbody td.col-if")).to_have_count(2)
    for at, voice in enumerate(["AnimeVost", "AniStar"]):
        await expect(card.locator("input.table-value").nth(at)).to_have_value(voice)
    await ui.shot("t1-table-of-voice-overs")
    async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/rules")) as saved, \
            page.expect_response(lambda r: r.request.method == "GET" and r.url.endswith("/rules")):
        await card.locator("button[title='Сохранить правило']").click()
    assert (await saved.value).ok, "the table was not saved"
    await ui._rule({"name": "серия", "if": SINGLE, "then": [("extract_single", SINGLE)]})
    await ui.close_settings()

    series_id = await ui.add_series(SERIES, SERIES_EN, [(ui.site.releases[slug].url, PROFILE) for slug in VOICES])
    await ui.open_output()
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card.is-ready")).to_have_count(2)
    view = await ui.check(series_id)
    for asset in view["assets"]:
        held = await mkv.contents(os.path.join(ui.library, asset["name"]))
        assert sorted(track.label for track in held.audio) == ["AniStar", "AnimeVost"]
