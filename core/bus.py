"""«Тупая» внутрипроцессная шина: publish + subscribe, больше ничего.

Чего шина НЕ делает и не должна уметь никогда (см. ТЗ, раздел 2.2):
маршрутизация по содержимому, трансформация payload, retry,
persistence, приоритеты, знание о модулях. Request/reply реализован
в обвязке модуля (core/module.py) ПОВЕРХ pub/sub — шина о нём не знает.

Учёт обработки (`settled`) — не маршрутизация, а сигнал для того, кто ждёт тишины: подписка модуля (`tracked`) сообщает
о каждом обработанном конверте, и шина знает, сколько конвертов ещё в пути или в работе. Так ожидание тишины — это
сигнал, а не пауза; подписка без учёта (тест, поток SSE) в него не входит.

Подписка — один или несколько шаблонов с одной очередью: конверты всех её топиков приходят в порядке публикации.
Рабочий подписчик (модуль, поток интерфейса) подписан ровно на те точные топики, которые разбирает; шаблоны с `*` и `#`
— для наблюдателей (тесты, приборы), которым нужен весь поток.

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
from typing import Callable

from .envelope import Envelope, topic_matches

_log = logging.getLogger("bus")

# Порог, после которого рост очереди подписчика считается аномалией.
_QUEUE_WARN_SIZE = 1000


def is_exact(pattern: str) -> bool:
    """A pattern that names one topic, not a family of them."""
    return "*" not in pattern.split(".") and "#" not in pattern.split(".")


@dataclass(slots=True)
class Subscription:
    """Подписка: шаблоны топиков + личная очередь конвертов."""
    sub_id: int
    patterns: tuple[str, ...]
    topics: frozenset[str] = frozenset()  # the exact ones: a topic is found among them without matching
    wild: tuple[str, ...] = ()  # the patterns of an observer
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    tracked: bool = False  # the consumer reports each envelope it is done with (`Bus.processed`)
    warned: bool = False  # the growth of its queue has been told and has not gone down since


class Bus:
    def __init__(self) -> None:
        self._subs: dict[int, Subscription] = {}
        self._ids = itertools.count(1)
        self._unfinished = 0  # envelopes of tracked subscriptions that are queued or being handled
        self._quiet = asyncio.Event()
        self._quiet.set()
        self._step = asyncio.Event()  # set with every envelope published and every envelope taken apart
        # Observers of every envelope published (the dump of a run): they only look, take nothing apart, change nothing.
        self._taps: list[Callable[[Envelope], None]] = []

    def tap(self, observer: Callable[[Envelope], None]) -> Callable[[], None]:
        """`observer` is told every envelope published, at once and in order; it must not block. Returns the way to stop."""
        self._taps.append(observer)
        return lambda: self._taps.remove(observer) if observer in self._taps else None

    def subscribe(self, *patterns: str, tracked: bool = False) -> Subscription:
        if not patterns:
            raise ValueError("подписка без топиков")
        sub = Subscription(sub_id=next(self._ids), patterns=patterns, tracked=tracked,
                           topics=frozenset(each for each in patterns if is_exact(each)),
                           wild=tuple(each for each in patterns if not is_exact(each)))
        self._subs[sub.sub_id] = sub
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        if self._subs.pop(sub.sub_id, None) is not None and sub.tracked:
            for _ in range(sub.queue.qsize()):  # envelopes that nobody will take apart any more
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
        for observer in tuple(self._taps):
            try:
                observer(env)
            except Exception:  # noqa: BLE001 — an observer never stops a message
                _log.exception("наблюдатель шины упал на конверте %s", env.topic)
        delivered = 0
        for sub in self._subs.values():
            if env.topic in sub.topics or any(topic_matches(each, env.topic) for each in sub.wild):
                sub.queue.put_nowait(env)
                delivered += 1
                if sub.tracked:
                    self._unfinished += 1
                    self._quiet.clear()
                # Told once as the queue crosses the size, and again only after it has gone down: one burst, one line.
                if sub.queue.qsize() > _QUEUE_WARN_SIZE and not sub.warned:
                    sub.warned = True
                    _log.warning(
                        "Очередь подписки '%s' разрослась до %d — "
                        "подписчик не успевает разбирать сообщения",
                        ", ".join(sub.patterns), sub.queue.qsize(),
                    )
                elif sub.warned and sub.queue.qsize() <= _QUEUE_WARN_SIZE // 2:
                    sub.warned = False
        return delivered
