"""The binding of a stored session to the account it was made under.

A tracker session that outlives a change of the login or the password would go on asking under the old account (its limits,
its rights) with no sign of it: the session is alive, so no login is made. The session therefore carries a fingerprint of
the credentials it was got with, and these rules say what of a stored session may be used under the credentials of now.
The clearance of an anti-bot challenge is about the client, not the account, so it survives such a change.
"""
from __future__ import annotations

import hashlib
from typing import Collection, Mapping


def account_fingerprint(credentials: Mapping[str, str | None] | None) -> str | None:
    """The fingerprint of the credentials a login was made under; None when there are none.

    The password enters as a hash of the pair, never as text. A change of the login or of the password makes another
    fingerprint, so the earlier login stops being applicable and the next request logs in again."""
    if not credentials:
        return None
    raw = f"{credentials.get('username') or ''}\0{credentials.get('password') or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def applicable_cookies(cookies: Mapping[str, str], stored: str | None, fingerprint: str | None,
                       client: Collection[str]) -> tuple[dict[str, str], bool]:
    """The cookies of a stored session that may be used under `fingerprint`, and whether it was made under another account.

    `stored` None means it is unknown which account the session was made under (a record older than the binding): that is
    not "another account", and the cookies are used, since dropping them would lose a working session where no automatic
    login is possible (the login form of RuTracker holds a captcha). Only a plain mismatch drops what is of the account
    and leaves what is of the client (`client`: the names of such cookies)."""
    foreign = stored is not None and stored != fingerprint
    if foreign:
        return client_cookies(cookies, client), True
    return dict(cookies), False


def client_cookies(cookies: Mapping[str, str], client: Collection[str]) -> dict[str, str]:
    """What of the cookies is about the client and not the account."""
    return {name: value for name, value in cookies.items() if name in client}
