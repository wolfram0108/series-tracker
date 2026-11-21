"""In-process "dumb" bus: publish and subscribe, nothing else.

The bus must never do the following (spec, section 2.2): route by content,
transform payloads, retry, persist, set priorities, or know about modules.
Request/reply is built in the module wrapper (core/module.py) on top of pub/sub;
the bus knows nothing about it.

Accounting for processing (`settled`) is not routing: it is a signal for whoever waits for silence. A module
subscription (`tracked`) reports each envelope it has finished, so the bus knows how many envelopes are still
in transit or in work. Waiting for silence is thus a signal, not a pause. Untracked subscriptions (tests, SSE
streams) are not counted.

A subscription is one or more patterns sharing one queue: envelopes of all its topics arrive in publication order.
A working subscriber (module, UI stream) subscribes to exactly the topics it parses; patterns with `*` and `#`
are for observers (tests, instruments) that need the whole stream.

Subscriber queues are unbounded: silent message loss is not acceptable, and backpressure
is not a problem in a single-user process. A growing queue is a subscriber bug and must be
visible (see the warning in publish).
"""
from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass, field
from typing import Callable

from .envelope import Envelope, topic_matches

_log = logging.getLogger("bus")

# A backlog this large means the subscriber cannot keep up, not a burst.
_QUEUE_WARN_SIZE = 1000


def is_exact(pattern: str) -> bool:
    """A pattern that names one topic, not a family of them."""
    return "*" not in pattern.split(".") and "#" not in pattern.split(".")


@dataclass(slots=True)
class Subscription:
    """Subscriber's topic patterns and its private envelope queue."""
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
        """Signal of the bus's next step: an envelope was published or taken apart. Take it at the same moment
        the condition is checked, so no step between the check and the wait is lost. While nothing happens,
        the condition does not change."""
        return self._step

    def processed(self, sub: Subscription) -> None:
        """The consumer of a tracked subscription is done with one envelope (parsed it or rejected it)."""
        self._tick()
        if sub.tracked and self._unfinished:
            self._unfinished -= 1
            if not self._unfinished:
                self._quiet.set()

    async def settled(self) -> None:
        """Waits until all envelopes of tracked subscriptions are taken apart, including those handlers produced on the way."""
        while self._unfinished:
            await self._quiet.wait()

    def publish(self, env: Envelope) -> int:
        """Puts the envelope into every matching subscriber's queue. Returns how many received it."""
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
