"""The window of the extensions in the browser, the whole application on the stand (tests/ui_stand.py): the tab of the provider of
external sources is the line of its address until an address is named, takes a provider at once and tells what it says (the state of
the socket, its applications), keeps its token a secret, and every tab of the window holds its layout at every width: the caption of a
row is above its body and nothing runs out of the window or over another field. The provider is a model of the
socket on real HTTP (tests/provider_mock.py)."""
from __future__ import annotations

import pytest
from playwright.async_api import expect

from provider_mock import MockProvider, serving
from ui_stand import text, ui  # noqa: F401 - the fixture

pytestmark = [pytest.mark.ui, pytest.mark.asyncio]

# The widths of the window the layout is walked at, in steps of 10 px: from a phone up.
WIDTHS = range(320, 1290, 10)

# What a layout may not do, asked of the page: a body wider than its window, a field over another field of its row, a tile
# or a pill whose text runs out of it.
DETECT = """() => {
  const body = document.querySelector('.modern-body');
  const problems = [];
  if (body.scrollWidth > body.clientWidth + 1) {
    const edge = body.getBoundingClientRect().right;
    const wide = [...body.querySelectorAll('*')].find((each) => each.getBoundingClientRect().right > edge + 1);
    problems.push('the body is wider than the window: ' + body.scrollWidth + ' > ' + body.clientWidth + ' by ' +
      (wide ? wide.tagName + '.' + wide.className + ' «' + wide.textContent.slice(0, 40) + '»' : '?') +
      (wide ? ' in ' + (() => { const way = []; for (let x = wide; x && x !== body; x = x.parentElement) way.push(x.className.toString().split(' ')[0] + '=' + Math.round(x.getBoundingClientRect().width)); return way.join(' < '); })() : ''));
  }
  for (const row of document.querySelectorAll('.sheet-row')) {
    const boxes = [...row.querySelectorAll('.input-constructor-group, .tile')].map((each) => each.getBoundingClientRect());
    boxes.forEach((a, i) => boxes.slice(i + 1).forEach((b) => {
      if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) problems.push('two fields overlap');
    }));
    const caption = row.querySelector('.sheet-caption').getBoundingClientRect(), place = row.querySelector('.sheet-body').getBoundingClientRect();
    if (caption.bottom > place.top + 1) problems.push('the caption is not above its body');
  }
  for (const each of document.querySelectorAll('.tile-title, .pill')) {
    if (each.scrollWidth > each.clientWidth + 1 && getComputedStyle(each).overflow !== 'visible') problems.push('text is cut: ' + each.textContent);
  }
  return problems;
}"""

# What the head of a window (the shell, components/ModalShell.vue) may not do: break a word of its title, push its close button out
# of it, or run wider than the window.
DETECT_HEAD = """() => {
  const problems = [];
  for (const bar of document.querySelectorAll('.modern-header')) {
    const title = bar.querySelector('.modal-title'), name = title.textContent.trim();
    const line = parseFloat(getComputedStyle(title).lineHeight);
    if (name.split(/\\s+/).length === 1 && title.getBoundingClientRect().height > line * 1.5) problems.push('the title is broken inside its word: ' + name);
    const edge = bar.getBoundingClientRect(), close = bar.querySelector('.modern-close').getBoundingClientRect();
    if (close.right > edge.right + 1 || close.left < edge.left - 1) problems.push('the close button is out of the head of ' + name);
    if (bar.scrollWidth > bar.clientWidth + 1) problems.push('the head runs wider than its window: ' + name);
  }
  return problems;
}"""


async def _no_provider(ui) -> None:
    """The stand may carry a provider of its own (the real one over models, when it is on the machine): the scenarios name theirs."""
    saved = await ui.call("PUT", "/api/provider", {"url": "", "token": ""})
    assert saved["saved"] is True and saved["provider"]["state"] == "none"


def _provider() -> MockProvider:
    mock = MockProvider()
    mock.add_site("mock", "Мок (Mock)")
    mock.title("mock", 7, ["名"], [("1", "X", "tok 1", [1080]), ("2", "Y", "tok 2", [720])])
    return mock


async def test_the_provider_tab_is_the_line_of_its_address_until_one_is_named_and_then_tells_what_the_provider_says(ui):  # noqa: F811
    page = ui.page
    await ui.first_run()
    await _no_provider(ui)
    await ui.extensions(text("extensions.tab.chines"))
    address = page.get_by_label(text("extensions.chines.address"))
    await expect(address).to_be_visible()
    await expect(page.locator(".sheet-row")).to_have_count(1)  # the line of the address and nothing else
    await expect(page.locator(".tile")).to_have_count(0)
    await ui.shot("50-provider-no-address")

    async with serving(_provider()) as server:
        await address.fill(server.url)
        await address.press("Enter")
        await expect(page.get_by_text(text("extensions.chines.up"))).to_be_visible()
        await expect(page.locator(".tile")).to_have_count(1)
        await expect(page.locator(".tile")).to_contain_text("Мок (Mock)")
        await expect(page.get_by_text(text("extensions.chines.protocol", protocol=2))).to_be_visible()
        await ui.shot("51-provider-connected")
        assert (await ui.call("GET", "/api/provider"))["url"] == server.url

        # The window is another window to the same core: after a reload the address is the one the core holds.
        await ui.close_extensions()
        await page.reload()
        await ui.extensions(text("extensions.tab.chines"))
        await expect(page.get_by_label(text("extensions.chines.address"))).to_have_value(server.url)
        await expect(page.get_by_text(text("extensions.chines.up"))).to_be_visible()

        # The provider falls: the window says so at once, by the push of the core, and keeps the applications of its last manifest.
        await server.down()
        await expect(page.locator(".pill-danger")).to_be_visible()
        await expect(page.locator(".tile-grid.is-stale .tile")).to_have_count(1)
        await ui.shot("52-provider-down")
        await server.up()
        await expect(page.get_by_text(text("extensions.chines.up"))).to_be_visible()


async def test_the_token_of_the_provider_is_taken_and_never_given_back(ui):  # noqa: F811
    page = ui.page
    await ui.first_run()
    await _no_provider(ui)
    async with serving(_provider()) as server:
        await ui.extensions(text("extensions.tab.chines"))
        await page.get_by_label(text("extensions.chines.address")).fill(server.url)
        await page.get_by_label(text("extensions.chines.address")).press("Enter")
        token = page.get_by_label(text("extensions.chines.token"), exact=True)
        await token.fill("s3cret")
        await token.press("Enter")
        try:
            await expect(page.get_by_role("button", name=text("extensions.chines.tokenClear"))).to_be_visible()
        except AssertionError:
            # What the window said (a toast) and what the core holds, so that a red run names its cause.
            raise AssertionError((await page.locator(".p-toast").inner_text(), await ui.call("GET", "/api/provider")))
        await expect(token).to_have_value("")  # what was typed is forgotten by the screen
        view = await ui.call("GET", "/api/provider")
        assert view["has_token"] is True and "s3cret" not in str(view)
        await page.get_by_role("button", name=text("extensions.chines.tokenClear")).click()
        await expect(page.get_by_role("button", name=text("extensions.chines.tokenClear"))).to_have_count(0)
        assert (await ui.call("GET", "/api/provider"))["has_token"] is False


async def test_every_tab_of_the_extensions_holds_its_layout_at_every_width(ui):  # noqa: F811
    page = ui.page
    await ui.first_run()
    await _no_provider(ui)
    async with serving(_provider()) as server:
        await ui.extensions(text("extensions.tab.chines"))
        await page.get_by_label(text("extensions.chines.address")).fill(server.url)
        await page.get_by_label(text("extensions.chines.address")).press("Enter")
        await expect(page.locator(".tile")).to_have_count(1)
        walked = {}
        for tab in (text("extensions.tab.chines"), text("extensions.tab.conform")):
            await page.locator(f".st-tab[title='{tab}']").click()
            if tab == text("extensions.tab.conform"):
                # The tab of the alignment server shows the rest with its address: name one.
                address = page.get_by_label(text("conform.settings.url"))
                await address.fill(ui.daemon_http.url)
                await address.press("Tab")
            await expect(page.locator(".sheet-row").first).to_be_visible()
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                found = await page.evaluate(DETECT)
                assert found == [], (tab, width, found)
            walked[tab] = len(WIDTHS)
            await ui.shot_of(page.locator(".modern-modal"), f"53-extensions-{tab}")
        await page.set_viewport_size({"width": 390, "height": 900})
        await ui.shot_of(page.locator(".modern-modal"), "54-extensions-narrow-conform")
        await page.locator(f".st-tab[title='{text('extensions.tab.chines')}']").click()
        await ui.shot_of(page.locator(".modern-modal"), "55-extensions-narrow-chines")
        assert len(walked) == 2


async def test_the_head_of_a_window_keeps_the_words_of_its_title_and_its_close_button_at_every_width(ui):  # noqa: F811
    """The title is one word of the window's own name; the tabs give way to it — tighter, then icons, then a line of their own under
    it — and the close button stays beside it, in the window of the extensions and in the window of the settings alike."""
    page = ui.page
    await ui.first_run()
    await _no_provider(ui)
    below = {}
    for name, open_window in (("extensions", lambda: ui.extensions(text("extensions.tab.chines"))),
                              ("settings", lambda: ui.settings(text("settings.tab.voices")))):
        await open_window()
        for width in WIDTHS:
            await page.set_viewport_size({"width": width, "height": 900})
            found = await page.evaluate(DETECT_HEAD)
            assert found == [], (name, width, found)
            below[(name, width)] = await page.locator(".modern-header.tabs-below").count()
        await page.set_viewport_size({"width": 390, "height": 900})
        await ui.shot_of(page.locator(".modern-header"), f"56-head-{name}-390")
        await page.set_viewport_size({"width": 1280, "height": 900})
        await ui.close_settings()
    # Wide, the tabs stand beside the title; narrow, under it — the shell moves them, as it measures.
    assert not below[("extensions", 1280)] and below[("extensions", 320)], below
