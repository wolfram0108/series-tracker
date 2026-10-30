"""Reading of the pages of 1.ruchime.org: the release, the list of its episodes, one episode page, the players it shows.

The gap between requests, the repeats and the waits a host asks for belong to the policy (`AdaptivePacer`, `core.retry`);
nothing here sleeps or repeats on its own account.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from urllib.parse import urlparse

import httpx

from core.reasons import reason
from core.retry import UNTIMED, Budget, retrying

from ...policy import AdaptivePacer
from ..players import PLAYER_ORDER
from ..table import Row

logger = logging.getLogger("sources.video.ruchime")

SITE_ROOT = "https://1.ruchime.org/"
# Bound concurrent page requests to respect the host limits and the connection pool.
MAX_CONCURRENT_PAGES = 4
PAGE_INTERVAL_S = 0.6
# The pause of everybody after a refusal that names none.
PAGE_COOLDOWN_S = 20.0
PAGE_BUDGET = Budget(attempts=4, pauses=(1.0, 3.0, 10.0), limit_s=UNTIMED)
RELEASE_BUDGET = Budget(attempts=3, pauses=(0.5, 1.0), limit_s=UNTIMED)
REQUEST_TIMEOUT = 8.0
RELEASE_TIMEOUT = 15.0

_MONTHS = {
    "янв": "01", "фев": "02", "мар": "03", "апр": "04", "мая": "05",
    "июн": "06", "июл": "07", "авг": "08", "сен": "09", "окт": "10",
    "ноя": "11", "дек": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10",
    "nov": "11", "dec": "12",
}


def extract_slug(url: str) -> str:
    match = re.search(r"/release/([^/?#]+)", urlparse(url).path)
    if not match:
        raise ValueError(f"Failed to extract release slug from URL: {url}")
    return match.group(1)


def parse_date(raw_date: str | None) -> str:
    # Deterministic date parsing to keep media_unique_id stable across scans.
    default_date = "1970-01-01T12:00:00Z"
    if not raw_date:
        return default_date
    parts = raw_date.strip().split()
    if len(parts) == 3:
        day, mon, year = parts
        month = _MONTHS.get(mon.lower()[:3])
        if month and day.isdigit() and year.isdigit():
            return f"{year}-{month}-{day.zfill(2)}T12:00:00Z"
    return default_date


def episode_title(series_name: str, ep: dict) -> str:
    num = ep.get("number")
    c_from, c_to = ep.get("compilation_from"), ep.get("compilation_to")
    label = f"{c_from}-{c_to} серия" if c_from is not None and c_to is not None else f"{num} серия"
    return f"{series_name} - {label}"


def release_names(raw_text: str, slug: str) -> tuple[str | None, str | None]:
    name_ru: str | None = None
    name_en: str | None = None
    for match in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', raw_text, re.DOTALL):
        try:
            data = json.loads(match.group(1))
        except Exception:
            continue
        if data.get("@type") == "TVSeries":
            name_ru = data.get("name")
            name_en = data.get("alternateName")
            break
    if not name_ru:
        title = re.search(r"<title>(.*?)(?:—\s*Ruchime.*)?</title>", raw_text)
        if title:
            name_ru = re.sub(r"\s+\d+\s+серия.*", "", title.group(1)).strip()
        else:
            name_ru = slug.replace("-", " ").title()
    return name_ru, name_en


def release_episodes(raw_text: str) -> list[dict]:
    match = re.search(r"const ALL_EPS\s*=\s*(\[.*?\]);", raw_text, re.DOTALL)
    if not match:
        return []
    try:
        episodes = json.loads(match.group(1))
    except Exception as exc:
        logger.warning("Failed parsing ALL_EPS JSON: %s", exc)
        return []
    return episodes if isinstance(episodes, list) else []


def retry_after(response: httpx.Response | None) -> float | None:
    try:
        value = response.headers.get("Retry-After") if response is not None else None
        return float(value) if value else None
    except (AttributeError, TypeError, ValueError):
        return None


class Site:
    """The one reader of the pages of the site; its pacer is shared by every request to the host."""

    def __init__(self) -> None:
        self.pacer = AdaptivePacer(PAGE_INTERVAL_S)

    async def _get_release_page(self, http: httpx.AsyncClient, url: str, headers: dict) -> httpx.Response:
        async def ask() -> httpx.Response | Exception:
            try:
                return await http.get(url, headers=headers, timeout=RELEASE_TIMEOUT, follow_redirects=True)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                return exc

        answer, _attempts, _exhausted = await retrying(
            ask, final=lambda got: isinstance(got, httpx.Response) and got.status_code == 200, budget=RELEASE_BUDGET)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def read_release(self, url: str, http: httpx.AsyncClient, headers: dict) -> tuple[httpx.Response, str]:
        slug = extract_slug(url)
        canonical = f"https://1.ruchime.org/release/{slug}"
        try:
            resp = await self._get_release_page(http, canonical, headers)
        except Exception:
            resp = await self._get_release_page(http, url, headers)
        if resp.status_code != 200:
            resp = await self._get_release_page(http, url, headers)
        resp.raise_for_status()
        return resp, slug

    async def read_episode_page(self, http: httpx.AsyncClient, url: str, headers: dict,
                                sem: asyncio.Semaphore) -> httpx.Response | None:
        """One episode page: asked again after a refusal or a failure, every request spaced by the pacer; None when nothing came."""
        async def ask() -> httpx.Response | None:
            await self.pacer.wait()
            async with sem:
                try:
                    response = await http.get(url, headers=headers, follow_redirects=True, timeout=REQUEST_TIMEOUT)
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

    async def read_episode(self, slug: str, series_name: str, ep: dict, http: httpx.AsyncClient, headers: dict,
                           sem: asyncio.Semaphore) -> Row | None:
        """The row of one episode: its page read once and the players it shows found on it."""
        num = ep.get("number")
        if num is None:
            return None
        page_url = f"https://1.ruchime.org/release/{slug}/episode-{num}"
        row = Row(number=int(num), page_url=page_url, title=episode_title(series_name, ep),
                  publication_date=parse_date(ep.get("date")))
        response = await self.read_episode_page(http, page_url, headers, sem)
        if response is None:
            row.page_failed = True
            row.reason = reason("page_unreachable")
            return row
        if response.status_code != 200:
            row.page_failed = True
            row.reason = reason("page_status", status=response.status_code)
            return row
        text = html.unescape(response.text)
        for adapter in PLAYER_ORDER:
            ref = adapter.find(text, page_url)
            if ref is not None:
                row.refs[adapter.name] = ref
        return row
