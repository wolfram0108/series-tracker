"""How many downloads go at once, by the way a file comes.

A download that takes its file by many connections at once (the segments of a playlist, the parts of a file, a page that
yt-dlp reads) takes `threads_per_file` of them; all such downloads together take no more than `threads_total` — with 32
and 16, two at once. A download by one connection (a stream ffmpeg reads) takes one place of `single_at_once`. A download a
provider of the socket makes (contracts/provider_socket.md) takes one place of `provider_at_once`: how it takes the file,
by how many connections, is the provider's own business, and this module counts only how many it has at work. The way is
known once the stream of a link is asked (`ytdlp.multithreaded`, or the answer that the provider takes the file), so a
download waits for its place after that.

The four numbers are settings (`download_threads_total`, `download_threads_per_file`, `download_single_at_once`,
`download_provider_at_once`); the module downloads is their one owner: this gives their defaults and refuses a value that is
no whole number from 1 up. The waiting for a place is the module's (modules/downloads/places.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.contracts import DownloadLimits, LimitsSet
from core.reasons import reason

# The ways a file comes: by many connections, by one, by a provider of the socket.
MULTI, SINGLE, PROVIDER = "multi", "single", "provider"

KEYS = {"threads_total": "download_threads_total", "threads_per_file": "download_threads_per_file",
        "single_at_once": "download_single_at_once", "provider_at_once": "download_provider_at_once"}


def whole(value: object) -> int | None:
    """A setting as a whole number from 1 up; None for anything else."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


def room(limits: DownloadLimits, way: str) -> int:
    """How many downloads of the way may go at once."""
    return {MULTI: limits.multi_at_once, SINGLE: limits.single_at_once, PROVIDER: limits.provider_at_once}[way]


def total(limits: DownloadLimits) -> int:
    """Downloads that may go at once whatever their way: how many leave the queue to ask their stream."""
    return limits.multi_at_once + limits.single_at_once + limits.provider_at_once


def limits_of(stored: Mapping[str, object]) -> DownloadLimits:
    """The limits the stored values give, by the name of the limit; one never stored, or one that is no whole number from 1 up, is the default."""
    default = DownloadLimits()
    return DownloadLimits(**{name: whole(stored.get(name)) or getattr(default, name) for name in KEYS})


def after_setting(limits: DownloadLimits, key: str, raw: object) -> DownloadLimits | None:
    """The limits once the setting `key` took the value `raw`; None when it is no limit of this module or no whole number from 1 up."""
    name = next((name for name, setting in KEYS.items() if setting == key), None)
    value = whole(raw)
    if name is None or value is None:
        return None
    return limits.model_copy(update={name: value})


@dataclass(frozen=True)
class Change:
    """What a request to change the limits comes to: the reason it is refused, or the settings to write and the values they hold."""

    refusal: str | None = None
    writes: tuple[tuple[str, str], ...] = ()  # (setting, value) in the order of the limits
    values: tuple[tuple[str, int], ...] = ()  # (limit, value)

    def applied_to(self, limits: DownloadLimits) -> DownloadLimits:
        """The limits with the values of the request put in; the limits it does not name stay as they are."""
        return limits.model_copy(update=dict(self.values))


def change_of(asked: LimitsSet) -> Change:
    """The limits a request names, each checked to be a whole number from 1 up; one that is not refuses the whole request."""
    named = {name: getattr(asked, name) for name in KEYS if name in asked.model_fields_set}
    wrong = next((name for name, value in named.items() if whole(value) is None), None)
    if wrong is not None:
        return Change(refusal=reason("download_limit_invalid", name=wrong, value=str(named[wrong])))
    return Change(writes=tuple((KEYS[name], str(whole(value))) for name, value in named.items()),
                  values=tuple((name, whole(value)) for name, value in named.items()))
