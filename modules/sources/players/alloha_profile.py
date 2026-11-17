"""Alloha's browser identity: capture from the real player, validate through the native client, then publish and save.

One refresh belongs to the player, not to a download. Waiters share it, including when one is cancelled. A generation
counts successful validations even when Chromium produces the same fingerprint. The encrypted cache survives restart;
the environment is only the initial fallback. No signed media URL or WebSocket token is persisted here.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from core import crypto, storage
from core.reasons import reason

from .alloha_session import AllohaRefused, Profile, profile_of

CAPTURE_TIMEOUT_S = 40.0
REFRESH_TIMEOUT_S = 120.0
REFRESH_COOLDOWN_S = 60.0
logger = logging.getLogger("sources")
Capture = Callable[[str], Awaitable[Profile]]
Verify = Callable[[Profile], Awaitable[None]]


def _read(path: Path) -> Profile | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    if not crypto.is_encrypted(raw):
        raise ValueError("profile cache is not encrypted")
    value = json.loads(crypto.decrypt(raw))
    if not isinstance(value, dict) or not all(isinstance(value.get(key), str) for key in ("fingerprint", "user_agent")):
        raise ValueError("invalid profile cache")
    return profile_of({"ST_ALLOHA_FINGERPRINT": value["fingerprint"], "ST_ALLOHA_USER_AGENT": value["user_agent"]})


def _write(path: Path, profile: Profile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = crypto.encrypt(json.dumps(asdict(profile)))
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def capture_profile(browser_getter, iframe: str, referer: str) -> Profile:
    """Let the iframe's own JS make its file request. Record the exact Borth identity and its actual User-Agent.

    The real iframe is embedded in a minimal local response at the site's address, so the browser sends iframe fetch
    metadata and the correct Referer. A top-level navigation is refused even with a copied Referer. The player JS is
    unmodified; no site-specific episode buttons or JS evaluation are needed. The caller validates on the native CDN.
    """
    browser = await browser_getter()
    context = await browser.new_context(locale="ru-RU", viewport={"width": 1280, "height": 720}, service_workers="block")
    try:
        page = await context.new_page()
        origin = urlsplit(iframe)

        def files(request) -> bool:
            parts = urlsplit(request.url)
            return (request.method == "POST" and (parts.scheme, parts.netloc) == (origin.scheme, origin.netloc)
                    and parts.path.startswith("/bnsi/movies/"))

        async def embed(route) -> None:
            await route.fulfill(content_type="text/html", body=(
                '<!doctype html><iframe allow="autoplay; fullscreen" style="width:100%;height:700px" src="'
                + html.escape(iframe, quote=True) + '"></iframe>'))

        async with asyncio.timeout(CAPTURE_TIMEOUT_S):
            await page.route(referer, embed)
            async with page.expect_request(files, timeout=CAPTURE_TIMEOUT_S * 1000) as asked:
                await page.goto(referer, wait_until="domcontentloaded", timeout=CAPTURE_TIMEOUT_S * 1000)
            headers = await (await asked.value).all_headers()
        profile = profile_of({"ST_ALLOHA_FINGERPRINT": headers.get("borth", "").partition("|")[0],
                              "ST_ALLOHA_USER_AGENT": headers.get("user-agent", "")})
        if profile is None:
            raise ValueError("missing browser identity")
        return profile
    finally:
        await context.close()


class Profiles:
    def __init__(self, initial: Profile | None = None) -> None:
        self.profile, self.generation = initial, 0
        self.path: Path | None = None
        self.capture: Capture | None = None
        self._loaded = False
        self._loading = asyncio.Lock()
        self._refresh: asyncio.Task | None = None
        self._not_before = 0.0
        self._closed = False

    async def load(self) -> None:
        async with self._loading:
            if self._loaded:
                return
            if self.path is not None:
                try:
                    saved = await storage.call(_read, self.path)
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    logger.warning("Alloha: сохранённый профиль не прочитан (%s); используется начальный", type(exc).__name__)
                else:
                    if saved is not None:
                        self.profile = saved
            self._loaded = True

    async def ensure(self, url: str, verify: Verify) -> tuple[Profile, int]:
        await self.load()
        if self.profile is None:
            await self.refresh(self.generation, url, verify)
        return self.profile, self.generation

    async def refresh(self, rejected: int, url: str, verify: Verify) -> None:
        if self._closed:
            raise AllohaRefused(reason("alloha_profile_refresh_stopped"))
        if rejected != self.generation and self.profile is not None:
            return  # another download already validated the replacement of this generation
        if self.capture is None:
            raise AllohaRefused(reason("alloha_no_profile"))
        if self._refresh is None:
            if time.monotonic() < self._not_before:
                raise AllohaRefused(reason("alloha_profile_refresh_cooldown"))
            self._not_before = time.monotonic() + REFRESH_COOLDOWN_S
            self._refresh = asyncio.create_task(self._renew(rejected, url, verify), name="alloha.profile.refresh")
            self._refresh.add_done_callback(self._finished)
        await asyncio.shield(self._refresh)

    def _finished(self, task: asyncio.Task) -> None:
        if self._refresh is task:
            self._refresh = None
        if not task.cancelled():
            task.exception()  # the owner may have been cancelled while other downloads still need this refresh

    async def _renew(self, rejected: int, url: str, verify: Verify) -> None:
        logger.info("Alloha: обновление профиля клиента через Chromium")
        try:
            async with asyncio.timeout(REFRESH_TIMEOUT_S):
                candidate = await self.capture(url)
                await verify(candidate)
                if rejected != self.generation:
                    return
                if self.path is not None:
                    await storage.call(_write, self.path, candidate, owns=str(self.path))
                self.profile = candidate
                self.generation += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Playwright exceptions can carry the iframe URL, headers and script data. Keep only the exception class.
            logger.warning("Alloha: обновление профиля не прошло проверку (%s)", type(exc).__name__)
            raise AllohaRefused(reason("alloha_profile_refresh_failed", cause=type(exc).__name__)) from None
        logger.info("Alloha: профиль проверен на CDN и обновлён")

    async def close(self) -> None:
        self._closed = True
        if self._refresh is not None:
            task = self._refresh
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
