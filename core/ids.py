"""The formula of the id of a torrent, a constraint of the data (Р-10): the releases already written must get the same id
(tests/test_scan_ids.py). A changed formula would make everything look new and download it again (finding 13). The
formulas of the ids of the media items of the old path left with their table (2026-11-17)."""
from __future__ import annotations

import hashlib


def torrent_id(link: str, date_time: str | None) -> str:
    """md5(link + date_time)[:16]; date_time — строка в историческом
    формате СВОЕГО трекера (modules/sources/dates.py)."""
    return hashlib.md5(f"{link}{date_time or ''}".encode()).hexdigest()[:16]
