"""Pure reading of what VK's embedded player says about one video, through the entries it has.

An entry is one way to ask: the batch call `video.get` with the key from the player link, the data inside the player
page, and the page call of a video (`vk_show`). Each reading says the same thing: the files by height, an explicit
refusal about this very video, or nothing sure. Nothing here touches the network or the clock.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any, Iterator, Sequence, TypeVar
from urllib.parse import parse_qs, urlsplit

from .contract import Kind, Reading

VK_HOSTS = frozenset({"vk.com", "www.vk.com", "vkvideo.ru", "www.vkvideo.ru", "vk.ru", "www.vk.ru"})
# The API host a page names is believed only when it is one of these.
API_HOSTS = frozenset({"api.vkvideo.ru", "api.vk.com", "api.vk.ru"})
API_VERSION = "5.289"
# The anonymous API answers at most this many videos per call, even when asked for more.
BATCH_LIMIT = 50
MESSAGE_LENGTH = 200
EMBED_TITLE = "video embed"
NOT_FOUND_TEXT = "видеофайл не найден"
REFUSALS = ("access denied", "access restricted", "protected by privacy", "private video",
            "скрыто настройками приватности", "доступ ограничен", "ошибка доступа")
# Errors of the API: a refusal about the video, a throttle, a token that is no longer taken.
REFUSED_CODES = frozenset({15, 204})
THROTTLE_CODES = frozenset({6, 9, 29})
TOKEN_CODE = 5

_FILE_KEY = re.compile(r"(?:mp4_|url)(\d+)")
_LITERAL = re.compile(r'"((?:mp4_|url)\d+)"\s*:\s*"((?:[^"\\]|\\.)*)"')
_HEIGHT_MARK = re.compile(r"mp4_(\d+)")
_TITLE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HIDDEN = re.compile(r"<head[\s>].*?</head>|<script.*?</script>|<style.*?</style>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")

T = TypeVar("T")


@dataclass(frozen=True)
class EmbedParts:
    oid: str
    id: str
    hash: str | None

    @property
    def video(self) -> tuple[str, str]:
        return self.oid, self.id

    @property
    def key(self) -> str:
        """`oid_id_hash`, the form `video.get` takes; a link without a hash names the video alone."""
        return f"{self.oid}_{self.id}" + (f"_{self.hash}" if self.hash else "")


def embed_parts(url: str) -> EmbedParts | None:
    parts = urlsplit(html.unescape(url or ""))
    if parts.hostname not in VK_HOSTS or parts.path != "/video_ext.php":
        return None
    query = parse_qs(parts.query)
    oid, video = query.get("oid", [""])[0], query.get("id", [""])[0]
    if not re.fullmatch(r"-?\d+", oid) or not video.isdigit():
        return None
    return EmbedParts(oid, video, query.get("hash", [""])[0] or None)


def chunks(items: Sequence[T], size: int = BATCH_LIMIT) -> Iterator[Sequence[T]]:
    size = max(1, min(size, BATCH_LIMIT))
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _json_after(source: str, pattern: str) -> Iterator[Any]:
    """JSON values that follow a pattern, decoded without running JS and without cutting nested objects by regex."""
    for match in re.finditer(pattern, source):
        try:
            yield json.JSONDecoder().raw_decode(source[match.end():].lstrip())[0]
        except (ValueError, RecursionError):
            continue


@dataclass(frozen=True)
class ApiContext:
    """The web token the player page issues to itself, and the API host it names."""
    token: str = ""
    domain: str = ""
    expires_at: float | None = None

    def __repr__(self) -> str:
        return f"ApiContext(domain={self.domain!r})"


def _epoch(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return value / 1000 if value > 100_000_000_000 else float(value) if value > 1_000_000_000 else None


def api_context(text: str) -> ApiContext | None:
    tokens = list(_json_after(text, r'(?:\bwebToken|"webToken")\s*:\s*'))
    configs = list(_json_after(text, r'(?:\bapiConfigDomains|"apiConfigDomains")\s*:\s*'))
    if not tokens or not configs or not isinstance(tokens[0], dict) or not isinstance(configs[0], dict):
        return None
    token, domain = tokens[0].get("access_token"), configs[0].get("apiDomain")
    if not isinstance(token, str) or not token or domain not in API_HOSTS:
        return None
    return ApiContext(token, domain, _epoch(tokens[0].get("expired_at")))


def call_form(context: ApiContext, keys: Sequence[str], ref_domain: str) -> dict[str, str]:
    return {"access_token": context.token, "v": API_VERSION, "videos": ",".join(keys), "count": str(len(keys)),
            "is_embed": "1", "ref_domain": ref_domain}


@dataclass(frozen=True)
class Called:
    """What an answer of `video.get` holds: the videos by (owner, id), or the error VK named."""
    items: dict[tuple[str, str], dict]
    error: tuple[int, str] | None = None

    @property
    def token_rejected(self) -> bool:
        return self.error is not None and (self.error[0] == TOKEN_CODE or "expired" in self.error[1].lower())


def read_call(text: str) -> Called | None:
    """None: what came back is not an answer of the API at all."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    if isinstance(error, dict):
        try:
            code = int(error.get("error_code") or 0)
        except (TypeError, ValueError):
            code = 0
        return Called({}, (code, str(error.get("error_msg") or "")[:MESSAGE_LENGTH]))
    response = data.get("response")
    items = response.get("items") if isinstance(response, dict) else None
    if not isinstance(items, list):
        return None
    # Never zip the answer with the request: VK omits some videos and reorders the rest.
    return Called({(str(item.get("owner_id")), str(item.get("id"))): item for item in items if isinstance(item, dict)})


def files_of(listed: Any) -> dict[int, str]:
    """Progressive files by height from a `files` object (`mp4_<h>`, or `url<h>` of the older player)."""
    if not isinstance(listed, dict):
        return {}
    found: dict[int, str] = {}
    for key, value in listed.items():
        match = _FILE_KEY.fullmatch(key) if isinstance(key, str) else None
        if match and isinstance(value, str) and value.startswith(("http://", "https://")):
            found[int(match.group(1))] = value
    return found


def playable(found: dict[int, str], status: int = 200) -> Reading:
    return Reading(Kind.PLAYABLE, status, tuple(sorted(found)), files=tuple(sorted(found.items())))


def read_item(item: dict, status: int = 200) -> Reading:
    """One video of an answer: its files, or the refusal it states about itself, else nothing sure."""
    found = files_of(item.get("files"))
    if found:
        return playable(found, status)
    restriction = item.get("restriction") if isinstance(item.get("restriction"), dict) else {}
    if item.get("content_restricted") or restriction.get("can_play") == 0:
        words = str(item.get("content_restricted_message") or restriction.get("text") or "")[:MESSAGE_LENGTH]
        return Reading(Kind.RESTRICTED, status, message=words or "restricted")
    return Reading(Kind.NO_FILE, status, message="no progressive files")


def read_single(called: Called | None, video: tuple[str, str], status: int = 200) -> Reading:
    """The answer of a `video.get` that asked for one video."""
    if called is None:
        return Reading(Kind.NOT_PLAYER, status, message="not an answer of the API")
    if called.error is not None:
        code, words = called.error
        if code in REFUSED_CODES:
            return Reading(Kind.RESTRICTED, status, message=words or "access denied")
        if code in THROTTLE_CODES or "too many" in words.lower():
            return Reading(Kind.HTTP_ERROR, 429, message=words)
        return Reading(Kind.NO_ANSWER, status, message=f"video.get {code} {words}".strip())
    item = called.items.get(video)
    if item is None:
        return Reading(Kind.NO_FILE, status, message="the video is not in the answer")
    return read_item(item, status)


def _visible(text: str) -> str:
    return " ".join(_TAG.sub(" ", _HIDDEN.sub(" ", text)).split())


def _structured_files(text: str, video: tuple[str, str]) -> tuple[dict[int, str], bool]:
    """The files of this video from the page's own data, and whether the page has such data at all."""
    structured = False
    for cache in _json_after(text, r'(?:"apiPrefetchCache"\s*:|\bapiPrefetchCache\s*=)\s*'):
        for entry in cache if isinstance(cache, list) else ():
            response = entry.get("response") if isinstance(entry, dict) and entry.get("method") == "video.get" else None
            for item in (response.get("items") if isinstance(response, dict) else None) or ():
                structured = True
                if isinstance(item, dict) and (str(item.get("owner_id")), str(item.get("id"))) == video:
                    found = files_of(item.get("files"))
                    if found:
                        return found, True
    for data in _json_after(text, r'\b(?:var\s+)?playerParams\s*=\s*'):
        params = data.get("params") if isinstance(data, dict) else None
        if isinstance(params, list) and params and isinstance(params[0], dict):
            structured = True
            if params[0].get("vid") is not None and str(params[0]["vid"]) != video[1]:
                continue
            found = files_of(params[0])
            if found:
                return found, True
    return {}, structured


def page_files(text: str, video: tuple[str, str]) -> dict[int, str]:
    """The files a player page lists for this video: its prefetched answer or params, else bare literals.

    Literals are taken only from a page with no data of its own, or they could belong to another video."""
    found, structured = _structured_files(text, video)
    if found or structured:
        return found
    literals: dict[str, str] = {}
    for key, raw in _LITERAL.findall(text):
        try:
            literals[key] = json.loads(f'"{raw}"')
        except ValueError:
            continue
    return files_of(literals)


def read_page(status: int, text: str, video: tuple[str, str]) -> Reading:
    """The player page of an embed address: a refusal it prints, the files it lists, or a shell without either."""
    if status == 404:
        return Reading(Kind.NOT_FOUND, status)
    if status != 200:
        return Reading(Kind.HTTP_ERROR, status)
    visible = _visible(text)
    lowered = visible.lower()
    # A private video can still list files in its page config, so a printed refusal outranks them.
    if any(phrase in lowered for phrase in REFUSALS):
        return Reading(Kind.RESTRICTED, status, message=visible[:MESSAGE_LENGTH])
    found = page_files(text, video)
    heights = tuple(sorted(set(found) | {int(height) for height in _HEIGHT_MARK.findall(text.lower())}))
    if heights:
        return Reading(Kind.PLAYABLE, status, heights, files=tuple(sorted(found.items())))
    if NOT_FOUND_TEXT in lowered:
        return Reading(Kind.NOT_FOUND, status, message=NOT_FOUND_TEXT)
    titled = _TITLE.search(text)
    title = " ".join(titled.group(1).split()) if titled else ""
    if title.lower() == EMBED_TITLE:
        return Reading(Kind.NO_FILE, status, title=title, message=visible[:MESSAGE_LENGTH])
    return Reading(Kind.NOT_PLAYER, status, title=title)


def pick_file(files: Sequence[tuple[int, str]], max_height: int | None = None) -> tuple[int, str] | None:
    """The best file not above `max_height`, the lowest above it when nothing fits; the best of all without a cap."""
    if not files:
        return None
    ordered = sorted(files)
    if not max_height:
        return ordered[-1]
    fitting = [item for item in ordered if item[0] <= max_height]
    return fitting[-1] if fitting else ordered[0]
