"""The search of the video applications (modules/sources/apps): a title has no page there, it is found by the search of
the application, and the card the user picks is a source by its address (`app://<application>/<card>`).

The cards come as the application gives them, in its order: nothing is chosen, ranked or left out here.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class AppSearchRequest(BaseModel):
    query: str
    page: int = Field(default=1, ge=1)
    # One application by its name; every application the service reads when empty.
    app: str = ""


class AppCard(BaseModel):
    """A card of a title as the search of its application gives it; `url` is the address it is a source by (empty when
    the application gave the card no number to be asked by again)."""

    app: str
    id: str
    url: str
    name: str = ""
    sub: str = ""          # the other name of the title the application gives (an English one, a slogan)
    year: str = ""
    area: str = ""
    genres: str = ""
    remarks: str = ""      # what the application says of the episodes out ("更新至第499集")
    blurb: str = ""
    pic: str = ""          # the poster, an address of the application's host


class AppResults(BaseModel):
    """One application's answer to the search: a page of its cards, or the reason it gave none."""

    app: str
    label: str
    page: int = 1
    pages: int | None = None   # how many pages the application says there are; None when it does not say
    total: int | None = None
    cards: list[AppCard] = Field(default_factory=list)
    reason: str | None = None


class AppSearchReply(BaseModel):
    apps: list[AppResults] = Field(default_factory=list)
