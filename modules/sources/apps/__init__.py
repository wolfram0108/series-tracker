"""The video applications read through their own APIs: a title is found by a search in the application, its card lists the
lines of episodes, and the stream of an episode is asked of the application right before it is downloaded.

An application has no page a user could give: a source of it is the address `app://<application>/<card>` that its search
gives. The profile of an application (its host and what its client says of itself) is private and comes from the
environment of the process (`ST_APP_<NAME>`, JSON); without it the application is not read.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol

SCHEME = "app"

# How long a client believes what it learned of the application itself (the parsers of its lines, its hosts, the
# configuration of its resolvers). The application publishes them anew when it changes them; a client that never asks again
# names a parser or a host that is gone until the process restarts. The profile may say `metadata_ttl` (seconds).
LEARNED_FOR_S = 600.0
LEARNED_FOR_MIN_S = 30.0


def learned_for(settings: Mapping[str, str], default: float = LEARNED_FOR_S) -> float:
    """The term of what a client learned of its application: `metadata_ttl` of the profile, not under half a minute."""
    try:
        return max(LEARNED_FOR_MIN_S, float(settings.get("metadata_ttl") or default))
    except ValueError:
        return default


class Lease:
    """What a client learned of the application is believed while its lease lasts. `renew` takes the term again (a shorter
    one after a failed asking, so that a host that is down is not asked at every request), `lapse` ends it at once."""

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.seconds, self._clock, self._until = seconds, clock, float("-inf")

    @property
    def live(self) -> bool:
        return self._clock() < self._until

    def renew(self, seconds: float | None = None) -> None:
        self._until = self._clock() + (self.seconds if seconds is None else seconds)

    def lapse(self) -> None:
        self._until = float("-inf")


# A refusal that comes of the session of the guest, and one that no guest escapes: a quota, a VIP check or a list of allowed
# addresses refuses the request whoever asks, so opening another session is no cure for it.
_SESSION_ENDED = re.compile(r"(?:token|session|登录|会话|令牌).{0,40}(?:expired|invalid|过期|失效|无效)|请先登录|未登录", re.I)
_WHOEVER_ASKS = re.compile(r"quota|配额|次数|上限|vip|会员|白名单|whitelist", re.I)


def whoever_asks(message: str) -> bool:
    return bool(_WHOEVER_ASKS.search(message))


def session_ended(code: object, message: str, status: int | None = None) -> bool:
    """The application says the session of the guest is over: HTTP 401, the code 401 or words of an ended session."""
    if whoever_asks(message):
        return False
    return status == 401 or str(code) == "401" or bool(_SESSION_ENDED.search(message))


def daily_quota(message: str) -> bool:
    """Only an explicit exhausted daily allowance, not a generic refusal or a statement of the plan's limit."""
    return bool(re.search(r"今日|当天|每日", message) and (re.search(r"已达|已超", message) or
                re.search(r"超过(?:今日|当天|每日).{0,12}(?:限制|限额|上限).{0,12}(?:明日|明天)再试", message))
                and re.search(r"次数|点数|限额|上限|限制", message)) or bool(
                    re.search(r"(?:daily\s+(?:limit|quota)|quota)\s+(?:(?:is|has been)\s+)?(?:exceeded|exhausted)", message, re.I))


class AppError(RuntimeError):
    """The application refused or could not be asked: a coded reason; `transient` marks causes that may pass."""

    def __init__(self, why: str, transient: bool = False, *, status: int | None = None,
                 retry_after: float | None = None) -> None:
        super().__init__(why)
        self.reason = why
        self.transient = transient
        self.status, self.retry_after = status, retry_after
        try:
            message = json.loads(why).get("message", "")
        except (ValueError, AttributeError):
            message = ""
        self.daily_quota = daily_quota(str(message))
ENV_PREFIX = "ST_APP_"


@dataclass(frozen=True)
class AppProfile:
    """What the client of an application says of itself, as its own build does, and the device it is known by."""
    name: str          # the name of the application in the addresses of its cards (`banri`)
    label: str         # its name as the owner sees it: its own and its Latin one (`半日 (Banri)`)
    protocol: str      # the family of its API (`app99`)
    settings: Mapping[str, str]


def profiles_of(environ: Mapping[str, str]) -> list[AppProfile]:
    """The applications the environment describes: `ST_APP_<NAME>` holds a JSON object with `label` (the name the
    application shows), `latin` (that name in Latin letters), `protocol` and the settings of the protocol; a value that is
    no such object is a fault of the environment and stops the start. Both names are required: the owner tells the
    applications apart by the Latin one, so no application is shown by its own name alone. `player_agent`, of any
    protocol, is the agent the application's own player asks its streams with (players/app_lines.py)."""
    found = []
    for key, value in sorted(environ.items()):
        if not key.startswith(ENV_PREFIX) or not value.strip():
            continue
        data = json.loads(value)
        if not isinstance(data, dict) or not all(data.get(each) for each in ("protocol", "label", "latin")):
            raise ValueError(f"{key}: an application needs a JSON object with `protocol`, `label` and `latin`")
        settings = {k: json.dumps(v) if isinstance(v, (list, dict)) else str(v) for k, v in data.items()}
        found.append(AppProfile(name=key[len(ENV_PREFIX):].lower(), label=f"{data['label']} ({data['latin']})",
                                protocol=str(data["protocol"]), settings=settings))
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
    # The address stands under the key the parser names, or is the whole `data` of its answer.
    target = answer.get(key) or inner.get(key) or (answer["data"] if isinstance(answer.get("data"), str) else None)
    return (target, words) if isinstance(target, str) and target.startswith(("http://", "https://")) else ("", words)


@dataclass(frozen=True)
class AppPage:
    """A page of the search: the cards as the application gives them ({id, name, sub, year, area, class, remarks,
    blurb, pic}), the number of pages and of cards when the application says them."""
    cards: list[dict]
    pages: int | None = None
    total: int | None = None
    # What an application says of an answer that holds no card (a reason: a code and its facts), when it has more to say than
    # «none» — where it looked, what to do. An answer with cards has none.
    note: str = ""


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

