"""What a screen does while the service is slow, the whole application on the stand (tests/ui_stand.py): every answer of the service is held
until the scenario lets it go. A screen that waits for its data puts no form on show to write into (the text typed meanwhile would be written
over by the data) and shows the skeleton of it in its place; an action pressed twice on such a line goes out once."""
from __future__ import annotations

import asyncio
import re

import pytest
from playwright.async_api import expect

from test_ui_stand import series_in_browser
from ui_stand import SERIES, text, ui  # noqa: F401 - the fixture

pytestmark = [pytest.mark.ui, pytest.mark.asyncio]

API = re.compile(r"/api/(?!stream)")


class Held:
    """The answers of the service held back from the moment of `hold` until `release`; what asked for them is listed in `asked`."""

    def __init__(self, page) -> None:
        self.page = page
        self.letgo = asyncio.Event()
        self.news = asyncio.Event()
        self.asked: list[str] = []

    async def _hold(self, route) -> None:
        if route.request.method != "GET":
            self.asked.append(f"{route.request.method} {re.sub(r'.*/api', '/api', route.request.url.split('?')[0])}")
            self.news.set()
        await self.letgo.wait()
        try:
            await route.continue_()
        except Exception:  # noqa: BLE001 - a page that went away meanwhile
            pass

    async def hold(self) -> None:
        self.letgo.clear()
        self.asked.clear()
        await self.page.route(API, self._hold)

    def of(self, ending: str) -> list[str]:
        return [each for each in self.asked if ending in each]

    async def first(self, ending: str) -> None:
        """Waits for the signal of the held route that a request ending so has been asked (the page gives it in a moment)."""
        while not self.of(ending):
            self.news.clear()
            await asyncio.wait_for(self.news.wait(), 10)

    async def release(self) -> None:
        self.letgo.set()
        await self.page.unroute(API, self._hold)


SCREENS = [  # the button that opens the window, its tab, what is on the tab once it has been read
    ("Настройки", "Авторизация", "input.item-input"),
    ("Настройки", "Источники", "[role=combobox]"),
    ("Настройки", "Озвучки", "[role=combobox]"),
    ("Настройки", "Отладка", "input.item-input"),
    (None, "Чинес", "input.item-input"),  # the extensions: their own button
    (None, "Conform", "input.item-input"),
]


async def test_a_screen_that_waits_for_its_data_puts_no_form_on_show_until_it_has_it(ui):  # noqa: F811
    await series_in_browser(ui, (1,))
    page = ui.page
    held = Held(page)
    for opener, tab, form in SCREENS:
        button = opener or text("header.extensions")
        await held.hold()
        try:
            await page.get_by_role("button", name=button).click()
            await page.locator(f".st-tab[title='{tab}']").click()
            body = page.locator(".modern-body")
            await expect(body.locator(".load-skeleton")).to_have_count(1)
            assert await body.locator("input:not([type=hidden]), textarea, [role=combobox]").count() == 0, f"{tab}: a form before its data"
        finally:
            await held.release()
        await expect(body.locator(form).first).to_be_visible(timeout=15_000)
        await expect(body.locator(".load-skeleton")).to_have_count(0)
        await page.keyboard.press("Escape")
        await expect(page.locator(".modern-modal")).to_have_count(0)


async def test_an_action_pressed_twice_on_a_slow_line_goes_out_once(ui):  # noqa: F811
    await series_in_browser(ui, (1,))
    page = ui.page
    held = Held(page)

    # the sync of a series, from its card
    card = page.locator(".series-card", has_text=SERIES).first
    await expect(page.locator(".p-toast-message")).to_have_count(0, timeout=15_000)  # the notices of making the series have gone
    await held.hold()
    await card.locator(".btn-scan").dblclick(delay=30)
    await held.first("/scan")
    await held.release()
    ended = page.locator(".p-toast-message", has_text=re.compile(f"{text('app.sync.done')}|{text('app.sync.noUpdates')}"))
    await expect(ended).to_be_visible(timeout=60_000)  # the run has ended: its end is told
    assert len(held.of("/scan")) == 1, held.asked
    await expect(page.locator(".p-toast-message")).to_have_count(0, timeout=15_000)

    # the sync of all, from the debug tab
    await page.get_by_role("button", name="Настройки").click()
    await page.locator(".st-tab[title='Отладка']").click()
    everything = page.get_by_role("button", name=text("debug.syncAll.button"))
    await expect(everything).to_be_enabled(timeout=15_000)
    await held.hold()
    await everything.dblclick(delay=30)
    await held.first("scan_all")
    await expect(everything).to_have_class(re.compile("p-button-loading"))  # the button says that the press has been taken
    await held.release()
    await expect(everything).not_to_have_class(re.compile("p-button-loading"), timeout=60_000)
    assert len(held.of("scan_all")) == 1, held.asked
