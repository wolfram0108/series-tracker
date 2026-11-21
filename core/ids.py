"""Torrent id formula is a data constraint (Р-10): releases already written must keep getting the same id
(tests/test_scan_ids.py), or everything looks new and is downloaded again (finding 13). Id formulas of the
old path's media items were removed together with their table (2026-11-17)."""
from __future__ import annotations

import hashlib


def torrent_id(link: str, date_time: str | None) -> str:
    """md5(link + date_time)[:16]; date_time is a string in the historical
    format of its own tracker (modules/sources/dates.py)."""
    return hashlib.md5(f"{link}{date_time or ''}".encode()).hexdigest()[:16]
