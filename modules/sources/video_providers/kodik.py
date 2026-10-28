"""Kodik player: iframe -> signatures and episode list -> /ftor -> HLS links per quality.

The iframe HTML of one voice-over carries the signed parameters of the embedding site and
the episode ids; `/ftor` answers with ROT18+base64 obfuscated links.
"""
from __future__ import annotations

import html
import re
from base64 import b64decode
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import httpx

from .retry import send_with_retry

ORIGIN = "https://kodikplayer.com"
FTOR_URL = f"{ORIGIN}/ftor"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
REQUEST_TIMEOUT = 20.0
# These query parameters make Kodik answer a truncated page without the episode list.
_TRUNCATING = frozenset({"only_episode", "only_season", "skip_button"})

_VAR = re.compile(r"""var\s+(\w+)\s*=\s*(?:Number\((\d+)\)|['"]([^'"]*)['"])\s*;""")
_TAG = re.compile(r"<option\b([^>]*)>")
_ATTR = re.compile(r"""([\w-]+)\s*=\s*["']([^"']*)["']""")
_SIGNED = ("domain", "d_sign", "pd", "pd_sign", "ref", "ref_sign")


class KodikError(RuntimeError):
    def __init__(self, reason: str, transient: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.transient = transient


@dataclass(frozen=True)
class KodikVoice:
    """One voice-over (translation): its own serial id and hash."""

    translation_id: str
    studio: str
    media_id: str
    media_hash: str
    kind: str


@dataclass
class KodikSession:
    """Parsed iframe of one voice-over: signatures plus the episode ids."""

    iframe_url: str
    signed: dict[str, str]
    episodes: dict[int, tuple[str, str]]
    voices: list[KodikVoice] = field(default_factory=list)


def iframe_url(media_id: str, media_hash: str) -> str:
    return f"{ORIGIN}/serial/{media_id}/{media_hash}/720p?translations=true"


def normalize_iframe(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    parts = urlsplit(html.unescape(url))
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key not in _TRUNCATING]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _options(block: str) -> list[dict[str, str]]:
    return [dict(_ATTR.findall(attrs)) for attrs in _TAG.findall(block)]


def _section(page: str, marker: str) -> str:
    start = page.find(marker)
    if start < 0:
        return ""
    end = page.find("</select>", start)
    return page[start:end if end > 0 else len(page)]


def parse_voices(page: str) -> list[KodikVoice]:
    voices = []
    for option in _options(_section(page, 'class="serial-translations-box"')):
        if not option.get("data-media-id") or not option.get("data-media-hash"):
            continue
        voices.append(KodikVoice(
            translation_id=option.get("data-id", ""),
            studio=html.unescape(option.get("data-title", "")).strip(),
            media_id=option["data-media-id"], media_hash=option["data-media-hash"],
            kind=option.get("data-translation-type", "")))
    return voices


def parse_episodes(page: str) -> dict[int, tuple[str, str]]:
    episodes = {}
    for option in _options(_section(page, 'class="serial-series-box"')):
        try:
            number = int(option["value"])
        except (KeyError, ValueError):
            continue
        if option.get("data-id") and option.get("data-hash"):
            episodes[number] = (option["data-id"], option["data-hash"])
    return episodes


def parse_signatures(page: str) -> dict[str, str]:
    found = {name: (number or text) for name, number, text in _VAR.findall(page)}
    missing = [name for name in _SIGNED if name not in found]
    if missing:
        raise KodikError(f"в iframe Kodik нет параметров {missing}: разметка изменилась")
    return {name: found[name] for name in _SIGNED}


def rot18_b64(text: str) -> str:
    """The cipher of Kodik's player script: letters shifted by 18 within their case, then base64."""
    def shift(char: str) -> str:
        if not char.isascii() or not char.isalpha():
            return char
        last = ord("Z") if char <= "Z" else ord("z")
        code = ord(char) + 18
        return chr(code - 26 if code > last else code)

    plain = "".join(shift(char) for char in text)
    return b64decode(plain + "=" * (-len(plain) % 4)).decode("utf-8")


async def open_session(http: httpx.AsyncClient, url: str, referer: str) -> KodikSession:
    """Load a voice-over iframe the way the embedding site does (its Referer signs the page)."""
    url = normalize_iframe(url)
    try:
        response = await send_with_retry(lambda: http.get(
            url, headers={"Referer": referer, "User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT, follow_redirects=True))
    except httpx.HTTPError as exc:
        raise KodikError(f"Kodik недоступен: {exc}", transient=True) from exc
    if response.status_code >= 500 or response.status_code == 429:
        raise KodikError(f"Kodik ответил {response.status_code}", transient=True)
    if response.status_code != 200:
        raise KodikError(f"Kodik ответил {response.status_code}")
    page = response.text
    episodes = parse_episodes(page)
    if not episodes:
        raise KodikError("в iframe Kodik нет списка серий")
    return KodikSession(iframe_url=url, signed=parse_signatures(page),
                        episodes=episodes, voices=parse_voices(page))


async def episode_qualities(http: httpx.AsyncClient, session: KodikSession,
                            episode: int) -> dict[int, str]:
    """HLS link per quality (height) of one episode."""
    if episode not in session.episodes:
        raise KodikError(f"у озвучки Kodik нет серии {episode}")
    episode_id, episode_hash = session.episodes[episode]
    base = session.iframe_url.split("?")[0]
    payload = {**session.signed, "bad_user": "false", "cdn_is_working": "true",
               "type": "seria", "hash": episode_hash, "id": episode_id}
    headers = {"Origin": ORIGIN, "Referer": f"{base}?episode={episode}",
               "X-Requested-With": "XMLHttpRequest", "User-Agent": USER_AGENT,
               "Accept": "application/json, text/javascript, */*; q=0.01"}
    try:
        response = await send_with_retry(lambda: http.post(
            FTOR_URL, data=payload, headers=headers, timeout=REQUEST_TIMEOUT))
    except httpx.HTTPError as exc:
        raise KodikError(f"Kodik недоступен: {exc}", transient=True) from exc
    if response.status_code >= 500 or response.status_code == 429:
        raise KodikError(f"Kodik ответил {response.status_code}", transient=True)
    if response.status_code != 200:
        raise KodikError(f"Kodik ответил {response.status_code}")
    try:
        links = response.json().get("links")
    except ValueError as exc:
        raise KodikError("Kodik вернул не JSON") from exc
    qualities: dict[int, str] = {}
    for height, items in (links or {}).items():
        src = items[0].get("src", "") if isinstance(items, list) and items else ""
        if not src or not str(height).isdigit():
            continue
        link = src if src.startswith(("http", "//")) else rot18_b64(src)
        qualities[int(height)] = "https:" + link if link.startswith("//") else link
    if not qualities:
        raise KodikError("Kodik не отдал ссылок на серию")
    return qualities


def voice_iframe(voice: KodikVoice) -> str:
    return iframe_url(voice.media_id, voice.media_hash)


def is_kodik_url(url: str) -> bool:
    return (urlparse(url).hostname or "").endswith("kodikplayer.com")
