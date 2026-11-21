"""Encryption of secrets stored in the database (see docs/security.md).

Fernet (AES-128-CBC + HMAC-SHA256). The key comes from ST_ENCRYPTION_KEY
(urlsafe base64, 32 bytes, the Fernet format) or is derived from ST_SECRET_KEY
(sha256 → base64). The key must stay stable: changing it makes previously
encrypted secrets undecryptable.

A secret is stored in the database as a Fernet token (starts with 'gAAAAA').
Reads decrypt it transparently; a plaintext value (legacy, before migration) is
recognised by the missing prefix and returned as is, which allows a smooth
transition and tolerates partially encrypted data.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

_TOKEN_PREFIX = "gAAAAA"  # Fernet version byte 0x80 always encodes to this base64 prefix
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.environ.get("ST_ENCRYPTION_KEY", "").strip()
        if not key:
            secret = (os.environ.get("ST_SECRET_KEY", "").encode()
                      or b"insecure-default-key")
            key = base64.urlsafe_b64encode(
                hashlib.sha256(secret).digest()).decode()
        _fernet = Fernet(key.encode())
    return _fernet


def reset_cache() -> None:
    """Drop the cached key so the next call re-reads the environment (for tests that change it)."""
    global _fernet
    _fernet = None


def is_encrypted(value: str | None) -> bool:
    return bool(value) and value.startswith(_TOKEN_PREFIX)


def encrypt(value: str | None) -> str | None:
    """Encrypt a secret. Empty or None is returned unchanged; an already encrypted
    value is not encrypted again (idempotent, so it is safe for migrations)."""
    if not value or is_encrypted(value):
        return value
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    """Decrypt a secret. Plaintext (legacy) is returned as is."""
    if not is_encrypted(value):
        return value
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return value
