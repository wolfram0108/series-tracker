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
    # Every response of the service that holds a session writes its cookie again: the cookie is cleared only after the window has
    # had every answer it asks for after the login (the scanner, the stream; the list is told by its text), or a late one gives it back.
    async with page.expect_response(lambda r: r.url.endswith("/api/scanner/status") and r.status == 200) as scanner, \
            page.expect_response(lambda r: "/api/stream" in r.url and r.status == 200) as stream:
        await locked.first_run()
    await scanner.value
    await stream.value
    await expect(page.get_by_text(text("app.empty"))).to_be_visible()
    await page.context.clear_cookies()  # the session ends; the stream that was open is cut when the service goes and comes back
    await locked._unserve()
    await locked._serve()
    await expect(page.get_by_role("dialog", name=text("login.title"))).to_be_visible(timeout=40_000)
