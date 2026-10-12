"""Хосты доставки: канонизация и порядок кандидатов.

Ссылка раздачи — ИДЕНТИЧНОСТЬ (входит в torrent_id, Р-10): менять её
нельзя, иначе ложное «всё новое» и перекачка (находка 13). Поэтому хост
из ссылки — лишь первый кандидат доставки, а не адрес: живой хост
выбирается в момент запроса из зеркал трекера.

Знание о префиксах доставки лежит здесь одной копией — его спрашивают и
транспорт (delivery), и резолв трекера по URL (repository).
"""
from __future__ import annotations

from typing import Iterable
from urllib.parse import urlparse

# Delivery-only subdomains: the tracker serves the same paths without them.
DELIVERY_PREFIXES = ("dl.", "www.")


def canonical_host(netloc: str) -> str:
    for prefix in DELIVERY_PREFIXES:
        netloc = netloc.removeprefix(prefix)
    return netloc


def candidates(url: str, mirrors: Iterable[str] = ()) -> list[str]:
    """URL-ы для попыток доставки по порядку: канонический хост ссылки,
    затем зеркала трекера, затем хост как написан в ссылке."""
    parsed = urlparse(url)
    ordered = [canonical_host(parsed.netloc)]
    ordered += [canonical_host(m) for m in mirrors if m]
    ordered.append(parsed.netloc)  # historical host last: it may come back
    out, seen = [], set()
    for host in ordered:
        if host and host not in seen:
            seen.add(host)
            out.append(parsed._replace(netloc=host).geturl())
    return out
