"""Delivery hosts: canonicalization and the order of candidates.

A release link is an IDENTITY (part of torrent_id, Р-10): it must not change,
or it gives a false "everything new" and a re-download (finding 13). So the
host in the link is only the first delivery candidate, not an address: the
live host is chosen at request time from the tracker's mirrors.

Knowledge of the delivery prefixes lives here in one copy: both the transport
(delivery) and the tracker's URL resolution (repository) ask it.
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
    """URLs to try for delivery, in order: the link's canonical host,
    then the tracker's mirrors, then the host as written in the link."""
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
