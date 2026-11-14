"""The settings of the voices on the stand of the whole application, in the browser: a group of voice-overs the user adds is saved
and stays after the page is read again, whatever the order of sources holds (tests/ui_stand.py)."""
from __future__ import annotations

import json

import pytest
from playwright.async_api import expect

from ui_stand import _pick, text, ui  # noqa: F401 - the fixture

pytestmark = [pytest.mark.ui, pytest.mark.asyncio]

GROUP = "New Horizons Studio"


async def test_a_group_added_while_the_order_holds_a_line_nobody_names_is_saved(ui):  # noqa: F811
    await ui.first_run()
    # The order the user saved while the provider was up: its line is in it, and no provider names it now.
    await ui.module("voices").request("settings.values.set_many",
                                      {"values": {"voices.source_priority": json.dumps(["line:CO4K", "kodik", "cvh"])}})
    page = ui.page
    await page.reload()
    await ui.settings(text("settings.tab.voices"))
    await page.get_by_label(text("voices.settings.group.new")).fill(GROUP)
    await page.get_by_role("button", name=text("voices.settings.group.create")).click()
    chosen = page.locator(".groups-editor .item-select").first.locator(".selected-value")
    await expect(chosen).to_have_text(GROUP)
    await _pick(page, page.locator(".groups-editor .group-language .item-select"), text("voices.language.rus"))
    await expect(page.locator(".groups-editor .group-language .selected-value")).to_have_text(text("voices.language.rus"))
    await expect(page.locator(".sheet-problem")).to_have_count(0)

    # Read again from the core: the group is there, and the order is as the user left it.
    await page.reload()
    await ui.settings(text("settings.tab.voices"))
    shown = await ui.call("GET", "/api/voices/settings")
    assert GROUP in shown["voice_groups"] and shown["voice_languages"][GROUP] == "rus"
    assert shown["priority"][:3] == ["line:CO4K", "kodik", "cvh"]
    await page.locator(".groups-editor .item-select").first.click()
    await expect(page.get_by_role("option", name=GROUP)).to_be_visible()
