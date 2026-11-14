"""The limits of the downloads: three numbers in the settings (Настройки → Отладка) and the one that is the provider's in the
extensions (Расширения → Чинес), all of the module of the downloads and shown, changed and kept by the whole application on the stand (tests/ui_stand.py); a value that is no whole number from 1
up is refused and the field shows the limit kept."""
from __future__ import annotations

import pytest
from playwright.async_api import expect

from ui_stand import text, ui  # noqa: F401 - the fixture

pytestmark = [pytest.mark.ui, pytest.mark.asyncio]


async def test_the_limits_of_the_downloads_are_shown_changed_and_kept(ui):  # noqa: F811
    page = ui.page
    await ui.first_run()
    await ui.settings("Отладка")
    total = page.locator(".num-field", has_text="Потоков на все многопоточные загрузки").locator("input")
    per_file = page.locator(".num-field", has_text="Потоков на одну многопоточную загрузку").locator("input")
    single = page.locator(".num-field", has_text="Однопоточных загрузок одновременно").locator("input")
    await expect(total).to_have_value("32")
    await expect(per_file).to_have_value("16")
    await expect(single).to_have_value("4")
    await expect(page.get_by_text("Многопоточных одновременно: 2")).to_be_visible()

    await total.fill("48")
    await total.blur()
    await expect(page.get_by_text("Многопоточных одновременно: 3")).to_be_visible()
    await single.fill("0")
    await single.blur()
    await expect(single).to_have_value("4")

    await ui.close_settings()
    # The limit of the provider is on its tab, which shows the rest once an address is named.
    await ui.extensions(text("extensions.tab.chines"))
    await page.get_by_label(text("extensions.chines.address")).fill("http://127.0.0.1:9")
    await page.get_by_label(text("extensions.chines.address")).press("Enter")
    provider = page.get_by_label(text("extensions.chines.atOnce"))
    await expect(provider).to_have_value("2")
    await provider.fill("3")
    await provider.blur()
    await ui.close_extensions()

    await page.reload()
    await ui.settings("Отладка")
    await expect(total).to_have_value("48")
    await expect(page.get_by_text("Многопоточных одновременно: 3")).to_be_visible()
    await ui.close_settings()
    await ui.extensions(text("extensions.tab.chines"))
    await expect(page.get_by_label(text("extensions.chines.atOnce"))).to_have_value("3")
