"""The windows and the lists for the keyboard and for the reader of the screen, the whole application on the stand (tests/ui_stand.py):
no screen has a violation that axe-core finds, a window holds the focus and gives it back, Escape goes to the layer on top alone, a window
with typed data asks before it is closed, a list that drops is worked with the keys.

The words on a gradient ground (the buttons) axe cannot count; web/src/styles/guards.test.ts does."""
from __future__ import annotations

import os

import pytest
from playwright.async_api import expect

from test_ui_stand import series_in_browser
from ui_stand import SERIES, text, ui  # noqa: F401 - the fixture

pytestmark = [pytest.mark.ui, pytest.mark.asyncio]

AXE = os.path.join(os.path.dirname(__file__), "..", "web", "node_modules", "axe-core", "axe.min.js")

FOUND = """() => axe.run(document, {resultTypes: ['violations']})
  .then((r) => r.violations.map((v) => v.id + ': ' + v.nodes.slice(0, 3).map((n) => n.target.join(' ').slice(0, 80)).join(' | ')))"""
TABS = "() => [...document.querySelectorAll('.modern-header .st-tab')].map((each) => each.getAttribute('title'))"
OUTSIDE = "() => !document.activeElement.closest('.modern-modal')"


async def tabs_of(page) -> list[str]:
    return await page.evaluate(TABS)


async def test_no_screen_has_a_violation_axe_finds(ui):  # noqa: F811
    await series_in_browser(ui, (1,))
    page = ui.page
    await page.set_viewport_size({"width": 1280, "height": 900})
    await page.add_script_tag(path=AXE)
    found: dict[str, list[str]] = {}

    async def scan(where: str) -> None:
        # A notice that is fading out is painted thin: axe would count its words half-written. Its end is the signal.
        await expect(page.locator(".p-toast-message")).to_have_count(0, timeout=15_000)
        violations = await page.evaluate(FOUND)
        if violations:
            found[where] = violations

    await scan("main")
    for button, name in ((text("header.addSeries"), "add"), (text("header.logs"), "logs"), ("Настройки", "settings"),
                         (text("header.extensions"), "extensions")):
        await page.get_by_role("button", name=button).click()
        await expect(page.locator(".modern-modal")).to_be_visible()
        for title in await tabs_of(page) or [None]:
            if title:
                await page.locator(f".st-tab[title='{title}']").click()
            await scan(f"{name}/{title}")
        await page.keyboard.press("Escape")
        await expect(page.locator(".modern-modal")).to_have_count(0)
    await ui.open_output(SERIES)
    for title in await tabs_of(page):
        await page.locator(f".st-tab[title='{title}']").click()
        await scan(f"status/{title}")
    assert found == {}, found


async def test_a_window_holds_the_focus_and_gives_it_back(ui):  # noqa: F811
    await series_in_browser(ui, (1,))
    page = ui.page
    opener = page.get_by_role("button", name=text("header.addSeries"))
    await opener.click()
    dialog = page.get_by_role("dialog", name=text("add.title"))
    await expect(dialog).to_be_visible()
    assert not await page.evaluate(OUTSIDE), "the focus is in the window when it opens"
    for _ in range(70):
        await page.keyboard.press("Tab")
        assert not await page.evaluate(OUTSIDE), "Tab walks the window in a ring"
    await page.keyboard.press("Escape")
    await expect(dialog).to_have_count(0)
    await expect(opener).to_be_focused()


async def test_escape_goes_to_the_layer_on_top_alone(ui):  # noqa: F811
    await series_in_browser(ui, (1,))
    page = ui.page
    await page.get_by_role("button", name="Настройки").click()
    await page.locator(f".st-tab[title='{text('settings.tab.voices')}']").click()
    combo = page.get_by_role("combobox").first
    await combo.focus()
    await page.keyboard.press("Enter")
    await expect(page.get_by_role("listbox")).to_be_visible()
    await page.keyboard.press("Escape")
    await expect(page.get_by_role("listbox")).to_have_count(0)
    await expect(page.locator(".modern-modal")).to_be_visible()  # the list went, the window stays
    await page.keyboard.press("Escape")
    await expect(page.locator(".modern-modal")).to_have_count(0)


async def test_a_list_that_drops_is_worked_with_the_keys(ui):  # noqa: F811
    await series_in_browser(ui, (1,))
    page = ui.page
    await page.get_by_role("button", name="Настройки").click()
    await page.locator(f".st-tab[title='{text('settings.tab.voices')}']").click()
    combo = page.get_by_role("combobox").first
    assert await combo.get_attribute("tabindex") == "0"  # reached by Tab at all
    await combo.focus()
    before = await combo.inner_text()
    await page.keyboard.press("ArrowDown")
    options = page.get_by_role("option")
    assert await options.count() >= 2
    await page.keyboard.press("ArrowDown")
    await page.keyboard.press("Enter")
    await expect(page.get_by_role("listbox")).to_have_count(0)
    assert await combo.inner_text() != before, "the option walked to is the one taken"


async def test_a_window_with_typed_data_asks_before_it_is_closed(ui):  # noqa: F811
    await series_in_browser(ui, (1,))
    page = ui.page
    await page.get_by_role("button", name=text("header.addSeries")).click()
    name = page.get_by_label("URL для парсинга")
    await name.fill("https://example.invalid/typed")
    await page.keyboard.press("Escape")
    ask = page.get_by_role("dialog", name=text("window.discard.title"))
    await expect(ask).to_be_visible()
    await page.keyboard.press("Escape")  # the question is on top: Escape answers it "no"
    await expect(ask).to_have_count(0)
    await expect(name).to_have_value("https://example.invalid/typed")
    await page.keyboard.press("Escape")
    await page.get_by_role("button", name=text("common.confirm")).click()
    await expect(page.locator(".modern-modal")).to_have_count(0)
