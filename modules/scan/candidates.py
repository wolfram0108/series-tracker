"""From the references chosen to the rows of media_items: which row stands for each slot and which rows are left over.

Pure functions. A row of media_items is identified by the row of the layout it was made from (series, source, row
key: `core.ids.row_unique_id`), not by its address and date as before. What is already on disk keeps its `unique_id`:
a row of the same slot with the address of the chosen row is the same candidate, and a better version of a downloaded
one gets a row of its own, so that the file that was fetched is never lost under a new resolution.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Collection, Iterable

from core import ids
from core.ignored import covered
from .planner import Slot, is_compilation, slot_of
from .reference import Chosen

# What is stored when a row gives no date; a fixed one, as the sites that give none always had.
NO_DATE = "1970-01-01 12:00:00"
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def stored_date(published: str | None) -> str:
    """The publication date of a row of a table in the form media_items keeps it: naive UTC `YYYY-MM-DD HH:MM:SS`.

    A site that gives a day and no time is placed at noon, not at midnight, so that no time zone moves it to another
    day; a row with no date, or one that cannot be read, takes the fixed default."""
    text = (published or "").strip()
    try:
        if _DAY.fullmatch(text):
            moment = datetime.strptime(text, "%Y-%m-%d").replace(hour=12)
        else:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return NO_DATE
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def is_downloaded(item: dict[str, Any]) -> bool:
    return item.get("status") == "completed" or bool(item.get("final_filename"))


def is_settled(item: dict[str, Any]) -> bool:
    """Fetched or being fetched: a row that must not be deleted or have its resolution rewritten under the download."""
    return is_downloaded(item) or item.get("status") == "downloading" or (item.get("slicing_status") or "none") != "none"


@dataclass(frozen=True)
class Placement:
    """The row of media_items that stands for a chosen slot. `fields` are the columns the scan writes to it."""

    chosen: Chosen
    unique_id: str
    fields: dict[str, Any]
    is_new: bool

    @property
    def slot(self) -> Slot:
        return self.chosen.slot


def place(series_id: int, chosen: Iterable[Chosen], existing: list[dict[str, Any]],
          busy: Collection[str] = ()) -> list[Placement]:
    """A placement for every chosen slot that has a row; slots with the marks `none` and `unfit` have none.

    `busy` are the rows with a task in the queue of downloads: a task is bound to the video it was made for, so a row
    that has one is held like a row that is being fetched, and another video behind it is a row of its own."""
    by_id = {item["unique_id"]: item for item in existing}
    placements = []
    for each in chosen:
        row = each.row
        if row is None:
            kept = _held_row(each, existing)
            if kept is not None:  # the file stands as it is: nothing of the row is rewritten from a layout without it
                placements.append(Placement(each, kept["unique_id"], {}, False))
            continue
        # The height of the reference, not of the row: a row held back while its answer is unsure has none of its own.
        resolution = each.reference.height
        slot = each.slot
        fresh = ids.row_unique_id(series_id, row.source_id, row.row_key)
        here = [item for item in existing if slot_of(item) == slot and not is_compilation(item)]
        known = [item for item in here if item.get("source_url") == row.link]
        moved = by_id.get(fresh)
        # The same row of the layout, numbered otherwise now, is kept in place. So is one that shows another video
        # while nothing was fetched from the row and no task waits for it: there is no file to mix up. A row with a
        # fetched video, or a task for one, that now shows another is the earlier video, which the new one displaces.
        if moved is not None and moved not in known and (
                moved.get("source_url") == row.link or not (is_settled(moved) or moved["unique_id"] in busy)):
            known.append(moved)
        fields = {
            "season": each.reference.season, "episode_start": each.reference.episode, "episode_end": None,
            "part": each.reference.part, "source_url": row.link, "download_url": None, "source_id": row.source_id,
            "row_key": row.row_key, "publication_date": stored_date(row.published),
            "voiceover_tag": row.voice_group or row.voice_name or None, "source_title": row.text or row.row_key}
        best = max(known, key=lambda item: (item.get("resolution") or 0, is_downloaded(item)), default=None)
        if best is not None and (best.get("resolution") or 0) >= (resolution or 0):
            placements.append(Placement(each, best["unique_id"], fields, False))
            continue
        open_ones = [item for item in known if not is_settled(item)]
        if open_ones:
            target = max(open_ones, key=lambda item: item.get("resolution") or 0)
            placements.append(Placement(each, target["unique_id"], {**fields, "resolution": resolution}, False))
            continue
        if known:
            unique_id = ids.upgrade_row_unique_id(series_id, row.source_id, row.row_key, resolution, row.link)
        elif fresh in by_id:  # the id of the row holds the video fetched before: this video is a row of its own
            unique_id = ids.version_row_unique_id(series_id, row.source_id, row.row_key, row.link)
        else:
            unique_id = fresh
        placements.append(Placement(each, unique_id, {**fields, "resolution": resolution}, unique_id not in by_id))
    return placements


def new_row(series_id: int, placement: Placement, ignored: dict[int, set[int]] | None) -> dict[str, Any]:
    """The whole row to insert for a placement that has no row yet; a switched-off episode is born switched off."""
    fields = placement.fields
    return {**fields, "series_id": series_id, "unique_id": placement.unique_id,
            "is_ignored_by_user": covered(fields["season"], {fields["episode_start"]}, ignored or {})}


def on_disk(existing: Iterable[dict[str, Any]]) -> set[tuple[int, str]]:
    """The rows of the layout (source id, row key) whose video is downloaded: the references that are held while a read
    does not bring their rows."""
    return {(item["source_id"], item["row_key"]) for item in existing
            if is_downloaded(item) and item.get("source_id") is not None and item.get("row_key") is not None}


def _held_row(chosen: Chosen, existing: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The downloaded row of a reference held without its row in the layout, the best of them if there are several."""
    reference = chosen.reference
    if reference.source_id is None or reference.row_key is None:
        return None
    mine = [item for item in existing if slot_of(item) == chosen.slot and not is_compilation(item) and is_downloaded(item)
            and (item.get("source_id"), item.get("row_key")) == (reference.source_id, reference.row_key)]
    return max(mine, key=lambda item: item.get("resolution") or 0, default=None)


def obsolete(existing: list[dict[str, Any]], kept: Collection[str], busy: Collection[str]) -> list[str]:
    """The rows that stand for nothing: not the winner of any slot, not fetched, with no download under way.

    `busy` are the rows that have a task in the queue of downloads: deleting one would leave the task without a row."""
    return [item["unique_id"] for item in existing
            if item["unique_id"] not in kept and not is_settled(item) and item["unique_id"] not in busy]


def rows_after(existing: list[dict[str, Any]], inserts: list[dict[str, Any]], updates: Iterable[tuple[str, dict[str, Any]]],
               deletes: Collection[str]) -> list[dict[str, Any]]:
    """The rows of the series as they will stand once the inserts, updates and deletes are written; what the planner reads."""
    gone, changes = set(deletes), dict(updates)
    rows = [{**item, **changes.get(item["unique_id"], {})} for item in existing if item["unique_id"] not in gone]
    rows.extend(dict(row) for row in inserts)
    return rows
