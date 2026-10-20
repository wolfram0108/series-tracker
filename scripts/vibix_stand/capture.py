"""Capture the signed Vibix playlist API response through a real browser."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

API_PATH = "/api/v1/embed-serials/"
_BLOCKED_HOSTS = (
    "googlesyndication.com",
    "doubleclick.net",
    "adfinity.pro",
    "umami.is",
    "mc.yandex.ru",
    "google-analytics.com",
)


class VibixUnavailable(RuntimeError):
    """Raised when a page has no Vibix player or the API never responds."""


@dataclass(frozen=True)
class Capture:
    page_url: str
    api_url: str
    body: bytes


async def capture_playlist(page_url: str, *, browser_name: Literal["firefox", "chromium"] = "firefox",
                           timeout_s: float = 60.0) -> Capture:
    """Open an episode page, select Vibix, and return the encrypted API response."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise VibixUnavailable(
            "playwright не установлен: pip install playwright && playwright install firefox"
        ) from exc

    async with async_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        browser = await browser_type.launch(headless=True)
        context = await browser.new_context(locale="ru-RU", viewport={"width": 1366, "height": 900})
        await context.route("**/*", _block_ads)
        page = await context.new_page()
        response_future: asyncio.Future = asyncio.get_running_loop().create_future()

        def on_response(response) -> None:
            if API_PATH in response.url and not response_future.done():
                response_future.set_result(response)

        page.on("response", on_response)
        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
            try:
                button = await page.wait_for_selector(
                    '.source-btn[data-player="vibix"]', timeout=15_000
                )
            except Exception as exc:
                raise VibixUnavailable("на странице нет кнопки Vibix") from exc
            await button.click(timeout=15_000)
            response = await asyncio.wait_for(response_future, timeout=timeout_s)
            body = await response.body()
            return Capture(page_url=page_url, api_url=response.url, body=body)
        except asyncio.TimeoutError as exc:
            raise VibixUnavailable(
                "Vibix API не ответил за отведённое время; проверьте страницу и браузер"
            ) from exc
        finally:
            await browser.close()


async def _block_ads(route) -> None:
    url = route.request.url
    if any(host in url for host in _BLOCKED_HOSTS):
        await route.abort()
    else:
        await route.continue_()
