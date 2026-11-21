"""The schedule of the syncs and the state of the scheduler: when the next pass is due, what the window is told of it, and what a request to
change the schedule comes to. The time is given: nothing here reads a clock."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from calc.schedule import Schedule, checked, hhmm, next_at, parse
from core.contracts import ScanStatus, ScheduleSaved, ScheduleTimes
from core.reasons import reason


def times_of(schedule: Schedule) -> ScheduleTimes:
    """The schedule as the window shows it: «HH:MM» for each time."""
    return ScheduleTimes(**schedule.as_value())


def moment_of(stored: str) -> datetime:
    """A moment the settings keep as ISO text; one with no zone is UTC."""
    moment = datetime.fromisoformat(stored)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def delay_until(stored: str | None, now: datetime) -> float | None:
    """Seconds to the next pass the settings hold (never negative); None when none is held."""
    if not stored:
        return None
    return max(0.0, (moment_of(stored) - now).total_seconds())


def is_due(stored: str | None, now: datetime) -> bool:
    """Whether the next pass the settings hold is at `now` or before it."""
    return bool(stored) and now >= moment_of(stored)


def next_pass(schedule_stored: str | None, now: datetime) -> str:
    """The next sync is the first time of day of the schedule after `now`, as the ISO text the settings keep it in; a schedule of none leaves
    no next (an empty text)."""
    moment = next_at(parse(schedule_stored).syncs, now)
    return moment.astimezone(timezone.utc).isoformat() if moment is not None else ""


def status_of(schedule_stored: str | None, enabled: bool, scanning: bool, next_scan: str | None, now: datetime) -> ScanStatus:
    """The state of the scheduler (GET /api/scanner/status): the switch, the schedule, the next sync and the next check of the torrent
    releases, and the times those checks are made at (free, or between the syncs)."""
    schedule = parse(schedule_stored)
    next_check = next_at(schedule.checks(), now)
    return ScanStatus(
        scanner_enabled=enabled, is_scanning=scanning, next_scan_time=next_scan or None,
        next_check_time=next_check.astimezone(timezone.utc).isoformat() if next_check is not None else None,
        schedule=times_of(schedule), checks=tuple(hhmm(each) for each in schedule.checks()))


def schedule_set(asked: object) -> tuple[ScheduleSaved, str | None]:
    """The answer to a request to keep a schedule, and the text to write into the settings (none when it is refused): the schedule is
    checked as a whole, and a time or a mode that is not one refuses all."""
    found = checked(asked)
    if isinstance(found, str):
        return ScheduleSaved(ok=False, reason=reason(found)), None
    return ScheduleSaved(ok=True, schedule=times_of(found)), json.dumps(found.as_value())
