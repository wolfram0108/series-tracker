"""Pure count of the library: which paths it admits, how a folder is listed, and where the files of a series go when it moves.

The module `library` touches the disk and the client; this says what the paths mean.
"""
from __future__ import annotations

import os
from typing import Iterable, Sequence

from core.contracts import DirectoryItem, DirectoryListing
from core.reasons import reason


def forbidden(roots: Sequence[str] | None, path: str) -> str | None:
    """The reason a path is refused when it lies outside the roots the library admits (none: the whole disk); none when it is admitted."""
    if roots is None:
        return None
    if any(path == root or path.startswith(root.rstrip("/") + "/") for root in roots):
        return None
    return reason("path_forbidden", path=path)


def destination_refusal(new_path: str, roots: Sequence[str] | None) -> str | None:
    """The reason a folder is refused as the place a series moves to: it is not an absolute path, or the library does not admit it."""
    if not os.path.isabs(new_path):
        return reason("save_path_relative", path=new_path)
    return forbidden(roots, os.path.normpath(new_path))


def folder_of(raw: str | None) -> str:
    """The folder to list as the library normalizes it before any check (the root when none is given)."""
    return os.path.normpath(raw or "/")


def listing_of(path: str, entries: Iterable[tuple[str, bool]]) -> DirectoryListing:
    """The folders among the entries of a folder (name, is it a folder), by name without regard to case."""
    items = [DirectoryItem(name=name, path=os.path.join(path, name)) for name, is_folder in entries if is_folder]
    items.sort(key=lambda item: item.name.lower())
    return DirectoryListing(path=path, items=tuple(items))


def moves_of(old_base: str, new_base: str, names: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """Each file of a series (its name under the folder) as the place it is and the place it goes to."""
    return tuple((os.path.join(old_base, name), os.path.join(new_base, name)) for name in names)
