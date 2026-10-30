"""Stand browser capture facade over the production implementation."""
from __future__ import annotations

from typing import Literal

from modules.sources.players.vibix_protocol import (  # noqa: F401 - re-exported for the stand
    Capture, VibixUnavailable, capture_with_browser)


async def capture_playlist(page_url: str, *,
                           browser_name: Literal["firefox", "chromium"] = "firefox",
                           timeout_s: float = 60.0) -> Capture:
    """Open the page with a stand-owned browser and capture the Vibix playlist."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise VibixUnavailable(
            "playwright не установлен: pip install playwright && playwright install firefox"
        ) from exc

    async with async_playwright() as playwright:
        browser = await getattr(playwright, browser_name).launch(headless=True)
        try:
            return await capture_with_browser(browser, page_url, timeout_s=timeout_s)
        finally:
            await browser.close()
