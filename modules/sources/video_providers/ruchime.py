"""Video provider for Ruchime (https://1.ruchime.org)."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from urllib.parse import urlparse

import httpx

from .base import BaseVideoProvider

logger = logging.getLogger("sources.video.ruchime")

# Bound concurrent HTTP requests to respect host limits and prevent connection pool saturation.
MAX_CONCURRENT_PROBES = 10

_MONTHS = {
    "янв": "01", "фев": "02", "мар": "03", "апр": "04", "мая": "05",
    "июн": "06", "июл": "07", "авг": "08", "сен": "09", "окт": "10",
    "ноя": "11", "дек": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10",
    "nov": "11", "dec": "12",
}


class RuchimeProvider(BaseVideoProvider):
    name = "ruchime"
    domains = ("1.ruchime.org", "ruchime.org")

    def match_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            return host in self.domains and "/release/" in parsed.path
        except Exception:
            return False

    def _extract_slug(self, url: str) -> str:
        path = urlparse(url).path
        m = re.search(r"/release/([^/?#]+)", path)
        if not m:
            raise ValueError(f"Failed to extract release slug from URL: {url}")
        return m.group(1)

    def canonical_url(self, url: str) -> str:
        slug = self._extract_slug(url)
        return f"https://1.ruchime.org/release/{slug}"

    @staticmethod
    def _parse_date(raw_date: str | None) -> str:
        # Deterministic date parsing to keep media_unique_id stable across scans.
        default_date = "1970-01-01T12:00:00Z"
        if not raw_date:
            return default_date
        parts = raw_date.strip().split()
        if len(parts) == 3:
            day, mon, year = parts
            mon_key = mon.lower()[:3]
            month = _MONTHS.get(mon_key)
            if month and day.isdigit() and year.isdigit():
                return f"{year}-{month}-{day.zfill(2)}T12:00:00Z"
        return default_date

    @staticmethod
    def _extract_allvideo_resolution(html_text: str) -> int | None:
        """Extract highest available video resolution from AllVideo / Playerjs configuration."""
        resolutions: list[int] = []
        m_file = re.search(r'file:\s*["\']([^"\']+)["\']', html_text)
        if m_file:
            file_val = m_file.group(1)
            for q in re.findall(r'\[(\d+)p?\]', file_val, re.IGNORECASE):
                resolutions.append(int(q))
            if re.search(r'\[4k\]', file_val, re.IGNORECASE):
                resolutions.append(2160)
            for q in re.findall(r'[-_](\d{3,4})p?\.(?:mp4|m3u8)', file_val, re.IGNORECASE):
                resolutions.append(int(q))

        if not resolutions:
            m_title = re.search(r'<title>(.*?)</title>', html_text, re.IGNORECASE)
            title_text = m_title.group(1) if m_title else ""
            if re.search(r'\b(?:4k|2160p?)\b', title_text, re.IGNORECASE) or '4к' in title_text.lower():
                resolutions.append(2160)
            for q in re.findall(r'\b(1080|720|480|360)p?\b', title_text, re.IGNORECASE):
                resolutions.append(int(q))

        return max(resolutions) if resolutions else None

    @staticmethod
    def _extract_best_stream_url(file_val: str) -> str | None:
        """Extract best stream URL from comma-separated Playerjs quality format."""
        streams: list[tuple[int, str]] = []
        for chunk in file_val.split(','):
            chunk = chunk.strip()
            if not chunk:
                continue
            m = re.search(r'\[(?:(\d+)p?|(4k))\]', chunk, re.IGNORECASE)
            if m:
                tag_num, tag_4k = m.group(1), m.group(2)
                quality = 2160 if tag_4k else int(tag_num)
                stream_url = re.sub(r'\[[^\]]+\]', '', chunk).strip()
                if stream_url:
                    streams.append((quality, stream_url))
            else:
                cleaned = re.sub(r'\[[^\]]+\]', '', chunk).strip()
                if cleaned:
                    streams.append((0, cleaned))
        if streams:
            streams.sort(key=lambda item: item[0], reverse=True)
            return streams[0][1]
        cleaned_fallback = re.sub(r'\[[^\]]+\]', '', file_val).strip()
        return cleaned_fallback if cleaned_fallback else None

    async def _resolve_episode(self, slug: str, series_name: str, ep: dict,
                               http: httpx.AsyncClient, headers: dict,
                               sem: asyncio.Semaphore) -> dict | None:
        """Resolve metadata, video stream URL, and resolution for a single episode."""
        num = ep.get("number")
        if num is None:
            return None
        ep_page_url = f"https://1.ruchime.org/release/{slug}/episode-{num}"

        c_from = ep.get("compilation_from")
        c_to = ep.get("compilation_to")
        if c_from is not None and c_to is not None:
            ep_label = f"{c_from}-{c_to} серия"
        else:
            ep_label = f"{num} серия"
        title = f"{series_name} - {ep_label}"
        pub_date = self._parse_date(ep.get("date"))

        video_url: str | None = None
        resolution: int | None = None

        async with sem:
            try:
                r = await http.get(ep_page_url, headers=headers, follow_redirects=True)
                if r.status_code == 200:
                    text = html.unescape(r.text)

                    # Probe primary player: VK Video
                    m_vk = re.search(
                        r"switchSource\(['\"](https?://(?:vk\.com|vkvideo\.ru)/"
                        r"video_ext\.php\?[^'\"]+)", text)
                    if m_vk:
                        raw_vk = html.unescape(m_vk.group(1))
                        m_oid_id = re.search(r"oid=(-?\d+)&id=(\d+)", raw_vk)
                        video_url = (f"https://vk.com/video{m_oid_id.group(1)}_{m_oid_id.group(2)}"
                                     if m_oid_id else raw_vk)
                        try:
                            r_vk = await http.get(raw_vk, headers={"Referer": ep_page_url},
                                                  follow_redirects=True)
                            if r_vk.status_code == 200:
                                res_vk = [int(q) for q in re.findall(r'mp4_(\d+)', r_vk.text)]
                                if res_vk:
                                    resolution = max(res_vk)
                        except Exception as exc:
                            logger.warning("VK probe failed for %s: %s", raw_vk, exc)

                    # Probe secondary player: AllVideo if VK was not found or yielded no resolution
                    if not resolution:
                        m_av = re.search(
                            r"switchSource\(['\"](https?://(?:fsst\.online|incvideo1\.online)/embed/\d+/?)",
                            text)
                        if m_av:
                            embed_url = m_av.group(1)
                            if not video_url:
                                video_url = embed_url
                            try:
                                r_av = await http.get(embed_url, headers={"Referer": ep_page_url},
                                                      follow_redirects=True)
                                if r_av.status_code == 200:
                                    res_av = self._extract_allvideo_resolution(r_av.text)
                                    if res_av is not None:
                                        resolution = res_av
                                        # Use AllVideo embed if VK returned no playable resolutions
                                        if video_url.startswith("https://vk.com"):
                                            video_url = embed_url
                            except Exception as exc:
                                logger.warning("AllVideo probe failed for %s: %s", embed_url, exc)
            except Exception as exc:
                logger.warning("Failed probing episode page %s: %s", ep_page_url, exc)

        return {
            "number": num,
            "title": title,
            "page_url": ep_page_url,
            "video_url": video_url,
            "resolution": resolution,
            "publication_date": pub_date,
        }

    async def parse_release(self, url: str, http: httpx.AsyncClient) -> dict:
        slug = self._extract_slug(url)
        canonical = self.canonical_url(url)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = await http.get(canonical, headers=headers)
        if resp.status_code != 200:
            resp = await http.get(url, headers=headers)
        resp.raise_for_status()
        raw_text = resp.text

        name_ru: str | None = None
        name_en: str | None = None
        for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>',
                             raw_text, re.DOTALL):
            try:
                ld = json.loads(m.group(1))
                if ld.get("@type") == "TVSeries":
                    name_ru = ld.get("name")
                    name_en = ld.get("alternateName")
                    break
            except Exception:
                continue

        if not name_ru:
            m_title = re.search(r"<title>(.*?)(?:—\s*Ruchime.*)?</title>", raw_text)
            if m_title:
                name_ru = re.sub(r"\s+\d+\s+серия.*", "", m_title.group(1)).strip()
            else:
                name_ru = self._extract_slug(url).replace("-", " ").title()

        titles: list[str] = []
        episodes_raw: list[dict] = []
        m_eps = re.search(r"const ALL_EPS\s*=\s*(\[.*?\]);", raw_text, re.DOTALL)
        if m_eps:
            try:
                episodes_raw = json.loads(m_eps.group(1))
            except Exception as exc:
                logger.warning("Failed parsing ALL_EPS JSON on %s: %s", url, exc)

        videos: list[dict] = []
        all_resolutions: list[int] = []

        if episodes_raw:
            sem = asyncio.Semaphore(MAX_CONCURRENT_PROBES)
            tasks = [self._resolve_episode(slug, name_ru, ep, http, headers, sem)
                     for ep in episodes_raw]
            resolved = await asyncio.gather(*tasks)
            for item in resolved:
                if item is None:
                    continue
                titles.append(item["title"])
                videos.append({
                    "title": item["title"],
                    "resolution": item["resolution"],
                })
                if item["resolution"] is not None:
                    all_resolutions.append(item["resolution"])

        release_res = max(all_resolutions) if all_resolutions else None

        return {
            "canonical_url": canonical,
            "title": {"ru": name_ru, "en": name_en},
            "titles": titles,
            "resolution": release_res,
            "videos": videos,
            "episodes_count": len(titles),
            "provider": self.name,
            "source_type": "web_video",
            "releases": [],
        }

    async def scan_episodes(self, url: str, http: httpx.AsyncClient,
                            series: dict) -> list[dict]:
        slug = self._extract_slug(url)
        canonical = f"https://1.ruchime.org/release/{slug}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        resp = await http.get(canonical, headers=headers)
        if resp.status_code != 200:
            resp = await http.get(url, headers=headers)
        resp.raise_for_status()

        m_eps = re.search(r"const ALL_EPS\s*=\s*(\[.*?\]);", resp.text, re.DOTALL)
        if not m_eps:
            raise ValueError(f"Ruchime: array ALL_EPS not found on page {url}")
        all_eps = json.loads(m_eps.group(1))

        name_ru = series.get("name")
        if not name_ru:
            m_ld = re.search(r'"name":"([^"]+)"', resp.text)
            name_ru = m_ld.group(1) if m_ld else slug

        sem = asyncio.Semaphore(MAX_CONCURRENT_PROBES)
        tasks = [self._resolve_episode(slug, name_ru, ep, http, headers, sem)
                 for ep in all_eps]
        resolved = await asyncio.gather(*tasks)

        facts: list[dict] = []
        for item in resolved:
            if item is None:
                continue
            if not item["video_url"]:
                logger.warning("Skipping episode %s: no video player stream found", item["page_url"])
                continue
            facts.append({
                "title": item["title"],
                "url": item["video_url"],
                "publication_date": item["publication_date"],
                "resolution": item["resolution"],
            })

        facts.sort(key=lambda f: f["publication_date"])
        return facts

    async def resolve_stream(self, url: str, http: httpx.AsyncClient) -> dict | None:
        """Resolve direct stream URL and required headers for AllVideo player embeds."""
        if not any(d in url for d in ("fsst.online", "incvideo1.online")):
            return None
        try:
            r = await http.get(url, headers={"Referer": "https://1.ruchime.org/"}, follow_redirects=True)
            if r.status_code != 200:
                return None
            m_file = re.search(r'file:\s*["\']([^"\']+)["\']', r.text)
            if not m_file:
                return None
            stream_url = self._extract_best_stream_url(m_file.group(1))
            if not stream_url:
                return None
            parsed = urlparse(url)
            origin = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else "https://incvideo1.online/"
            return {
                "stream_url": stream_url,
                "headers": {"Referer": origin},
            }
        except Exception as exc:
            logger.warning("Failed resolving AllVideo stream for %s: %s", url, exc)
            return None
