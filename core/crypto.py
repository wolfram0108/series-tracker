"""Шифрование секретов в БД (Этап 3Б, docs/security.md).

Fernet (AES-128-CBC + HMAC-SHA256). Ключ берётся из ST_ENCRYPTION_KEY
(base64 urlsafe, 32 байта — формат Fernet) либо выводится из ST_SECRET_KEY
(sha256 → base64). Ключ обязан быть стабильным: при его смене ранее
зашифрованные секреты не расшифруются.

Хранение: секрет в БД лежит как Fernet-токен (начинается с 'gAAAAA').
Чтение прозрачно расшифровывает; значение в открытом виде (legacy, до
миграции) распознаётся по отсутствию префикса и возвращается как есть —
это обеспечивает плавный переход и устойчивость к недозашифрованным данным.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

_TOKEN_PREFIX = "gAAAAA"  # маркер Fernet-токена (версия 0x80 в base64)
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
    """Сбросить кэш ключа (для тестов, меняющих окружение)."""
    global _fernet
    _fernet = None


def is_encrypted(value: str | None) -> bool:
    return bool(value) and value.startswith(_TOKEN_PREFIX)


def encrypt(value: str | None) -> str | None:
    """Зашифровать секрет. Пустое/None — без изменений. Уже зашифрованное
    повторно не шифруется (идемпотентно — безопасно для миграции)."""
    if not value or is_encrypted(value):
        return value
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    """Расшифровать секрет. Открытый (legacy) текст возвращается как есть."""
    if not is_encrypted(value):
        return value
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return value
