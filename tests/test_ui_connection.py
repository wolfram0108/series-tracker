"""What the window shows while the service is away or has sent the user away, the whole application on the stand (tests/ui_stand.py):
a stream that broke is told in a strip and its return once; an action that found no service says so in words, not in the text of an
exception; a list that could not be read is no "no series" and is read again by its button; a session that ended while the window was
open brings the login, not a screen that stopped changing."""
from __future__ import annotations

import pytest
from playwright.async_api import expect

from test_ui_stand import series_in_browser
from ui_stand import UiStand, text, ui  # noqa: F401 - the fixture

pytestmark = [pytest.mark.ui, pytest.mark.asyncio]


async def test_a_lost_stream_is_told_in_a_strip_and_its_return_once_and_an_action_says_it_was_not_done(ui):  # noqa: F811
    await series_in_browser(ui, (1,))
    page = ui.page
    strip = page.get_by_role("status").filter(has_text=text("connection.lost"))
    await expect(strip).to_have_count(0)
    await ui._unserve()
    await expect(strip).to_be_visible(timeout=20_000)
    await page.locator(".series-card .switch").first.click()
    toast = page.locator(".p-toast-message")
    await expect(toast.first).to_contain_text(text("common.networkFailed"))
    await expect(toast.first).to_contain_text(text("common.notDone"))
    assert "TypeError" not in await toast.first.inner_text(), "the text of an exception is no word for the user"
    await ui._serve()
    await expect(strip).to_have_count(0, timeout=40_000)
    await expect(page.get_by_text(text("connection.restored"))).to_be_visible()


async def test_a_list_that_could_not_be_read_says_so_and_is_read_again_by_its_button(ui):  # noqa: F811
    await series_in_browser(ui, (1,))
    page = ui.page
    await page.route("**/api/series", lambda route: route.abort())
    await page.reload()
    await expect(page.get_by_text(text("app.loadFailed"))).to_be_visible()
    assert await page.get_by_text(text("app.empty")).count() == 0, "a list not read is not a list of none"
    await page.unroute("**/api/series")
    await page.get_by_role("button", name=text("common.retry")).click()
    await expect(page.locator(".series-card")).to_have_count(1)


@pytest.fixture
async def locked(tmp_path):
    """The stand with the lock of the gateway on: a request without a session is answered 401."""
    made = UiStand(str(tmp_path))
    made.locked = True
    await made.start()
    await made.open()
    yield made
    await made.shut()
    await made.stop()
    made.close()


async def test_a_session_that_ended_while_the_window_was_open_brings_the_login(locked):
    page = locked.page
    await locked.first_run()
    await expect(page.locator(".empty-state")).to_be_visible()
    await page.context.clear_cookies()  # the session ends; the stream that was open is cut when the service goes and comes back
    await locked._unserve()
    await locked._serve()
    await expect(page.get_by_role("dialog", name=text("login.title"))).to_be_visible(timeout=40_000)
