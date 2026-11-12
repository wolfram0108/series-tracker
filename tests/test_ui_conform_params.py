"""The alignment server in the settings (Настройки → Отладка): the address and the work folder, and how the daemon is
asked to align — its audio method, the drift speed, the checkpoints, the cuts filled with the reference — set in the
browser and carried by the next job the daemon is sent. The whole application on the stand (tests/ui_stand.py), the
daemon a model of the real one (tests/plan_disk.py)."""
from __future__ import annotations

import pytest
from playwright.async_api import expect

from test_ui_stand import series_in_browser
from ui_stand import SERIES, text, ui  # noqa: F401 - the fixture

pytestmark = [pytest.mark.ui, pytest.mark.asyncio]


async def test_the_parameters_set_in_the_debug_tab_go_with_the_next_job_to_the_daemon(ui):  # noqa: F811
    series_id = await series_in_browser(ui, (1,))
    page = ui.page
    await ui.settings("Отладка")
    block = page.locator(".modern-fieldset", has_text=text("conform.settings.title"))
    # What the daemon accepts and what the service asked before: MuQ, the daemon's drift, the cuts filled.
    await expect(block.get_by_text(text("agents.conform.daemon.up"))).to_be_visible()
    drift = block.get_by_label(text("conform.params.drift"))
    await expect(drift).to_have_value("1.25")
    await expect(block.locator(".conform-range")).to_have_text("0.25–10")

    await block.get_by_role("button", name=text("conform.params.method.band")).click()
    await drift.fill("2,5")
    await drift.press("Enter")
    await expect(drift).to_have_value("2.5")
    await block.locator(".conform-switch", has_text=text("conform.params.keepTmp")).locator(".p-toggleswitch").click()
    await block.locator(".conform-switch", has_text=text("conform.params.fill")).locator(".p-toggleswitch").click()
    await block.get_by_role("button", name=text("conform.params.edges.cut")).click()
    await expect(block.get_by_role("button", name=text("conform.params.edges.cut"))).to_have_attribute(
        "aria-pressed", "true")
    print("STATE", await block.locator(".p-togglebutton").evaluate_all("bs => bs.map(b => [b.textContent.trim(), b.getAttribute('aria-pressed'), b.className, getComputedStyle(b.querySelector('.p-togglebutton-content') || b).backgroundColor])"))
    await ui.shot("43-conform-params")
    await ui.close_settings()

    await ui.open_output(SERIES)
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(1)
    # The core holds every change, the last one too: none was taken back by the answer to an earlier one.
    assert (await ui.call("GET", "/api/conform/settings"))["params"] == {
        "audio_method": "band", "drift_speed_pct": 2.5, "keep_tmp": True, "fill_silence": False, "extend_edges": False}
    sent = ui.daemon.bodies[-1]
    assert (sent["audio_method"], sent["drift_speed_pct"], sent["keep_tmp"], sent["fill_silence"]) == (
        "band", 2.5, True, False), sent
