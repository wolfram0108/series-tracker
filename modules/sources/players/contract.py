"""What a player is to a site parser: found on a page, asked once, its answer read into one kind.

Whether to ask again is not a player's business: that is decided in one place, `modules/sources/policy.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import httpx


class Kind(str, Enum):
    PLAYABLE = "playable"
    NOT_FOUND = "not_found"
    RESTRICTED = "restricted"
    NO_FILE = "no_file"
    NOT_PLAYER = "not_player"
    HTTP_ERROR = "http_error"
    NO_ANSWER = "no_answer"


@dataclass(frozen=True)
class PlayerRef:
    name: str
    url: str
    raw_url: str = ""


@dataclass(frozen=True)
class Answer:
    """What came back from one request; a transport failure is an answer without a status."""
    status: int | None
    text: str = ""
    retry_after: float | None = None
    error: str = ""
    # A player that has to look through several entries states its own reading instead of one text to read.
    reading: "Reading | None" = None
    # Where the request finally got its answer from (after redirects); not a part of what the answer says.
    final_url: str = field(default="", compare=False)


@dataclass(frozen=True)
class Reading:
    kind: Kind
    status: int = 0
    resolutions: tuple[int, ...] = ()
    title: str = ""
    message: str = ""
    # Signed addresses of the files by height; out of repr and comparison so that they reach neither a log nor a test.
    files: tuple[tuple[int, str], ...] = field(default=(), repr=False, compare=False)


class Player(Protocol):
    name: str
    series_scope: bool

    def find(self, text: str, page_url: str) -> PlayerRef | None: ...

    async def fetch(self, ref: PlayerRef, http: "httpx.AsyncClient", page_url: str) -> Answer:
        """One request, no retries; a failure of the transport comes back as an `Answer`, not as an exception."""
        ...

    def read(self, answer: Answer) -> Reading: ...

    def video_of(self, url: str) -> str:
        """The video a link of this player names, as its host numbers it; '' when the link names a place on its page
        instead (the same on every page, so that one video shown by two pages is known as one)."""
        ...


# The page that embeds the players, as the hosts see it: VK asks the API on behalf of this domain.
EMBED_PAGE = "https://1.ruchime.org/"


@runtime_checkable
class StreamPlayer(Protocol):
    """A player that gives the stream of a link it owns. A download asks the player right before it begins: the
    addresses of the files expire, so they are not kept."""

    name: str

    def owns(self, url: str) -> bool: ...

    async def stream(self, url: str, http: "httpx.AsyncClient", max_height: int | None, pacer: Any) -> dict:
        """{stream_url, headers, progressive?} of the best file within `max_height` (the best of all without it).
        Raises StreamUnavailable when the player owns the link but has no stream for it now."""
        ...


class StreamUnavailable(RuntimeError):
    """The address belongs to a player (or its site) that cannot produce a stream for it now; `transient` marks causes
    that may pass."""

    def __init__(self, reason: str, transient: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.transient = transient
