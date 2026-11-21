"""The rules of the login: how long a password is, and the guard against a guess by address (a window of failures and a ban). The time is
given: nothing here reads a clock; the attempts of each address are kept by the gateway."""
from __future__ import annotations

from dataclasses import dataclass

PASSWORD_MIN = 8

# The guard against a guess: the failures of an address in a window, and the ban when they reach the limit.
MAX_FAILS = 5
WINDOW_S = 300.0
BAN_S = 300.0


def password_long_enough(password: str) -> bool:
    return len(password) >= PASSWORD_MIN


@dataclass(frozen=True, slots=True)
class Attempts:
    """The failed logins of an address: when each was (the monotonic clock), and the moment its ban ends."""

    fails: tuple[float, ...] = ()
    banned_until: float = 0.0


def ban_remaining(attempts: Attempts, now: float) -> float:
    """The seconds the address stays banned; none once the ban is over."""
    return max(0.0, attempts.banned_until - now)


def after_failure(attempts: Attempts, now: float) -> Attempts:
    """The attempts of an address after a failed login at `now`: the failures of the window and this one, and the ban when they reach
    the limit (the failures are then forgotten, the ban counts)."""
    fails = (*(at for at in attempts.fails if now - at < WINDOW_S), now)
    if len(fails) >= MAX_FAILS:
        return Attempts(banned_until=now + BAN_S)
    return Attempts(fails=fails, banned_until=attempts.banned_until)
