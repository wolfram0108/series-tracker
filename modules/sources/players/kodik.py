"""Kodik player: iframe -> signatures and episode list -> /ftor -> HLS links per quality.

The iframe HTML of one voice-over carries the signed parameters of the embedding site and
the items of its list (an item is a single episode or a compilation, and has no number the player vouches for: the text of
the item is what is shown); `/ftor` answers with ROT18+base64 obfuscated links. A player asks once and reads
the answer; asking again is decided by `policy`.
"""
from __future__ import annotations

import html
import json
import logging
import re
import time
from base64 import b64decode
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from weakref import WeakKeyDictionary

import httpx

from core.contracts import RowVoice
from core.reasons import describe, reason

from ..policy import ask
from .catalog import CatalogError, Entry
from .contract import Answer, Kind, PlayerRef, Reading
from .http import get_answer, post_answer, read_answer
from .streaming import listed_stream

logger = logging.getLogger("sources")

ORIGIN = "https://kodikplayer.com"
FTOR_URL = f"{ORIGIN}/ftor"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
REQUEST_TIMEOUT_S = 20.0
# An opened iframe is kept this long: its signatures are issued for the embedding site and serve all its episodes.
SESSION_LIFETIME_S = 300.0
# These query parameters make Kodik answer a truncated page without the episode list.
_TRUNCATING = frozenset({"only_episode", "only_season", "skip_button"})

_VAR = re.compile(r"""var\s+(\w+)\s*=\s*(?:Number\((\d+)\)|['"]([^'"]*)['"])\s*;""")
_TAG = re.compile(r"<option\b([^>]*)>")
_OPTION = re.compile(r"<option\b([^>]*)>(.*?)</option>", re.DOTALL)
_TEXT = re.compile(r"<[^>]+>")
_ATTR = re.compile(r"""([\w-]+)\s*=\s*["']([^"']*)["']""")
_SIGNED = ("domain", "d_sign", "pd", "pd_sign", "ref", "ref_sign")
# The iframe of a page that embeds the player in place, or the button that switches a page to it.
_FRAME = re.compile(r"""<iframe\b[^>]*\bdata-src=["']([^"']*kodikplayer\.com[^"']*)["']""", re.IGNORECASE)
_SWITCH = re.compile(r"""switchSource\(\s*["']([^"']*kodikplayer\.com[^"']*)["']""", re.IGNORECASE)
_MEDIA = re.compile(r"/serial/([^/]+)/([^/?#]+)")
_LINK = re.compile(r"^kodik=([^/]+)/([^/]+)/(\d+)$")


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


@dataclass(frozen=True)
class KodikSeason:
    """A season of a voice-over as the iframe lists it: its title, and the serial that holds its items."""

    title: str
    media_id: str
    media_hash: str
    selected: bool


@dataclass(frozen=True)
class KodikItem:
    """An item of the list of a season: `id` and `hash` open it, `text` is what the list shows (a range for a compilation).
    `value` is the mark the player puts on the item (the last number of a compilation): a note, not a number of the episode."""

    id: str
    hash: str
    text: str
    value: str


@dataclass
class KodikSession:
    """Parsed iframe of one voice-over: signatures plus the items of the list it opened with."""

    iframe_url: str
    signed: dict[str, str]
    items: dict[str, KodikItem]
    voices: list[KodikVoice] = field(default_factory=list)
    seasons: list[KodikSeason] = field(default_factory=list)


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


def parse_seasons(page: str) -> list[KodikSeason]:
    seasons = []
    for option in _options(_section(page, 'class="serial-seasons-box"')):
        if option.get("data-serial-id") and option.get("data-serial-hash"):
            seasons.append(KodikSeason(
                title=html.unescape(option.get("data-title", "")).strip(), media_id=option["data-serial-id"],
                media_hash=option["data-serial-hash"], selected="selected" in option))
    return seasons


def parse_items(page: str) -> dict[str, KodikItem]:
    """The items of the list in the order of the list, by the id of the item: the one thing about it that is its own."""
    items: dict[str, KodikItem] = {}
    for attrs, inner in _OPTION.findall(_section(page, 'class="serial-series-box"')):
        option = dict(_ATTR.findall(attrs))
        if option.get("data-id") and option.get("data-hash"):
            text = " ".join(html.unescape(_TEXT.sub("", inner)).split()) or html.unescape(option.get("data-title", "")).strip()
            items[option["data-id"]] = KodikItem(option["data-id"], option["data-hash"], text, option.get("value", ""))
    return items


def parse_signatures(page: str) -> dict[str, str]:
    found = {name: (number or text) for name, number, text in _VAR.findall(page)}
    missing = [name for name in _SIGNED if name not in found]
    if missing:
        raise KodikError(reason("kodik_params_missing", missing=", ".join(missing)))
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


def read_session(url: str, answer: Answer) -> KodikSession:
    """The session an iframe answer carries; the error says why there is none, and whether asking again may help."""
    if answer.status is None:
        raise KodikError(reason("player_unreachable", player="Kodik", error=answer.error), transient=True)
    if answer.status >= 500 or answer.status == 429:
        raise KodikError(reason("player_status", player="Kodik", status=answer.status), transient=True)
    if answer.status != 200:
        raise KodikError(reason("player_status", player="Kodik", status=answer.status))
    items = parse_items(answer.text)
    if not items:
        raise KodikError(reason("kodik_no_episode_list"))
    return KodikSession(iframe_url=url, signed=parse_signatures(answer.text), items=items,
                        voices=parse_voices(answer.text), seasons=parse_seasons(answer.text))


def read_ftor(status: int, text: str) -> Reading:
    """HLS link per quality (height) of one episode, read from the `/ftor` answer."""
    if status == 404:
        return Reading(Kind.NOT_FOUND, status)
    if status != 200:
        return Reading(Kind.HTTP_ERROR, status)
    try:
        links = json.loads(text).get("links")
    except (ValueError, AttributeError):
        return Reading(Kind.NOT_PLAYER, status, message="not JSON")
    files: dict[int, str] = {}
    for height, items in (links or {}).items():
        src = items[0].get("src", "") if isinstance(items, list) and items and isinstance(items[0], dict) else ""
        if not src or not str(height).isdigit():
            continue
        try:
            link = src if src.startswith(("http", "//")) else rot18_b64(src)
        except ValueError:
            return Reading(Kind.NOT_PLAYER, status, message="links not decoded")
        files[int(height)] = "https:" + link if link.startswith("//") else link
    if not files:
        return Reading(Kind.NO_FILE, status)
    return Reading(Kind.PLAYABLE, status, tuple(sorted(files)), files=tuple(sorted(files.items())))


def link_of(page_url: str, media_id: str, media_hash: str, item_id: str) -> str:
    """The candidate link of one item of the list: a fragment of the page, so the identity of the item is stable."""
    return f"{page_url}#kodik={media_id}/{media_hash}/{item_id}"


def parse_link(url: str) -> tuple[str, str, str] | None:
    found = _LINK.match(urlsplit(url).fragment)
    return (found.group(1), found.group(2), found.group(3)) if found else None


def is_kodik_url(url: str) -> bool:
    return (urlparse(url).hostname or "").endswith("kodikplayer.com")


def voice_iframe(voice: KodikVoice) -> str:
    return iframe_url(voice.media_id, voice.media_hash)


class KodikPlayer:
    name = "kodik"
    label = "Kodik"
    series_scope = True
    # What a stream of this player is asked with.
    stream_headers = {"Referer": ORIGIN + "/"}

    def __init__(self) -> None:
        # A session belongs to the client that asked for it: the signatures are issued for that visitor.
        self._sessions: "WeakKeyDictionary[httpx.AsyncClient, dict[str, tuple[float, KodikSession]]]" = WeakKeyDictionary()

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        """The iframe a page embeds, or the button that switches the page to it; it opens a voice-over of the title."""
        match = _FRAME.search(text) or _SWITCH.search(text)
        return PlayerRef(self.name, normalize_iframe(html.unescape(match.group(1)))) if match else None

    @staticmethod
    def ref_of(page_url: str, media_id: str, media_hash: str, item_id: str) -> PlayerRef:
        return PlayerRef("kodik", link_of(page_url, media_id, media_hash, item_id))

    @staticmethod
    def owns(url: str) -> bool:
        return parse_link(url) is not None

    async def stream(self, url: str, http: httpx.AsyncClient, max_height: int | None, pacer) -> dict:
        return await listed_stream(self, url, http, max_height, pacer)

    def capped(self, max_height: int | None) -> "KodikPlayer":
        """Kodik lists every file of an item, so the cap is applied when one is picked, not when it is asked for."""
        return self

    async def open_page(self, http: httpx.AsyncClient, url: str, referer: str) -> Answer:
        """One request for an iframe, the way the embedding site loads it (its Referer signs the page)."""
        return await get_answer(http, normalize_iframe(url), referer, headers={"Referer": referer, "User-Agent": USER_AGENT},
                                timeout=REQUEST_TIMEOUT_S)

    def remember(self, http: httpx.AsyncClient, session: KodikSession, key: str | None = None) -> None:
        self._sessions.setdefault(http, {})[key or session.iframe_url] = (time.time() + SESSION_LIFETIME_S, session)

    def recall(self, http: httpx.AsyncClient, key: str) -> KodikSession | None:
        kept = self._sessions.get(http, {}).get(key)
        return kept[1] if kept is not None and kept[0] > time.time() else None

    async def entries(self, source: PlayerRef, http: httpx.AsyncClient, page_url: str) -> list[Entry]:
        """Every item of every season of every voice-over the iframe offers (the ones its address limits it to, when it
        names any), each as the list shows it."""
        parts = urlsplit(page_url)
        referer = f"{parts.scheme}://{parts.netloc}/"
        first = await self._open(http, source.url, referer)
        wanted = _only_translations(source.url)
        voices = [voice for voice in (first.voices or [_lone_voice(source.url)]) if not wanted or voice.translation_id in wanted]
        found: list[Entry] = []
        for voice in voices:
            opened = first if (voice.media_id, voice.media_hash) == _media_of(source.url) else await self._open(
                http, voice_iframe(voice), referer)
            for season in opened.seasons or [KodikSeason("", voice.media_id, voice.media_hash, True)]:
                session = opened if season.selected or not opened.seasons else await self._open(
                    http, iframe_url(season.media_id, season.media_hash), referer)
                self.remember(http, session, iframe_url(season.media_id, season.media_hash))
                found += [Entry(
                    key=f"{voice.translation_id}:{item.id}", ref=self.ref_of(page_url, season.media_id, season.media_hash, item.id),
                    text=item.text, group=season.title, voice=RowVoice(name=voice.studio, kind=voice.kind),
                    site_fields={"value": item.value, "translation_id": voice.translation_id}) for item in session.items.values()]
        return found

    async def _open(self, http: httpx.AsyncClient, url: str, referer: str) -> KodikSession:
        """An iframe opened under the one policy."""
        url = normalize_iframe(url)
        answer = await ask(lambda: self.open_page(http, url, referer))
        try:
            return read_session(url, answer)
        except KodikError as exc:
            logger.warning("Kodik iframe %s did not open: %s", url, describe(exc.reason))
            raise CatalogError(exc.reason, exc.transient) from exc

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        target = parse_link(ref.url)
        if target is None:
            return Answer(200, reading=Reading(Kind.NOT_PLAYER, 200, message="not a Kodik link"))
        media_id, media_hash, item_id = target
        key = iframe_url(media_id, media_hash)
        parts = urlsplit(page_url)
        referer = f"{parts.scheme}://{parts.netloc}/"
        session = self.recall(http, key)
        if session is None:
            opened = await self.open_page(http, key, referer)
            try:
                session = read_session(key, opened)
            except KodikError:
                return Answer(opened.status, retry_after=opened.retry_after, error=opened.error, reading=_unopened(opened))
            self.remember(http, session)
        item = session.items.get(item_id)
        if item is None:
            return Answer(200, reading=Reading(Kind.NOT_FOUND, 200, message=f"item {item_id}"))
        base = session.iframe_url.split("?")[0]
        payload = {**session.signed, "bad_user": "false", "cdn_is_working": "true",
                   "type": "seria", "hash": item.hash, "id": item.id}
        headers = {"Origin": ORIGIN, "Referer": f"{base}?episode={item.value}", "X-Requested-With": "XMLHttpRequest",
                   "Accept": "application/json, text/javascript, */*; q=0.01"}
        return await post_answer(http, FTOR_URL, payload, headers, timeout=REQUEST_TIMEOUT_S)

    def read(self, answer: Answer) -> Reading:
        if answer.reading is not None:
            return answer.reading
        return read_answer(answer, read_ftor)


def _media_of(url: str) -> tuple[str, str] | None:
    found = _MEDIA.search(urlsplit(url).path)
    return (found.group(1), found.group(2)) if found else None


def _only_translations(url: str) -> frozenset[str]:
    """The voice-overs an iframe address limits the iframe to; empty when it limits nothing."""
    values = [value for key, value in parse_qsl(urlsplit(url).query) if key == "only_translations"]
    return frozenset(part for value in values for part in value.split(",") if part)


def _lone_voice(url: str) -> KodikVoice:
    """An iframe that lists no voice-overs is the one voice-over its address names; the iframe does not word it."""
    media = _media_of(url) or ("", "")
    only = sorted(_only_translations(url))
    return KodikVoice(translation_id=only[0] if only else "", studio="", media_id=media[0], media_hash=media[1], kind="")


def _unopened(opened: Answer) -> Reading:
    """Why an iframe gave no session: nothing came back, the host refused, or the page lacks the list or the signatures."""
    if opened.status is None:
        return Reading(Kind.NO_ANSWER, message=opened.error)
    if opened.status != 200:
        return Reading(Kind.HTTP_ERROR, opened.status)
    return Reading(Kind.NOT_PLAYER, opened.status, message="no episode list or signatures")
