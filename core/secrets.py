"""What is a secret on the bus, and how a payload is told without its secrets.

The bus is written down: the dump of every sync run (modules/runlog) keeps every envelope that went round it. A secret is therefore
told there as a mask, never as its value. The names are the ones the modules give their secrets: the settings that are encrypted at rest
(`SECRET_SETTING_KEYS`, whose value goes in a payload `{key, value}`) and the fields named `password` and `secret`.
"""
from __future__ import annotations

SECRET_SETTING_KEYS = frozenset({"tmdb_token", "provider.token"})
SECRET_FIELDS = frozenset({"password", "secret"})
MASK = "***"


def redacted(payload):
    """A copy of the payload with every secret in it masked. For what is written down, never for what is sent."""
    if isinstance(payload, dict):
        key = payload.get("key")
        # A setting is named by a string; a field `key` of another message may hold anything (a list of keys).
        hides = isinstance(key, str) and key in SECRET_SETTING_KEYS
        return {name: (MASK if value and (name in SECRET_FIELDS or (hides and name == "value")) else redacted(value))
                for name, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [redacted(each) for each in payload]
    return payload
