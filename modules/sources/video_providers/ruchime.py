"""Провайдер видео для сайта Ruchime (https://1.ruchime.org)."""
from __future__ import annotations

import asyncio
import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from .. import dates, hosts
from .base import BaseVideoProvider


class RuchimeProvider(BaseVideoProvider):
    name = "ruchime"
    domains = ("1.ruchime.org", "ruchime.org")

    def match_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = hosts.canonical_host(parsed.netloc)
            return host in self.domains and "/release/" in parsed.path
        except Exception:
            return False

    def _extract_slug(self, url: str) -> str:
        path = urlparse(url).path
        m = re.search(r"/release/([^/?#]+)", path)
        if not m:
            raise ValueError(f"Не удалось извлечь slug релиза из URL: {url}")
        return m.group(1)

    def canonical_url(self, url: str) -> str:
        slug = self._extract_slug(url)
        return f"https://1.ruchime.org/release/{slug}"

    @staticmethod
    def _parse_date(raw_date: str | None) -> str:
        if not raw_date:
            return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        parts = raw_date.strip().split()
        if len(parts) == 3:
            day, mon, year = parts
            month = dates._MONTHS_SHORT.get(mon.lower()[:3], "01")
            return f"{year}-{month}-{day.zfill(2)}T12:00:00Z"
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def parse_release(self, url: str, http: httpx.AsyncClient) -> dict:
        slug = self._extract_slug(url)
        canonical = self.canonical_url(url)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = await http.get(url, headers=headers)
        if resp.status_code != 200:
            resp = await http.get(canonical, headers=headers)
        resp.raise_for_status()
        raw_text = resp.text

        # 1. Метаданные (JSON-LD TVSeries)
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

        # 2. Список серий из массива ALL_EPS
        titles: list[str] = []
        episodes_raw: list[dict] = []
        m_eps = re.search(r"const ALL_EPS\s*=\s*(\[.*?\]);", raw_text, re.DOTALL)
        if m_eps:
            try:
                episodes_raw = json.loads(m_eps.group(1))
                for ep in episodes_raw:
                    c_from = ep.get("compilation_from")
                    c_to = ep.get("compilation_to")
                    if c_from is not None and c_to is not None:
                        ep_label = f"{c_from}-{c_to} серия"
                    else:
                        num = ep.get("number")
                        ep_label = f"{num} серия" if num is not None else ep.get("title", "")
                    titles.append(f"{name_ru} - {ep_label}")
            except Exception:
                pass

        # Определение индивидуальных разрешений для каждой серии
        videos: list[dict] = []
        all_resolutions: list[int] = []

        if episodes_raw:
            sem = asyncio.Semaphore(15)

            async def probe(title: str, ep: dict) -> dict:
                num = ep.get("number")
                res = None
                if num is not None:
                    ep_probe_url = f"https://1.ruchime.org/release/{slug}/episode-{num}"
                    async with sem:
                        res = await self._probe_episode_resolution(ep_probe_url, http, headers)
                return {"title": title, "resolution": res}

            tasks = [probe(t, ep) for t, ep in zip(titles, episodes_raw)]
            videos = list(await asyncio.gather(*tasks))
            all_resolutions = [v["resolution"] for v in videos if v.get("resolution") is not None]

        release_res = max(all_resolutions) if all_resolutions else None

        for v in videos:
            if v.get("resolution") is None:
                v["resolution"] = release_res

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

    @staticmethod
    def _extract_allvideo_resolution(html_text: str) -> int | None:
        """Извлечение максимального разрешения из плеера AllVideo / Playerjs.
        1) Разбирает параметр file: "[360p]url,[720p]url,[1080p]url"
        2) Если в file не указано — проверяет заголовок страницы (<title>... 4К/1080p).
        """
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

    async def _probe_episode_resolution(self, ep_page_url: str,
                                        http: httpx.AsyncClient,
                                        headers: dict) -> int | None:
        """Определяет разрешение серии по VK или AllVideo плееру."""
        try:
            r = await http.get(ep_page_url, headers=headers, follow_redirects=True)
            if r.status_code != 200:
                return None
            text = html.unescape(r.text)
            # 1. VK
            m_vk = re.search(
                r"switchSource\(['\"](https?://(?:vk\.com|vkvideo\.ru)/"
                r"video_ext\.php\?[^'\"]+)", text)
            if m_vk:
                vk_url = html.unescape(m_vk.group(1))
                r_vk = await http.get(vk_url, headers={"Referer": ep_page_url},
                                      follow_redirects=True)
                if r_vk.status_code == 200:
                    res_vk = [int(q) for q in re.findall(r'mp4_(\d+)', r_vk.text)]
                    if res_vk:
                        return max(res_vk)
            # 2. AllVideo
            m_av = re.search(
                r"switchSource\(['\"](https?://(?:fsst\.online|incvideo1\.online)/embed/\d+/?)",
                text)
            if m_av:
                r_av = await http.get(m_av.group(1), headers={"Referer": ep_page_url},
                                      follow_redirects=True)
                if r_av.status_code == 200:
                    return self._extract_allvideo_resolution(r_av.text)
        except Exception:
            pass
        return None

    async def scan_episodes(self, url: str, http: httpx.AsyncClient,
                            series: dict) -> list[dict]:
        slug = self._extract_slug(url)
        canonical = f"https://1.ruchime.org/release/{slug}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        # Сначала получаем страницу релиза с ALL_EPS
        resp = await http.get(url, headers=headers)
        if resp.status_code != 200:
            resp = await http.get(canonical, headers=headers)
        resp.raise_for_status()

        m_eps = re.search(r"const ALL_EPS\s*=\s*(\[.*?\]);", resp.text, re.DOTALL)
        if not m_eps:
            raise ValueError(f"Ruchime: массив ALL_EPS не найден на странице {url}")
        all_eps = json.loads(m_eps.group(1))

        # Имя сериала для названия серии
        name_ru = series.get("name")
        if not name_ru:
            m_ld = re.search(r'"name":"([^"]+)"', resp.text)
            name_ru = m_ld.group(1) if m_ld else slug

        sem = asyncio.Semaphore(10)

        async def fetch_episode_fact(ep: dict) -> dict | None:
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
            title = f"{name_ru} - {ep_label}"
            pub_date = self._parse_date(ep.get("date"))

            video_url = None
            resolution: int | None = None
            async with sem:
                try:
                    r = await http.get(ep_page_url, headers=headers)
                    if r.status_code == 200:
                        text = html.unescape(r.text)
                        # 1. Приоритет: VK
                        m_vk = re.search(
                            r"switchSource\(['\"](https?://(?:vk\.com|vkvideo\.ru)/"
                            r"video_ext\.php\?[^'\"]+)", text)
                        if m_vk:
                            raw_vk = html.unescape(m_vk.group(1))
                            m_oid_id = re.search(r"oid=(-?\d+)&id=(\d+)", raw_vk)
                            if m_oid_id:
                                video_url = f"https://vk.com/video{m_oid_id.group(1)}_{m_oid_id.group(2)}"
                            else:
                                video_url = raw_vk
                            try:
                                r_vk = await http.get(raw_vk, headers={"Referer": ep_page_url},
                                                      follow_redirects=True)
                                if r_vk.status_code == 200:
                                    res_vk = [int(q) for q in re.findall(r'mp4_(\d+)', r_vk.text)]
                                    if res_vk:
                                        resolution = max(res_vk)
                            except Exception:
                                pass
                        else:
                            # 2. Вторичный плеер (AllVideo / fsst.online / incvideo1.online)
                            m_av = re.search(
                                r"switchSource\(['\"](https?://(?:fsst\.online|incvideo1\.online)/embed/\d+/?)",
                                text)
                            if m_av:
                                embed_url = m_av.group(1)
                                video_url = embed_url
                                try:
                                    r_av = await http.get(embed_url, headers={"Referer": ep_page_url},
                                                          follow_redirects=True)
                                    if r_av.status_code == 200:
                                        resolution = self._extract_allvideo_resolution(r_av.text)
                                except Exception:
                                    pass
                except Exception:
                    pass

            # Если плеер не найден в HTML страницы серии, запасной URL — адрес страницы серии
            if not video_url:
                video_url = ep_page_url

            return {
                "title": title,
                "url": video_url,
                "publication_date": pub_date,
                "resolution": resolution or 1080,
            }

        tasks = [fetch_episode_fact(ep) for ep in all_eps]
        results = await asyncio.gather(*tasks)
        facts = [f for f in results if f is not None]
        # Сортируем по возрастанию даты / номера
        facts.sort(key=lambda f: f["publication_date"])
        return facts
