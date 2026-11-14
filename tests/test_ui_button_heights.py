"""The height of the buttons, the whole application on the stand (tests/ui_stand.py): the buttons on the ground of a window — the header of
the page, the line of actions of a tab — keep their height, and those in the band of a head (a season, a source) are lower than they."""
from __future__ import annotations

import pytest
from playwright.async_api import expect

from test_ui_stand import series_in_browser
from ui_stand import SERIES, text, ui  # noqa: F401 - the fixture

pytestmark = [pytest.mark.ui, pytest.mark.asyncio]

HEIGHTS = """(selector) => [...document.querySelectorAll(selector)].map((each) => Math.round(each.getBoundingClientRect().height))"""


async def test_the_buttons_in_the_band_of_a_head_are_lower_than_the_buttons_on_the_ground(ui):  # noqa: F811
    await series_in_browser(ui, (1,))
    page = ui.page
    await page.set_viewport_size({"width": 1280, "height": 900})
    ground = await page.evaluate(HEIGHTS, ".header-actions .p-button")
    await ui.open_output(SERIES)
    await expect(page.locator(".episode-card")).to_have_count(1)
    toolbar = await page.evaluate(HEIGHTS, ".output-actions .p-button")
    season = await page.evaluate(HEIGHTS, ".season-head .p-button")
    assert ground and toolbar and season
    assert min(ground + toolbar) > max(season), (ground, toolbar, season)
    await ui.tab(text("status.tab.properties"))
    source = await page.evaluate(HEIGHTS, ".source-head .p-button")
    assert source and min(ground + toolbar) > max(source), (ground, toolbar, source)
