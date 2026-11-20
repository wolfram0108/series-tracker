"""The library of the media: the folders of the disk it lists, and the move of the folder of a series (I-59)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .frozen import Frozen


class DirectoriesGet(BaseModel):
    """`library.directories.list`: the folder to list (the root of the disk when none is given)."""

    path: str | None = None


class DirectoryItem(Frozen):
    name: str
    path: str
    type: Literal["directory"] = "directory"


class DirectoryListing(Frozen):
    """`library.directories.list`: the folder as the module normalized it and the folders in it, by name."""

    path: str
    items: tuple[DirectoryItem, ...]


class RelocationOf(BaseModel):
    """`library.relocation.get` and `library.relocation.apply`: the series whose move is asked about or made."""

    series_id: int


class RelocationPlan(BaseModel):
    """`library.relocation.plan`: the folder a save of the card moves the series to."""

    series_id: int
    new_path: str


class RelocationPath(Frozen):
    """`library.relocation.get` and `library.relocation.plan`: the folder of the move that waits or is under way (none: no move)."""

    new_path: str | None = None


class Relocated(Frozen):
    """`library.relocation.apply`: whether a planned move was made."""

    moved: bool


class RelocationPlanned(Frozen):
    """The event `library.relocation.planned`: a plan was written, or dropped (`new_path` none)."""

    series_id: int
    new_path: str | None = None


class RelocationStarted(Frozen):
    """The event `library.relocation.started`: the files of a series begin to move."""

    series_id: int


class RelocationFinished(Frozen):
    """The event `library.relocation.finished`: the move ended; `message` is its reason code on success, the cause of the failure otherwise."""

    series_id: int
    success: bool
    message: str
