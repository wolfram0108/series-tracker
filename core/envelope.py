"""Envelope of a bus message.

The only format in which modules exchange information.
Payloads are JSON-serializable data only: no objects, sessions or connections.
This rule keeps a future move to an external broker a change of one file
(core/bus.py), not a rewrite of the modules.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Kind = Literal["event", "command", "query", "reply"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class Envelope:
    topic: str
    kind: Kind
    payload: Any = None
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    reply_to: str | None = None
    ts: str = field(default_factory=_now_iso)
    # The number of the admitted change a request belongs to (core/admission.py): its handler may tell it from a new one.
    admission: str | None = None


def topic_matches(pattern: str, topic: str) -> bool:
    """Checks a topic against a pattern.

    Segments are dot-separated; `*` matches exactly one segment,
    `#` matches the whole tail (only at the end of the pattern).
    """
    if pattern == "#":
        return True
    p_parts = pattern.split(".")
    t_parts = topic.split(".")
    for i, p in enumerate(p_parts):
        if p == "#":
            return i == len(p_parts) - 1
        if i >= len(t_parts):
            return False
        if p != "*" and p != t_parts[i]:
            return False
    return len(p_parts) == len(t_parts)
