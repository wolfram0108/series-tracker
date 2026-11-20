"""Anilibria via its official public API (decision Р-9).

Instead of rendering their Vue frontend in a browser, a single GET to
`/api/v1/anime/releases/{alias}` gives the titles, the releases with their
qualities, the infohash as a ready field (matches Р-2), and honest ISO dates.
Docs: anilibria.top/api/docs; no keys or registration.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from core.reasons import reason

from . import dates
from .parsers import SourceParseError, _release

_ALIAS_RE = re.compile(r"/(?:release|anime/releases/release)/([^/?#]+)")


def alias_from_url(url: str) -> str:
    m = _ALIAS_RE.search(url)
    if not m:
        raise SourceParseError(reason("anilibria_no_alias", url=url))
    return m.group(1)


def api_base_from_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/api/v1"


def release_to_result(data: dict) -> dict:
    """Convert an API release JSON to the sources.parse contract."""
    name = data.get("name") or {}
    releases = []
    for t in data.get("torrents") or []:
        quality_parts = []
        if isinstance(t.get("quality"), dict):
            quality_parts.append(t["quality"].get("value"))
        if isinstance(t.get("codec"), dict):
            quality_parts.append(t["codec"].get("label"))
        releases.append(_release(
            magnet=t.get("magnet"),
            date_marker=dates.anilibria_date(t["updated_at"]),
            episodes=t.get("description"),          # '1-17'
            quality=" • ".join(p for p in quality_parts if p) or None,
            hash=t.get("hash"),                      # infohash as a ready field
            label=t.get("label"),
        ))
    return {"title": {"ru": name.get("main"), "en": name.get("english")},
            "releases": releases,
            "extra": {"is_ongoing": data.get("is_ongoing"),
                      "episodes_total": data.get("episodes_total")}}
