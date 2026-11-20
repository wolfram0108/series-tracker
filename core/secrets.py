"""What is a secret on the bus, and how a payload is told without its secrets.

The bus is written down: the dump of every sync run (modules/runlog) keeps every envelope that went round it. A secret is therefore
told there as a mask, never as its value. The names are the ones the modules give their secrets: the settings that are encrypted at rest
(`SECRET_SETTING_KEYS`, which travel as a payload `{key, value}`, or as an entry of a map of settings `{values: {key: value}}`), the fields
named `password` and `secret`, and the fields that are a secret in the payload of one topic only (`TOPIC_SECRET_FIELDS`: the `token` of
a provider is one, the `token` of an edit of a series is no secret).
"""
from __future__ import annotations

from .topics import SOURCES_PROVIDER_SET

SECRET_SETTING_KEYS = frozenset({"tmdb_token", "provider.token"})
SECRET_FIELDS = frozenset({"password", "secret"})
TOPIC_SECRET_FIELDS = {SOURCES_PROVIDER_SET.name: frozenset({"token"})}
MASK = "***"


def redacted(payload, topic: str | None = None):
    """A copy of the payload with every secret in it masked. For what is written down, never for what is sent. `topic` is the topic
    the payload goes to, for the fields that are a secret there only."""
    if isinstance(payload, dict):
        key = payload.get("key")
        # A setting is named by a string; a field `key` of another message may hold anything (a list of keys).
        hides = isinstance(key, str) and key in SECRET_SETTING_KEYS
        fields = SECRET_FIELDS | TOPIC_SECRET_FIELDS.get(topic, frozenset())
        return {name: (MASK if value and (name in fields or name in SECRET_SETTING_KEYS or (hides and name == "value"))
                       else redacted(value, topic))
                for name, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [redacted(each, topic) for each in payload]
    return payload
