"""The video applications read through their own APIs: a title is found by a search in the application, its card lists the
lines of episodes, and the stream of an episode is asked of the application right before it is downloaded.

An application has no page a user could give: a source of it is the address `app://<application>/<card>` that its search
gives. The profile of an application (its host and what its client says of itself) is private and comes from the
environment of the process (`ST_APP_<NAME>`, JSON); without it the application is not read.
"""
from __future__ import annotations

import json
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

