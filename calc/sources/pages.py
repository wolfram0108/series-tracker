"""The state of a page of a site and what is to be done with it: whether its table is current, what to do when it is asked to be
read, which pages to forget, what the rows of a read that did not ask everything come to, and what a read gave.

Nothing here reads a page, a clock or a store: the module hands in what it saw (the stored table, the time now, the term of a site,
whether a read goes) and takes back what to answer and what to do.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Callable, Iterable, Mapping, Sequence

from core.contracts import ReleaseTable, TableGetReply, TableReady, TableRow, TableState
from core.contracts.release_table import site_of
from core.contracts.torrent import order_name


# --- the term of a table -----------------------------------------------------------------------------------------------------------

def is_fresh(read_at: str | None, now: datetime, term: timedelta) -> bool:
    """Whether a table read at `read_at` is current at `now` under `term`. A table "from the future" means the clock moved back: it
    is not trusted."""
    try:
        age = now - datetime.fromisoformat(read_at or "")
    except (TypeError, ValueError):
        return False
    return timedelta(0) <= age < term


def fresh_until(read_at: str | None, term: timedelta) -> str | None:
    """The end of the term of a table: its read and the term of its site; none for a table never read (or read at a time that is no time)."""
    if not read_at:
        return None
    try:
        read = datetime.fromisoformat(read_at)
    except ValueError:
        return None
    return (read + term).astimezone(timezone.utc).isoformat()


def ready_of(url: str, read_at: str, term: timedelta) -> TableReady:
    return TableReady(url=url, read_at=read_at, fresh_until=fresh_until(read_at, term))


def fresh_pages(read: Mapping[str, str | None], keys: Iterable[str], term_of: Callable[[str], timedelta]) -> list[TableReady]:
    """The stored tables of the pages `keys` (canonical addresses) with the end of their term, once each; a page with no table read
    is left out. `read` is the time each stored page was read."""
    found: dict[str, TableReady] = {}
    for key in keys:
        if read.get(key):
            found[key] = ready_of(key, read[key], term_of(key))
    return list(found.values())


def fresh_pages_of_site(read: Mapping[str, str | None], site: str, term_of: Callable[[str], timedelta]) -> list[TableReady]:
    """The stored tables of one site with the end of their term: what a change of its term changes."""
    return [ready_of(url, at, term_of(url)) for url, at in read.items() if at and site_of(url) == site]


def forgotten(read: Mapping[str, str | None], listed: set[str], was_listed: set[str], now: datetime,
              term_of: Callable[[str], timedelta]) -> list[str]:
    """The stored pages no source lists, to be forgotten: a page that was listed a moment ago goes at once, a page that never was (a
    preview, a page left from before this was kept) goes when its table is past the term, so that a page the user looked at and is about
    to add is not lost."""
    return [url for url, at in read.items()
            if url not in listed and (url in was_listed or not is_fresh(at, now, term_of(url)))]


# --- what to answer, what to do -----------------------------------------------------------------------------------------------------

def standing(key: str, table: ReleaseTable | None, state: str | None, why: str | None, reading: bool) -> TableGetReply:
    """The page as it stands, with no read: a recount of a layout must not cost a read of the site. A read that goes is `reading`, a
    last read that failed is `failed` with its reason, any other complete table is `ready` however old, and a page nothing was read
    of is `absent`. All of it with the last complete table, when there is one."""
    if reading:
        return TableGetReply(url=key, state=TableState.READING, table=table)
    if state == "failed":
        return TableGetReply(url=key, state=TableState.FAILED, table=table, reason=why)
    if table is not None:
        return TableGetReply(url=key, state=TableState.READY, table=table)
    return TableGetReply(url=key, state=TableState.ABSENT)


class Step(StrEnum):
    """What a request to read a page comes to: the table is current (`READY`), the page waits for the user (`STANDING`: answered as it
    stands), the provider that reads it is not up (`BLOCKED`), a read begins (`LAUNCH`), or one goes already (`GOING`)."""

    READY = "ready"
    STANDING = "standing"
    BLOCKED = "blocked"
    LAUNCH = "launch"
    GOING = "going"


def refresh_step(*, has_table: bool, table_fresh: bool, waits_for_user: bool, by_sync: bool, reading: bool,
                 blocked: str | None) -> Step:
    """What to do with a page asked to be read. A current table is served. The page of a site that waits for the user (a title of the
    provider) is begun by the user's command; a page nobody has read waits for it, and a page that was read is read again by a
    synchronization only (its rows are the setting of its source). Otherwise a read begins, unless one goes or the provider that
    would read the page is not up."""
    if has_table and table_fresh:
        return Step.READY
    if waits_for_user and (not has_table or not by_sync):
        return Step.STANDING
    if reading:
        return Step.GOING
    if blocked is not None:
        return Step.BLOCKED
    return Step.LAUNCH


# --- what a read gives --------------------------------------------------------------------------------------------------------------

def first_repeated_key(rows: Iterable[TableRow]) -> TableRow | None:
    """The first row whose key an earlier row of the table has: a key names one row, so two are a fault of the site."""
    seen: set[str] = set()
    for row in rows:
        if row.key in seen:
            return row
        seen.add(row.key)
    return None


def merged_rows(old: Sequence[TableRow], new: Sequence[TableRow], off: frozenset[str], limited_read: bool) -> list[TableRow]:
    """The rows of a read that did not ask everything: the earlier rows in their places, each replaced by its new row when this read took
    it, and kept as it was when it did not (a player switched off, a line read in part); the new rows that were not there come after them
    in the order the site listed them. The row of a player asked and no longer listed is gone, as in any read."""
    taken = {row.key: row for row in new}
    merged = []
    for row in old:
        if row.key in taken:
            merged.append(taken.pop(row.key))
        elif limited_read or order_name(row.player) in off:
            merged.append(row)
    return merged + [row for row in new if row.key in taken]


def kept_rows(rows: Sequence[TableRow], stored: Sequence[TableRow]) -> list[TableRow]:
    """The rows of the last complete table of every part this read did not get; a row whose video this read listed again is not kept
    twice."""
    parts, keys = {row.part for row in rows} | {""}, {row.key for row in rows}
    return [row for row in stored if row.part not in parts and row.key not in keys]


def read_summary(table: ReleaseTable) -> dict:
    """What a read gave, player by player: its rows by state, the frames of the rows that play, and every cause a row was left without a
    video with, counted. The debugging of a source begins here, not at the rows one by one."""
    players: dict[str, dict] = {}
    causes: dict[tuple[str, str, str], int] = {}
    for row in table.rows:
        held = players.setdefault(row.player, {"player": row.player, "label": row.player_label or row.player, "rows": 0,
                                               "states": {}, "heights": {}})
        held["rows"] += 1
        held["states"][row.state.value] = held["states"].get(row.state.value, 0) + 1
        if row.height:
            held["heights"][str(row.height)] = held["heights"].get(str(row.height), 0) + 1
        if row.state.value != "playable":
            key = (row.player, row.state.value, row.reason or "")
            causes[key] = causes.get(key, 0) + 1
    return {"rows": len(table.rows), "players": list(players.values()),
            "causes": [{"player": player, "state": state, "reason": why, "rows": count}
                       for (player, state, why), count in sorted(causes.items(), key=lambda item: -item[1])],
            "unread": [part.model_dump(mode="json") for part in table.unread]}

