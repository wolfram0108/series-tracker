"""Finding the public shares of 139 Cloud: what the public indexes say, read into candidates. Pure: no request is made here.

139 Cloud has no search of its own for what others published. The shares are found where people post them: the public
Telegram channels that announce them (the web page of a channel can be searched, `https://t.me/s/<channel>?q=`) and the
search APIs of PanSou, which index such channels. A candidate is a share whose post names the title asked for: it is a
folder somebody published, not a verified release — what it holds is known only when its listing is read.

An address is taken only when it is a share of 139 Cloud itself (`yun.139.com`, `caiyun.139.com`, no credentials in it): a
look-alike domain, another cloud, or a title that does not hold the words asked for is no candidate.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

# The indexes read in this order, one after another. A `fallback` index is asked only when the earlier ones found nothing.
DEFAULT_SOURCES: list[dict] = [
    {"kind": "telegram", "channel": "yunpan139"},
    {"kind": "telegram", "channel": "ydypzyfx"},
    {"kind": "pansou", "url": "https://so.252035.xyz/api/search", "channels": ["yunpan139", "ydypzyfx", "Q_dongman"],
     "fallback": True},
    {"kind": "pansou", "url": "https://pansou.app/api/search", "channels": ["yunpan139", "ydypzyfx"], "fallback": True},
]

SHARE_RE = re.compile(r"https?://(?:yun|caiyun)\.139\.com/(?:shareweb/)?(?:#/[wv]/i/|[wv]/i/|m/i\?)([A-Za-z0-9_-]{4,128})", re.I)
_CODE_RE = re.compile(r"(?:提取码|访问码|密码)\s*[:：=]?\s*([A-Za-z0-9]{4,8})")
_HOSTS = frozenset({"yun.139.com", "caiyun.139.com"})


class IndexAnswerError(ValueError):
    """An index answered in a form that is none of its own: `code` says which (`search_index_shape`, `search_index_refused`)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Candidate:
    """A share a post offers: its link, the code the post gives for it, the title of the post, where it was posted and when,
    and which kind of index told of it."""
    link_id: str
    password: str
    title: str
    publisher_url: str
    published_at: str
    found_by: str


def normalize(value: object) -> str:
    """What a title is compared by: its letters and digits, whatever their case."""
    return "".join(each.casefold() for each in str(value) if each.isalnum())


def title_matches(query: str, title: str) -> bool:
    return bool(normalize(query)) and normalize(query) in normalize(title)


def parse_share(address: str, password: str = "") -> tuple[str, str] | None:
    """(the link of a share, its access code) of an address of a public share of 139 Cloud; None for any other address. The
    code is the one given, else the one the address carries."""
    address = html.unescape(address.strip())
    parsed = urlsplit(address)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in _HOSTS or parsed.username or parsed.password:
        return None
    found = SHARE_RE.match(address)
    if found is None:
        return None
    params = parse_qs(parsed.query)
    params.update(parse_qs(parsed.fragment.partition("?")[2]))
    given = password or next(iter(params.get("pwd") or params.get("password") or params.get("passwd") or [""]))
    return found[1], given


class _Messages(HTMLParser):
    """The posts of the web page of a Telegram channel: for each its link, its time, its text and the links in the text."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[dict] = []
        self._post: dict | None = None
        self._depth = 0
        self._text_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        held = dict(attrs)
        classes = (held.get("class") or "").split()
        if tag == "div" and "tgme_widget_message" in classes:
            self._post = {"publisher_url": "https://t.me/" + (held.get("data-post") or ""), "links": []}
            self._parts, self._depth = [], 1
            return
        if self._post is None:
            return
        if tag == "div":
            self._depth += 1
        if "tgme_widget_message_text" in classes:
            self._text_depth = self._depth
        if tag == "a" and self._text_depth and held.get("href"):
            self._post["links"].append(held["href"])
        if tag == "time" and held.get("datetime"):
            self._post["published_at"] = held["datetime"]
        if tag == "br" and self._text_depth:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._post is not None and self._text_depth:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._post is None or tag != "div":
            return
        if self._depth == self._text_depth:
            self._text_depth = 0
        self._depth -= 1
        if self._depth == 0:
            text = "".join(self._parts)
            self.messages.append({**self._post, "title": text.split("\n", 1)[0], "text": text})
            self._post = None


def telegram_candidates(body: str, query: str) -> list[Candidate]:
    """The shares the posts of a channel page offer, of the posts whose text holds the words asked for. A post that offers
    one share gives its code (a post of several shares gives none: whose it is, is not said)."""
    parser = _Messages()
    parser.feed(body)
    found: list[Candidate] = []
    for post in parser.messages:
        if not title_matches(query, post["text"]):
            continue
        shares: dict[str, str] = {}
        for address in [*post["links"], *(each[0] for each in SHARE_RE.finditer(post["text"]))]:
            parsed = parse_share(address)
            if parsed is not None:
                shares.setdefault(parsed[0], address)
        password = ""
        if len(shares) == 1:
            coded = _CODE_RE.search(post["text"])
            password = coded[1] if coded else ""
        title = post["title"].removeprefix("名称：").strip() or query
        for address in shares.values():
            parsed = parse_share(address, password)
            if parsed is not None:
                found.append(Candidate(parsed[0], parsed[1], title, post["publisher_url"], post.get("published_at", ""),
                                       "telegram"))
    return found


def pansou_candidates(payload: object, query: str) -> list[Candidate]:
    """The shares an answer of a PanSou API offers whose title holds the words asked for. An answer that is none of PanSou's,
    or that refuses, is an error: it is not the answer «nothing found»."""
    if not isinstance(payload, dict):
        raise IndexAnswerError("search_index_shape")
    if payload.get("code") not in (None, 0, "0", 200, "200"):
        raise IndexAnswerError("search_index_refused")
    data = payload.get("data", payload)
    if not isinstance(data, dict) or ("results" not in data and "merged_by_type" not in data):
        raise IndexAnswerError("search_index_shape")
    found: list[Candidate] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        for link in item.get("links") or []:
            if not isinstance(link, dict):
                continue
            title = str(link.get("work_title") or item.get("title") or "")
            parsed = parse_share(str(link.get("url") or ""), str(link.get("password") or "")) if title_matches(query, title) else None
            if parsed is None:
                continue
            channel, message = item.get("channel") or "", str(item.get("message_id") or "")
            found.append(Candidate(parsed[0], parsed[1], title, f"https://t.me/{channel}/{message}" if channel and message else "",
                                   str(item.get("datetime") or ""), "pansou"))
    for group in (data.get("merged_by_type") or {}).values():
        for link in group if isinstance(group, list) else ():
            if not isinstance(link, dict):
                continue
            title = str(link.get("note") or link.get("work_title") or "")
            parsed = parse_share(str(link.get("url") or ""), str(link.get("password") or "")) if title_matches(query, title) else None
            if parsed is not None:
                found.append(Candidate(parsed[0], parsed[1], title, str(link.get("source") or ""),
                                       str(link.get("datetime") or ""), "pansou"))
    return found
