"""The order of sources in the settings (Настройки → Озвучки): every player with its kind, the best height of its rows and
where it is among the sources of the series, and its switch — a player switched off leaves the layout of every series as
the choice of the settings and keeps its place in the order; switched on, its rows come back. The whole application on
the stand (tests/ui_stand.py)."""
from __future__ import annotations

import asyncio
import re

import pytest
from playwright.async_api import expect

from test_ui_stand import series_in_browser
from ui_stand import text, ui  # noqa: F401 - the fixture

pytestmark = [pytest.mark.ui, pytest.mark.asyncio]


async def exclusions(ui, series_id: int, want: set) -> set:
    """The reasons the rows of the stand's player are out of the layout, once the layout has come to `want`."""
    found: set = set()
    for _ in range(100):
        rows = (await ui.call("GET", f"/api/series/{series_id}/composition?include_excluded=true"))["rows"]
        found = {row["exclusion"] for row in rows if row["player"] == "allvideo"}
        if found == want:
            return found
        await asyncio.sleep(0.1)
    return found


async def test_the_order_shows_where_each_player_is_and_its_switch_takes_it_out_of_every_series_and_back(ui):  # noqa: F811
    series_id = await series_in_browser(ui, (1,))
    page = ui.page
    assert await exclusions(ui, series_id, {None}) == {None}

    await ui.settings("Озвучки")
    row = page.locator(".priority-row[data-player='allvideo']")
    await expect(row.locator(".kind")).to_have_text(text("voices.settings.priority.kind.site"))
    await expect(row.locator(".height-cell")).to_have_text("720p")
    await expect(row.locator(".place")).to_have_text(["ruchime 2"])
    await expect(page.locator(".priority-row[data-player='vibix'] .none")).to_have_text(
        text("voices.settings.priority.noSources"))
    place = await page.locator(".priority-row").evaluate_all("rows => rows.map(row => row.dataset.player)")

    await row.locator(".p-toggleswitch").click()
    await expect(row).to_have_class(re.compile(r"\bis-off\b"))
    assert await exclusions(ui, series_id, {"settings"}) == {"settings"}
    # Switched off, the player keeps its place in the order.
    assert await page.locator(".priority-row").evaluate_all("rows => rows.map(row => row.dataset.player)") == place
    await ui.shot("41-source-order-off")
    # A narrow section turns each row into a card: no head, the height said in words.
    await page.set_viewport_size({"width": 390, "height": 844})
    await expect(page.locator(".priority-head")).to_be_hidden()
    await expect(row.locator(".height-card")).to_have_text(text("voices.settings.priority.upTo", height=720))
    await row.scroll_into_view_if_needed()
    await ui.shot("42-source-order-phone")
    await page.set_viewport_size({"width": 1440, "height": 1000})

    await ui.close_settings()
    await ui.settings("Озвучки")
    await expect(row).to_have_class(re.compile(r"\bis-off\b"))
    await row.locator(".p-toggleswitch").click()
    await expect(row).not_to_have_class(re.compile(r"\bis-off\b"))
    assert await exclusions(ui, series_id, {None}) == {None}
