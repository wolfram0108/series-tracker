"""«Тупая» внутрипроцессная шина: publish + subscribe, больше ничего.

Чего шина НЕ делает и не должна уметь никогда (см. ТЗ, раздел 2.2):
маршрутизация по содержимому, трансформация payload, retry,
persistence, приоритеты, знание о модулях. Request/reply реализован
в обвязке модуля (core/module.py) ПОВЕРХ pub/sub — шина о нём не знает.

Очереди подписчиков неограниченные: потеря сообщения недопустима как
тихий режим работы, а backpressure в однопользовательском процессе —
несуществующая проблема. Если очередь растёт — это баг подписчика,
и он должен быть виден (см. предупреждение в publish).
"""
from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass, field

from .envelope import Envelope, topic_matches

_log = logging.getLogger("bus")

# Порог, после которого рост очереди подписчика считается аномалией.
_QUEUE_WARN_SIZE = 1000


@dataclass(slots=True)
class Subscription:
    """Подписка: шаблон топика + личная очередь конвертов."""
    sub_id: int
    pattern: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)


class Bus:
    def __init__(self) -> None:
        self._subs: dict[int, Subscription] = {}
        self._ids = itertools.count(1)

    def subscribe(self, pattern: str) -> Subscription:
        sub = Subscription(sub_id=next(self._ids), pattern=pattern)
        self._subs[sub.sub_id] = sub
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subs.pop(sub.sub_id, None)

    def publish(self, env: Envelope) -> int:
        """Кладёт конверт всем подходящим подписчикам. Возвращает их число."""
        delivered = 0
        for sub in self._subs.values():
            if topic_matches(sub.pattern, env.topic):
                sub.queue.put_nowait(env)
                delivered += 1
                if sub.queue.qsize() > _QUEUE_WARN_SIZE:
                    _log.warning(
                        "Очередь подписки '%s' разрослась до %d — "
                        "подписчик не успевает разбирать сообщения",
                        sub.pattern, sub.queue.qsize(),
                    )
        return delivered
