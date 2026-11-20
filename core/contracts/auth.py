"""The account of the administrator of the web interface as the module `auth` answers: whether a pair is right, whether
there is an account, whether it was saved. The hash of the password never leaves the module."""
from __future__ import annotations

from .frozen import Frozen


class LoginReply(Frozen):
    """`auth.login`: whether the login and the password are the administrator's."""

    ok: bool


class AdminState(Frozen):
    """`auth.exists`: whether an administrator is set up and his name (for the screen of the login), empty when there is none."""

    exists: bool
    username: str = ""


class PasswordSaved(Frozen):
    """`auth.password.set`: whether the account was made or changed (not, when the name or the password is empty)."""

    ok: bool
