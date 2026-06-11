"""Конверт сообщения шины.

Единственный формат, в котором модули обмениваются информацией.
Payload — только JSON-сериализуемые данные: никаких объектов, сессий,
коннектов. Это правило делает будущий переезд на внешний брокер
заменой одного файла (core/bus.py), а не переписыванием модулей.
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


def topic_matches(pattern: str, topic: str) -> bool:
    """Сопоставление топика с шаблоном.

    Сегменты разделяются точкой; `*` закрывает ровно один сегмент,
    `#` — весь хвост (только в конце шаблона).
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
