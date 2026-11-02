"""«Тупая» внутрипроцессная шина: publish + subscribe, больше ничего.

Чего шина НЕ делает и не должна уметь никогда (см. ТЗ, раздел 2.2):
маршрутизация по содержимому, трансформация payload, retry,
persistence, приоритеты, знание о модулях. Request/reply реализован
в обвязке модуля (core/module.py) ПОВЕРХ pub/sub — шина о нём не знает.

Учёт обработки (`settled`) — не маршрутизация, а сигнал для того, кто ждёт тишины: подписка модуля (`tracked`) сообщает
о каждом обработанном конверте, и шина знает, сколько конвертов ещё в пути или в работе. Так ожидание тишины — это
сигнал, а не пауза; подписка без учёта (тест, поток SSE) в него не входит.

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
    tracked: bool = False  # потребитель сообщает `Bus.processed` о каждом конверте


class Bus:
    def __init__(self) -> None:
        self._subs: dict[int, Subscription] = {}
        self._ids = itertools.count(1)
        self._unfinished = 0  # конверты учитываемых подписок: в очереди или в обработке
        self._quiet = asyncio.Event()
        self._quiet.set()
        self._step = asyncio.Event()  # поднимается с каждым опубликованным и с каждым обработанным конвертом

    def subscribe(self, pattern: str, *, tracked: bool = False) -> Subscription:
        sub = Subscription(sub_id=next(self._ids), pattern=pattern, tracked=tracked)
        self._subs[sub.sub_id] = sub
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        if self._subs.pop(sub.sub_id, None) is not None and sub.tracked:
            for _ in range(sub.queue.qsize()):  # конверты, которые уже никто не разберёт
                self.processed(sub)

    def _tick(self) -> None:
        self._step.set()
        self._step = asyncio.Event()

    def next_step(self) -> asyncio.Event:
        """Сигнал следующего шага шины: конверт опубликован или разобран. Берётся в тот же миг, когда проверено условие,
        и тогда ни один шаг между проверкой и ожиданием не теряется; пока в системе ничего не происходит, условие
        не изменится."""
        return self._step

    def processed(self, sub: Subscription) -> None:
        """Потребитель учитываемой подписки закончил с одним конвертом (разобрал или отказался)."""
        self._tick()
        if sub.tracked and self._unfinished:
            self._unfinished -= 1
            if not self._unfinished:
                self._quiet.set()

    async def settled(self) -> None:
        """Ждёт, пока все конверты учитываемых подписок разобраны, включая те, что обработчики породили по дороге."""
        while self._unfinished:
            await self._quiet.wait()

    def publish(self, env: Envelope) -> int:
        """Кладёт конверт всем подходящим подписчикам. Возвращает их число."""
        self._tick()
        delivered = 0
        for sub in self._subs.values():
            if topic_matches(sub.pattern, env.topic):
                sub.queue.put_nowait(env)
                delivered += 1
                if sub.tracked:
                    self._unfinished += 1
                    self._quiet.clear()
                if sub.queue.qsize() > _QUEUE_WARN_SIZE:
                    _log.warning(
                        "Очередь подписки '%s' разрослась до %d — "
                        "подписчик не успевает разбирать сообщения",
                        sub.pattern, sub.queue.qsize(),
                    )
        return delivered
