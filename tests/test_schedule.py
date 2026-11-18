"""The day of the scheduled work (core/schedule.py): times of day in the local time of the server, the next moment after
now, and the checks of the torrent releases free or evenly between the syncs."""
from datetime import datetime, timedelta, timezone

from core.schedule import Schedule, checked, hhmm, last_at, minute_of, next_at, parse

TZ = timezone(timedelta(hours=6))


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


def test_a_time_of_day_is_read_and_written_as_hh_mm_and_anything_else_is_no_time():
    assert [minute_of(each) for each in ("00:00", "10:05", "23:59")] == [0, 605, 1439]
    assert [minute_of(each) for each in ("24:00", "9:60", "ten", "", None, "1:2:3")] == [None] * 6
    assert hhmm(605) == "10:05"


def test_between_the_syncs_the_checks_stand_evenly_and_the_last_gap_goes_round_midnight():
    syncs = tuple(minute_of(each) for each in ("10:00", "16:00", "20:00"))
    assert [hhmm(each) for each in Schedule(syncs=syncs).checks()] == ["03:00", "13:00", "18:00"]
    assert [hhmm(each) for each in Schedule(syncs=syncs, torrent_between=2).checks()] == [
        "00:40", "05:20", "12:00", "14:00", "17:20", "18:40"]
    # One sync makes one gap of the whole day; none makes no check between syncs.
    assert [hhmm(each) for each in Schedule(syncs=(600,)).checks()] == ["22:00"]
    assert Schedule().checks() == () and Schedule(syncs=syncs, torrent_between=0).checks() == ()


def test_free_checks_are_the_times_the_user_put():
    schedule = Schedule(syncs=(600,), torrent_mode="free", torrent_free=(480, 1320))
    assert schedule.checks() == (480, 1320)


def test_the_next_moment_is_the_first_time_of_day_after_now_tomorrow_when_today_is_over():
    times = (600, 960, 1200)
    assert next_at(times, at(18, 12), TZ) == at(18, 16)
    assert next_at(times, at(18, 16), TZ) == at(18, 20)  # strictly after
    assert next_at(times, at(18, 21), TZ) == at(19, 10)
    assert next_at((), at(18, 12), TZ) is None
    assert last_at(times, at(18, 12), TZ) == at(18, 10)
    assert last_at(times, at(18, 9), TZ) == at(17, 20)
    # A moment given in another zone is the same moment.
    assert next_at(times, at(18, 12).astimezone(timezone.utc), TZ) == at(18, 16)


def test_a_schedule_of_the_window_is_checked_and_a_stored_one_read_back():
    good = checked({"syncs": ["20:00", "10:00", "10:00"], "torrent_mode": "free", "torrent_free": ["08:00"],
                    "torrent_between": 1})
    assert good == Schedule(syncs=(600, 1200), torrent_mode="free", torrent_free=(480,), torrent_between=1)
    for bad in ({"syncs": ["25:00"]}, {"torrent_mode": "often"}, {"torrent_between": -1}, {"torrent_between": True},
                {"syncs": [f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 20, 40)]}, "x"):
        assert checked(bad) == "schedule_invalid", bad
    import json
    assert parse(json.dumps(good.as_value())) == good
    assert parse(None) == Schedule() and parse("{broken") == Schedule()
