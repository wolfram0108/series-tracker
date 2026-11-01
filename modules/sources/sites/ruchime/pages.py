"""Reading of the pages of 1.ruchime.org: the release, the list of its episodes, one episode page and the players it shows.

The gap between requests, the repeats and the waits a host asks for belong to the policy (`AdaptivePacer`, `core.retry`);
nothing here sleeps or repeats on its own account.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlparse

import httpx

from core.reasons import reason
from core.retry import UNTIMED, Budget, retrying

from ...players.contract import Player, PlayerRef
from ...policy import AdaptivePacer

logger = logging.getLogger("sources")

SITE_ROOT = "https://1.ruchime.org/"
# Bound concurrent page requests to respect the host limits and the connection pool.
MAX_CONCURRENT_PAGES = 4
# The gap between page requests is a limit of the host (measured in decision R-33), not a way to wait for it.
PAGE_INTERVAL_S = 0.6
# The pause of everybody after a refusal that names none.
PAGE_COOLDOWN_S = 20.0
PAGE_BUDGET = Budget(attempts=4, pauses=(1.0, 3.0, 10.0), limit_s=UNTIMED)
RELEASE_BUDGET = Budget(attempts=3, pauses=(0.5, 1.0), limit_s=UNTIMED)
REQUEST_TIMEOUT = 8.0
RELEASE_TIMEOUT = 15.0
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

_MONTHS = {
    "янв": "01", "фев": "02", "мар": "03", "апр": "04", "мая": "05",
    "июн": "06", "июл": "07", "авг": "08", "сен": "09", "окт": "10",
    "ноя": "11", "дек": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10",
    "nov": "11", "dec": "12",
}
_VOICE = re.compile(r'info-meta-key">\s*Озвучка\s*</span>\s*<span[^>]*class="info-meta-val"[^>]*>\s*(.*?)\s*</span>',
                    re.DOTALL)


def extract_slug(url: str) -> str:
    match = re.search(r"/release/([^/?#]+)", urlparse(url).path)
    if not match:
        raise ValueError(f"Failed to extract release slug from URL: {url}")
    return match.group(1)


def published_of(raw_date: str | None) -> str | None:
    """'09 May 2026' -> '2026-05-09': the site gives a day and no time, and no time is made up."""
    parts = (raw_date or "").strip().split()
    if len(parts) == 3:
        day, mon, year = parts
        month = _MONTHS.get(mon.lower()[:3])
        if month and day.isdigit() and year.isdigit():
            return f"{year}-{month}-{day.zfill(2)}"
    return None


def release_names(raw_text: str, slug: str) -> list[str]:
    """The names of the release as the site writes them: the main one, then the original when the site has it."""
    name_ru: str | None = None
    name_en: str | None = None
    for match in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', raw_text, re.DOTALL):
        try:
            data = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "TVSeries":
            name_ru = data.get("name")
            name_en = data.get("alternateName")
            break
    if not name_ru:
        title = re.search(r"<title>(.*?)(?:—\s*Ruchime.*)?</title>", raw_text)
        name_ru = re.sub(r"\s+\d+\s+серия.*", "", title.group(1)).strip() if title else slug.replace("-", " ").title()
    return [name for name in (name_ru, name_en) if name]


def release_voice(raw_text: str) -> str:
    """The voice-over the site names on the page of the release (a line of the info block); empty when it names none."""
    found = _VOICE.search(raw_text)
    return html.unescape(re.sub(r"<[^>]+>", "", found.group(1))).strip() if found else ""


def release_episodes(raw_text: str) -> list[dict]:
    match = re.search(r"const ALL_EPS\s*=\s*(\[.*?\]);", raw_text, re.DOTALL)
    if not match:
        return []
    try:
        episodes = json.loads(match.group(1))
    except ValueError as exc:
        logger.warning("Failed parsing ALL_EPS JSON: %s", exc)
        return []
    return episodes if isinstance(episodes, list) else []


def retry_after(response: httpx.Response | None) -> float | None:
    try:
        value = response.headers.get("Retry-After") if response is not None else None
        return float(value) if value else None
    except (AttributeError, TypeError, ValueError):
        return None


def episode_url(slug: str, entry: dict) -> str | None:
    """The address of the page of a list entry; the site names its pages by its own number, which is an address here."""
    number = entry.get("number")
    return f"https://1.ruchime.org/release/{slug}/episode-{number}" if number is not None else None


@dataclass
class EpisodePage:
    """What one page of an episode shows: the players it names, or why the page could not be read."""
    url: str
    refs: dict[str, PlayerRef] = field(default_factory=dict)
    failure: str | None = None


class PageReader:
    """The one reader of the pages of the site; its pacer is shared by every request to the host."""

    def __init__(self) -> None:
        self.pacer = AdaptivePacer(PAGE_INTERVAL_S)

    async def read_release(self, slug: str, http: httpx.AsyncClient) -> httpx.Response:
        """The release page; raises the transport error or an error status once the budget is spent."""
        url = f"https://1.ruchime.org/release/{slug}"

        async def ask() -> httpx.Response | Exception:
            try:
                return await http.get(url, headers=HEADERS, timeout=RELEASE_TIMEOUT, follow_redirects=True)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                return exc

        answer, _attempts, _exhausted = await retrying(
            ask, final=lambda got: isinstance(got, httpx.Response) and got.status_code == 200, budget=RELEASE_BUDGET)
        if isinstance(answer, Exception):
            raise answer
        answer.raise_for_status()
        return answer

    async def read_episode_page(self, http: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> httpx.Response | None:
        """One episode page: asked again after a refusal or a failure, every request spaced by the pacer; None when
        nothing came."""
        async def ask() -> httpx.Response | None:
            await self.pacer.wait()
            async with sem:
                try:
                    response = await http.get(url, headers=HEADERS, follow_redirects=True, timeout=REQUEST_TIMEOUT)
                except Exception as exc:  # noqa: BLE001 - asked again below
                    logger.warning("Episode page %s failed: %s", url, exc)
                    return None
            if response.status_code == 429:
                held = retry_after(response) or PAGE_COOLDOWN_S
                self.pacer.slow_down(hold_s=held)
                logger.warning("Episode page %s rate-limited; every request waits %.0f s", url, held)
            return response

        response, _attempts, _exhausted = await retrying(
            ask, final=lambda got: got is not None and got.status_code != 429 and got.status_code < 500,
            budget=PAGE_BUDGET, retry_after=retry_after)
        return response

    async def read_episode(self, url: str, http: httpx.AsyncClient, sem: asyncio.Semaphore,
                           players: Iterable[Player]) -> EpisodePage:
        """The page of an episode, read once, and the players it shows: every player of the registry that finds itself."""
        page = EpisodePage(url)
        response = await self.read_episode_page(http, url, sem)
        if response is None:
            page.failure = reason("page_unreachable")
        elif response.status_code != 200:
            page.failure = reason("page_status", status=response.status_code)
        else:
            text = html.unescape(response.text)
            for player in players:
                ref = player.find(text, url)
                if ref is not None:
                    page.refs[player.name] = ref
        return page
