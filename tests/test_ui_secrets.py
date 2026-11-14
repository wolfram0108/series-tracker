"""The eye of a field of a secret in the settings (Настройки → Авторизация), the whole application on the stand (tests/ui_stand.py): a
secret that is set shows as dots in the empty field, is not given with the credentials, and the eye asks the core for it and shows it."""
from __future__ import annotations

import pytest
from playwright.async_api import expect

from ui_stand import text, ui  # noqa: F401 - the fixture

pytestmark = [pytest.mark.ui, pytest.mark.asyncio]


async def test_the_eye_of_a_secret_field_shows_the_secret_the_core_keeps_and_hides_it_again(ui):  # noqa: F811
    page = ui.page
    await ui.first_run()
    await ui.call("POST", "/api/auth", {"kinozal": {"username": "vova", "password": "k1n0-pass"}})
    asked = []
    page.on("request", lambda request: asked.append(request.url) if "/secret" in request.url else None)
    await ui.settings(text("settings.tab.auth"))
    row = page.locator(".sheet-row", has_text="Kinozal.me")
    field = row.get_by_label(text("auth.password"), exact=True)
    await expect(field).to_have_value("")
    await expect(field).to_have_attribute("placeholder", text("auth.secret.mask"))
    assert not asked  # nothing of the secret came with the window
    eye = row.locator(".password-toggle-btn")
    await eye.click()
    await expect(field).to_have_value("k1n0-pass")
    await expect(field).to_have_attribute("type", "text")
    assert len(asked) == 1 and asked[0].endswith("/api/auth/kinozal/secret")
    await eye.click()
    await expect(field).to_have_attribute("type", "password")
    # a service with no password has nothing to show and asks for nothing
    other = page.locator(".sheet-row", has_text="RuTracker.org").get_by_label(text("auth.password"), exact=True)
    await page.locator(".sheet-row", has_text="RuTracker.org").locator(".password-toggle-btn").click()
    await expect(other).to_have_attribute("type", "text")
    await expect(other).to_have_value("")
    assert len(asked) == 1
