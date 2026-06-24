"""Тесты шифрования секретов (Этап 3Б, core/crypto)."""
import pytest

from core import crypto


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("ST_SECRET_KEY", "test-secret-for-crypto")
    monkeypatch.delenv("ST_ENCRYPTION_KEY", raising=False)
    crypto.reset_cache()
    yield
    crypto.reset_cache()


def test_roundtrip():
    enc = crypto.encrypt("hunter2")
    assert enc != "hunter2"
    assert crypto.is_encrypted(enc)
    assert crypto.decrypt(enc) == "hunter2"


def test_idempotent_encrypt():
    enc = crypto.encrypt("x")
    assert crypto.encrypt(enc) == enc  # повторно не шифруется (миграция)


def test_empty_and_none_unchanged():
    assert crypto.encrypt("") == ""
    assert crypto.encrypt(None) is None
    assert crypto.decrypt(None) is None
    assert crypto.decrypt("") == ""


def test_legacy_plain_passthrough():
    # открытый (не Fernet) текст читается как есть — плавная миграция
    assert not crypto.is_encrypted("plain-legacy")
    assert crypto.decrypt("plain-legacy") == "plain-legacy"


def test_dedicated_key(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("ST_ENCRYPTION_KEY", Fernet.generate_key().decode())
    crypto.reset_cache()
    enc = crypto.encrypt("secret")
    assert crypto.is_encrypted(enc)
    assert crypto.decrypt(enc) == "secret"
