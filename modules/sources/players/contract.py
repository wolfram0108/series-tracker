"""What a player is to a site parser: found on a page, asked once, its answer read into one kind.

Whether to ask again is not a player's business: that is decided in one place, `modules/sources/policy.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Protocol

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


class StreamUnavailable(RuntimeError):
    """The address belongs to a player (or its site) that cannot produce a stream for it now; `transient` marks causes
    that may pass."""

    def __init__(self, reason: str, transient: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.transient = transient
