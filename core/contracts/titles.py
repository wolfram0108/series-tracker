"""The search of the titles a provider of the socket reads (contracts/provider_socket.md): a title has no page of its own, it is found
by the search of the provider, and the line of a title the user picks is a source by its address (`title://<site>/<id>/<line>`).

The titles come in the order of the provider, which ranks nothing; it gives only what can be chosen (a title with no line it plays is
no source) and says how many of the page it left out (`without_lines`), so that nothing is lost in silence. Nothing is chosen, ranked
or left out here. What reaches the title (which doors, which protocols) is the provider's own business: nothing of it is in these
messages.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .frozen import Frozen


class TitleSearchRequest(BaseModel):
    query: str
    page: int = Field(default=1, ge=1)


class TitleLine(Frozen):
    """A line of a title: the archive its episodes come from, and the one thing a series takes as its source. `url` is the address it
    is a source by; `rows` is how many it holds when the provider knows, `routes` how many independent ways to it the provider
    has (a reserve: nothing in the tracker depends on it). `aka` are the other addresses the same line was issued under: a title merged
    into this one keeps its old identifier, so a source stored under it is this line too (a line is compared by `url` and `aka`)."""

    code: str
    url: str
    label: str = ""
    rows: int | None = None
    routes: int = 0
    aka: tuple[str, ...] = Field(default_factory=tuple)


class TitleCard(Frozen):
    """A title as the search of its provider gives it. A title is no source: each of its `lines` is."""

    id: str
    name: str = ""
    sub: str = ""          # the other name of the title the provider gives (an English one, a slogan)
    year: str = ""
    area: str = ""
    genres: str = ""
    remarks: str = ""      # what the source says of the episodes out ("更新至第499集")
    blurb: str = ""
    pic: str = ""          # the poster, an address of the source's host
    lines: tuple[TitleLine, ...] = Field(default_factory=tuple)


class TitleSearchReply(Frozen):
    """A page of the search: the titles, how many pages and titles the provider says there are and whether a page follows (None when
    it does not say), how many titles of the page it left out for having no line it plays, the codes of what the answer lacks (a
    part of the sources did not answer) and the reason it gave no title."""

    titles: tuple[TitleCard, ...] = Field(default_factory=tuple)
    page: int = 1
    pages: int | None = None
    total: int | None = None
    more: bool | None = None  # whether a page follows this one, when the provider says; None: it does not
    without_lines: int = 0  # the titles of this page left out because none of their lines is one the provider plays
    notes: tuple[str, ...] = Field(default_factory=tuple)
    reason: str | None = None
