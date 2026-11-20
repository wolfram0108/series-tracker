"""The credentials of the trackers and the pages fetched with them, as the module `trackerauth` takes and answers them.

A password leaves the module in one answer only (`SecretReply`, to the eye of its field in the settings); the credentials
carry whether one is set, never the password. The bus is written down (modules/runlog), so the fields that hold a secret
are the ones `core/secrets.py` masks.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .frozen import Frozen


class ServiceRef(BaseModel):
    """`trackerauth.credentials.get` and `trackerauth.secret.get`: the service (`kinozal`, `rutracker`, `vk`, `qbittorrent`) asked about."""

    service: str


class CredentialsSet(BaseModel):
    """`trackerauth.credentials.set`: the login, the password and the address of a service. An empty password leaves the stored one
    as it is (the window never has it to send back); the login is kept without the spaces around it."""

    service: str
    username: str | None = None
    password: str | None = None
    url: str | None = None


class Credentials(Frozen):
    """What a service is set up with, for the window: its login and address and whether a password is set, never the password."""

    username: str | None = None
    url: str | None = None
    has_password: bool


class CredentialsReply(Frozen):
    """`trackerauth.credentials.get`: the credentials of the service, none when it has no record."""

    credentials: Credentials | None = None


class SecretReply(Frozen):
    """`trackerauth.secret.get`: the password of a service, empty when there is none."""

    secret: str


class CredentialsSaved(Frozen):
    """`trackerauth.credentials.set`: the credentials were written."""

    ok: bool


class CredentialsChanged(Frozen):
    """The event `trackerauth.credentials.changed`: the service whose credentials were written (its sessions are dropped)."""

    service: str


class FetchRequest(BaseModel):
    """`trackerauth.fetch`: a request to a service with its session. `timeout` is the seconds the tracker has to answer."""

    service: str
    url: str
    method: str = "GET"
    params: dict[str, Any] | None = None
    data: Any = None
    headers: dict[str, str] | None = None
    timeout: float = 25


class FetchReply(Frozen):
    """`trackerauth.fetch`: what the service answered. The body is `text` for a page and `content_b64` for a file."""

    status: int
    final_url: str
    content_type: str
    text: str | None = None
    content_b64: str | None = None
