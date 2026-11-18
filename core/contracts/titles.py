"""The search of the titles a provider of the socket reads (contracts/provider_socket.md): a title has no page of its own, it is found
by the search of the provider, and the line of a title the user picks is a source by its address (`title://<site>/<id>/<line>`).

The titles come as the provider gives them, in its order: nothing is chosen, ranked or left out here. What reaches the title
(which doors, which protocols) is the provider's own business: nothing of it is in these messages.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class TitleSearchRequest(BaseModel):
    query: str
    page: int = Field(default=1, ge=1)


class TitleLine(BaseModel):
    """A line of a title: the archive its episodes come from, and the one thing a series takes as its source. `url` is the address it
    is a source by; `rows` is how many it holds when the provider knows, `routes` how many independent ways to it the provider
    has (a reserve: nothing in the tracker depends on it)."""

    code: str
    url: str
    label: str = ""
    rows: int | None = None
    routes: int = 0


class TitleCard(BaseModel):
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
    lines: list[TitleLine] = Field(default_factory=list)


class TitleSearchReply(BaseModel):
    """A page of the search: the titles, how many pages and titles the provider says there are (None when it does not say), the
    codes of what the answer lacks (a part of the sources did not answer) and the reason it gave no title."""

    titles: list[TitleCard] = Field(default_factory=list)
    page: int = 1
    pages: int | None = None
    total: int | None = None
    notes: list[str] = Field(default_factory=list)
    reason: str | None = None
