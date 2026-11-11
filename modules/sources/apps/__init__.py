"""The video applications read through their own APIs: a title is found by a search in the application, its card lists the
lines of episodes, and the stream of an episode is asked of the application right before it is downloaded.

An application has no page a user could give: a source of it is the address `app://<application>/<card>` that its search
gives. The profile of an application (its host and what its client says of itself) is private and comes from the
environment of the process (`ST_APP_<NAME>`, JSON); without it the application is not read.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Mapping, Protocol

SCHEME = "app"


class AppError(RuntimeError):
    """The application refused or could not be asked: a coded reason; `transient` marks causes that may pass."""

    def __init__(self, why: str, transient: bool = False) -> None:
        super().__init__(why)
        self.reason = why
        self.transient = transient
ENV_PREFIX = "ST_APP_"


@dataclass(frozen=True)
class AppProfile:
    """What the client of an application says of itself, as its own build does, and the device it is known by."""
    name: str          # the name of the application in the addresses of its cards (`banri`)
    label: str         # its name as the application shows it (`半日`)
    protocol: str      # the family of its API (`app99`)
    settings: Mapping[str, str]


def profiles_of(environ: Mapping[str, str]) -> list[AppProfile]:
    """The applications the environment describes: `ST_APP_<NAME>` holds a JSON object with `label`, `protocol` and the
    settings of the protocol; a value that is no such object is a fault of the environment and stops the start."""
    found = []
    for key, value in sorted(environ.items()):
        if not key.startswith(ENV_PREFIX) or not value.strip():
            continue
        data = json.loads(value)
        if not isinstance(data, dict) or not data.get("protocol") or not data.get("label"):
            raise ValueError(f"{key}: an application needs a JSON object with `protocol` and `label`")
        found.append(AppProfile(name=key[len(ENV_PREFIX):].lower(), label=str(data["label"]),
                                protocol=str(data["protocol"]), settings={k: str(v) for k, v in data.items()}))
    return found


def card_address(app: str, card: str) -> str:
    return f"{SCHEME}://{app}/{card}"


# --- what every protocol gives: the card with its lines, the stream of an episode, a page of the search ---------------

@dataclass(frozen=True)
class AppItem:
    """An episode of a line: its place in the line, its label as the application gives it, its token."""
    place: int
    label: str
    token: str


@dataclass(frozen=True)
class AppLine:
    """A line of a card: its code, its name in the application, whether it lists its playlists itself (no parse), the
    parsers it allows, its episodes."""
    code: str
    name: str
    listed: bool
    parsers: str
    items: tuple[AppItem, ...]


@dataclass(frozen=True)
class AppCardData:
    names: list[str]
    year: str
    lines: list[AppLine]


@dataclass(frozen=True)
class AppStream:
    """The address of the stream of an episode now, with the headers its host wants."""
    address: str
    headers: dict[str, str] = field(default_factory=dict)


def listed_lines(codes: str, lists: str) -> list[tuple[str, str, tuple[AppItem, ...]]]:
    """(the name of a line on its card, its code, its episodes) of a card that lists its lines as a catalogue of films
    does: the codes apart by `$$$`, and for each code `<label>$<token>#...`. A code a card gives twice names its second
    line `<code>.2` (and so on): a line is one of its card by its name. A line without an episode is not listed."""
    found, seen = [], {}
    for code, listed in zip(codes.split("$$$"), lists.split("$$$")):
        code, items = code.strip(), []
        for item in listed.split("#"):
            label, apart, token = item.partition("$")
            if apart and token.strip():
                items.append(AppItem(len(items) + 1, label.strip(), token.strip()))
        if not code or not items:
            continue
        seen[code] = seen.get(code, 0) + 1
        found.append((code if seen[code] == 1 else f"{code}.{seen[code]}", code, tuple(items)))
    return found


# The codes by which a parser says it made the address of the episode (the clients the applications are read by state
# one of these on success; a number of an HTTP refusal or a word of failure in its place is the parser saying no).
_PARSED = frozenset({"0", "1", "200", "true", "ok", "success"})


def advertised_headers(said: object) -> dict[str, str]:
    """The headers a player or a parser of an application names for the stream: its agent (`user_agent`) and its
    `header`/`headers` (an object, or JSON of one). A name is one header whatever its case; what is no header is left out."""
    if not isinstance(said, dict):
        return {}
    found: dict[str, str] = {}

    def put(name: object, value: object) -> None:
        name = str(name).strip()
        if name and re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) and "\r" not in str(value) and "\n" not in str(value):
            for held in [held for held in found if held.lower() == name.lower()]:
                del found[held]
            found[name] = str(value)

    agent = said.get("user_agent") or said.get("userAgent")
    if agent:
        put("User-Agent", agent)
    for each in (said.get("header"), said.get("headers")):
        if isinstance(each, str):
            try:
                each = json.loads(each)
            except ValueError:
                each = None
        if isinstance(each, dict):
            for name, value in each.items():
                put(name, value)
    return found


def parsed_address(answer: object, key: str = "url") -> tuple[str, str]:
    """(the address of the stream a parser's JSON answer names, what the parser says in words). A parser that states a
    code states whether it parsed, and its refusal may name an address as well — a quota spent comes with one that leads
    nowhere: the address of a refusal is no stream, and the words of the refusal are the answer."""
    if not isinstance(answer, dict):
        return "", ""
    inner = answer.get("data") if isinstance(answer.get("data"), dict) else {}
    words = str(answer.get("msg") or answer.get("message") or inner.get("msg") or inner.get("message") or "")
    for said in (answer, inner):
        code = said.get("code")
        if said.get("success") in (False, "false") or (code is not None and str(code).lower() not in _PARSED):
            return "", words or f"code {code}"
    target = answer.get(key) or inner.get(key)
    return (target, words) if isinstance(target, str) and target.startswith(("http://", "https://")) else ("", words)


@dataclass(frozen=True)
class AppPage:
    """A page of the search: the cards as the application gives them ({id, name, sub, year, area, class, remarks,
    blurb, pic}), the number of pages and of cards when the application says them."""
    cards: list[dict]
    pages: int | None = None
    total: int | None = None


class AppClient(Protocol):
    """The client of the API of one application, whatever its protocol (app99.py, appdrama.py)."""
    profile: AppProfile

    async def card(self, card_id: str) -> AppCardData: ...

    async def resolve(self, line: str, token: str, card: str = "") -> AppStream:
        """The stream of an episode now. `card` is the card the row of the episode is of: a protocol whose card holds
        what the stream is asked with (the address of the parser of each episode) asks the card again when it no longer
        holds it."""
        ...

    async def search_page(self, query: str, page: int) -> AppPage: ...

