"""Torrent content selection — pure functions (like pipeline.py).

Single source of truth for "download / don't download". The rule is an
allowlist: only a file that the system recognized as a series episode and
that the user did not exclude is downloaded. Everything else (non-video
files, files without a resolved season — renaming does not create them,
files without an episode number) gets qBit priority 0 before the download
starts.

The exclusion memory lives in the series (seasons + episode numbers), not
in the file rows: a release gets replaced and files are recreated, so the
selection must survive the replacement. Here it is only applied.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from core.contracts import SeriesCard
from core.ignored import covered, episodes_of, parse_memory

# qBittorrent file priority: 0 — do not download, 1 — normal.
PRIORITY_SKIP = 0
PRIORITY_NORMAL = 1

REASON_USER = "user"
REASON_UNRECOGNIZED = "unrecognized"


@dataclass
class Selection:
    """Priority delta for one torrent."""
    to_disable: list[int] = field(default_factory=list)
    to_enable: list[int] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    wanted_empty: bool = False


def memory_of(series: SeriesCard) -> tuple[set[int], dict[int, set[int]]]:
    """The exclusion memory of a series: the seasons and the episode numbers the user left out; a memory that cannot be read is empty."""
    try:
        seasons = {int(s) for s in json.loads(series.ignored_seasons or "[]")}
    except (json.JSONDecodeError, TypeError, ValueError):
        seasons = set()
    try:
        episodes = parse_memory(json.loads(series.ignored_episodes or "{}"))
    except (json.JSONDecodeError, TypeError):
        episodes = {}
    return seasons, episodes


def is_wanted(extracted: dict | None, ignored_seasons: set[int],
              ignored_episodes: dict[int, set[int]]
              ) -> tuple[bool, str | None]:
    """Whether the file is wanted. Returns (wanted, exclusion reason)."""
    meta = extracted or {}
    if meta.get("is_extra"):
        return True, None
    season = meta.get("season")
    if season is not None and int(season) in ignored_seasons:
        return False, REASON_USER
    episodes = episodes_of(meta)
    if not episodes:
        return False, REASON_UNRECOGNIZED
    if season is None:
        return True, None
    if covered(season, episodes, ignored_episodes):
        return False, REASON_USER
    return True, None


def _index_of(qb_file: dict, position: int) -> int:
    value = qb_file.get("index")
    return int(value) if value is not None else position


def resolve(db_rows: list[dict], qb_files: list[dict]) -> Selection:
    """DB rows + qBit files -> which indices to enable / disable.

    Matched by qb_file_index; the name is a fallback for rows written before
    the index existed (the name mutates on rename, the index does not).
    """
    by_index: dict[int, dict] = {}
    by_name: dict[str, dict] = {}
    for row in db_rows:
        idx = row.get("qb_file_index")
        if idx is not None:
            by_index[int(idx)] = row
        for name in (row.get("renamed_path"), row.get("original_path")):
            if name:
                by_name[name] = row

    selection = Selection()
    matched: set[int] = set()
    wanted = 0
    for position, qb_file in enumerate(qb_files):
        index = _index_of(qb_file, position)
        row = by_index.get(index) or by_name.get(qb_file.get("name") or "")
        if row is None:
            # Not in the registry: non-video, or the rules gave no season —
            # we never download such files (allowlist).
            want = False
        else:
            matched.add(id(row))
            want = not row.get("is_excluded")
        if want:
            wanted += 1
        current = qb_file.get("priority")
        if want and current == PRIORITY_SKIP:
            selection.to_enable.append(index)
        elif not want and current != PRIORITY_SKIP:
            selection.to_disable.append(index)

    for row in db_rows:
        if id(row) not in matched:
            selection.unmatched.append(
                row.get("renamed_path") or row.get("original_path") or "")
    selection.wanted_empty = bool(qb_files) and wanted == 0
    return selection
