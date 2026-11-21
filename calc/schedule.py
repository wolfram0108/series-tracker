"""The day of the scheduled work (the settings «Расписание»): the times of day the syncs of all series begin and the times
the watched torrent releases are checked. One rule for the scan module, the torrents module and the windows.

A time is a minute of the day in the local time of the server. The checks of the releases are free times of their own,
or `between`: as many as asked, evenly, in every gap between two syncs; the last gap goes round midnight to the first
sync, and a single sync makes one gap of the whole day."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Iterable

SCHEDULE_KEY = "schedule"
DAY = 24 * 60
MODES = ("between", "free")
# A day of more points than this is no schedule of syncs but a mistake of the hand.
MOST_POINTS = 48
MOST_BETWEEN = 12


def hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def minute_of(text: object) -> int | None:
    """The minute of the day «HH:MM» names; None for anything else."""
    if not isinstance(text, str) or len(text.split(":")) != 2:
        return None
    hours, minutes = text.split(":")
    if not (hours.isdigit() and minutes.isdigit()):
        return None
    hour, minute = int(hours), int(minutes)
    return hour * 60 + minute if 0 <= hour < 24 and 0 <= minute < 60 else None


@dataclass(frozen=True)
class Schedule:
    syncs: tuple[int, ...] = ()
    torrent_mode: str = "between"
    torrent_free: tuple[int, ...] = ()
    torrent_between: int = 1

    def checks(self) -> tuple[int, ...]:
        """The minutes of the day the watched releases are checked."""
        if self.torrent_mode == "free":
            return self.torrent_free
        days = sorted(set(self.syncs))
        found = set()
        for at, start in enumerate(days):
            gap = (days[(at + 1) % len(days)] - start) % DAY or DAY
            for step in range(1, self.torrent_between + 1):
                found.add((start + round(gap * step / (self.torrent_between + 1))) % DAY)
        return tuple(sorted(found - set(days)))

    def as_value(self) -> dict:
        return {"syncs": [hhmm(each) for each in self.syncs], "torrent_mode": self.torrent_mode,
                "torrent_free": [hhmm(each) for each in self.torrent_free], "torrent_between": self.torrent_between}


def checked(data: object) -> Schedule | str:
    """The schedule a value of the window names, or the code of the reason it is refused."""
    if not isinstance(data, dict):
        return "schedule_invalid"
    syncs = [minute_of(each) for each in data.get("syncs") or []]
    free = [minute_of(each) for each in data.get("torrent_free") or []]
    mode = data.get("torrent_mode", "between")
    between = data.get("torrent_between", 1)
    if None in syncs or None in free or mode not in MODES:
        return "schedule_invalid"
    if not isinstance(between, int) or isinstance(between, bool) or not 0 <= between <= MOST_BETWEEN:
        return "schedule_invalid"
    if len(syncs) > MOST_POINTS or len(free) > MOST_POINTS:
        return "schedule_invalid"
    return Schedule(syncs=tuple(sorted(set(syncs))), torrent_mode=mode, torrent_free=tuple(sorted(set(free))),
                    torrent_between=between)


def parse(value: str | None) -> Schedule:
    """The schedule a stored value holds; an empty or broken one is a day of nothing scheduled."""
    try:
        found = checked(json.loads(value)) if value else Schedule()
    except ValueError:
        found = Schedule()
    return found if isinstance(found, Schedule) else Schedule()


def _moments(minutes: Iterable[int], around: datetime, tz: tzinfo | None, days: range) -> list[datetime]:
    local = around.astimezone(tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return [midnight + timedelta(days=day, minutes=minute) for day in days for minute in minutes]


def next_at(minutes: Iterable[int], after: datetime, tz: tzinfo | None = None) -> datetime | None:
    """The first moment of the given minutes of the day strictly after `after`; None when there are none."""
    later = [each for each in _moments(tuple(minutes), after, tz, range(0, 2)) if each > after]
    return min(later) if later else None


def last_at(minutes: Iterable[int], before: datetime, tz: tzinfo | None = None) -> datetime | None:
    """The last moment of the given minutes of the day at or before `before`; None when there are none."""
    earlier = [each for each in _moments(tuple(minutes), before, tz, range(-1, 1)) if each <= before]
    return max(earlier) if earlier else None
