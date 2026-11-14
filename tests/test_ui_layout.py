"""The places of the windows of the application on the stand (tests/ui_stand.py): a plate that holds a name has room for it."""
from __future__ import annotations

import pytest
from playwright.async_api import expect

from test_ui_stand import series_in_browser
from ui_stand import SERIES, text, ui  # noqa: F401 - the fixture

pytestmark = [pytest.mark.ui, pytest.mark.asyncio]

LONG = "Владыка множества миров / Почтенный мастер всех миров / Ten Thousand Worlds"


async def test_the_plate_of_the_series_in_the_agents_of_its_window_has_the_whole_row_for_a_long_name(ui):  # noqa: F811
    await series_in_browser(ui, (1,))
    page = ui.page
    await page.set_viewport_size({"width": 1280, "height": 700})
    await page.locator(".series-card", has_text=SERIES).first.locator("[title='Статус']").click()
    await page.get_by_label(text("seriesForm.nameRu")).fill(LONG)
    await page.locator(".modern-footer").get_by_role("button", name=text("common.save")).click()
    await expect(page.locator(".p-toast-message", has_text=text("properties.saved"))).to_be_visible()
    await page.locator(".st-tab[title='Агенты']").click()
    plate = page.locator(".agents-select")
    await expect(plate).to_be_visible()
    box, room = await plate.bounding_box(), await page.locator(".agents-filter").bounding_box()
    assert box["width"] >= room["width"] - 2, (box, room)  # not a plate of a picker's width
    assert box["height"] < 70, box  # the name is in one line where the room holds it
