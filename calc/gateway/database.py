"""The tables of the database the administrator tab shows and clears."""
from __future__ import annotations

from typing import Iterable

# The tables the tab does not show: the credentials and the live cookies of the trackers.
EXCLUDED_TABLES = frozenset({"auth", "tracker_sessions"})


def shown_tables(tables: Iterable[str]) -> list[str]:
    """The tables of the database the tab lists."""
    return [table for table in tables if table not in EXCLUDED_TABLES]


def is_shown(name: str, tables: Iterable[str]) -> bool:
    """Whether a table is one the tab may read or clear: it is in the database and is not excluded."""
    return name not in EXCLUDED_TABLES and name in set(tables)
