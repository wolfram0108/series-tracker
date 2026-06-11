"""Anilibria через официальный публичный API (решение Р-9).

Вместо рендера их Vue-фронтенда браузером — один GET
`/api/v1/anime/releases/{alias}`: названия, раздачи с качествами,
infohash готовым полем (стыкуется с Р-2), честные ISO-даты.
Документация: anilibria.top/api/docs; ключей/регистрации нет.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from . import dates
from .parsers import SourceParseError, _release

_ALIAS_RE = re.compile(r"/(?:release|anime/releases/release)/([^/?#]+)")


def alias_from_url(url: str) -> str:
    m = _ALIAS_RE.search(url)
    if not m:
        raise SourceParseError(f"Anilibria: alias не извлечён из URL {url!r}")
    return m.group(1)


def api_base_from_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/api/v1"


def release_to_result(data: dict) -> dict:
    """JSON релиза API -> контракт sources.parse."""
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
            hash=t.get("hash"),                      # infohash готовым полем
            label=t.get("label"),
        ))
    return {"title": {"ru": name.get("main"), "en": name.get("english")},
            "releases": releases,
            "extra": {"is_ongoing": data.get("is_ongoing"),
                      "episodes_total": data.get("episodes_total")}}
