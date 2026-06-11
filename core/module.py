"""BaseModule — обвязка модуля над шиной.

Даёт модулю:
- декларативные подписки (handle("topic.pattern")) с потребителями;
- publish_event / send_command — отправка без ожидания;
- request() — query→reply с correlation_id и таймаутом (поверх pub/sub);
- автоматический ответ на query: значение, возвращённое обработчиком,
  уезжает reply-конвертом в reply_to запроса;
- жизненный цикл start/stop и хук on_start (reconcile: добор
  незавершённых задач из БД — правило «шина — сигнал, БД — истина»).

Правило изоляции: модуль импортирует только core/*, никогда — другой
модуль. Всё межмодульное общение — через шину.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from .bus import Bus, Subscription
from .envelope import Envelope
from .logging import get_logger

Handler = Callable[[Envelope], Awaitable[Any]]

REPLY_PREFIX = "_reply"
ERROR_KEY = "_bus_error"


class BusRequestError(RuntimeError):
    """Обработчик на той стороне ответил ошибкой."""


class BaseModule:
    name: str = "module"

    def __init__(self, bus: Bus) -> None:
        self.bus = bus
        self.log = get_logger(self.name)
        self._handlers: list[tuple[str, Handler]] = []
        self._tasks: list[asyncio.Task] = []
        self._subs: list[Subscription] = []
        self._pending: dict[str, asyncio.Future] = {}
        self._reply_topic = f"{REPLY_PREFIX}.{self.name}"
        self.register()

    # --- объявление интерфейса модуля -------------------------------------

    def register(self) -> None:
        """Переопределяется модулем: здесь объявляются self.handle(...)."""

    def handle(self, pattern: str, handler: Handler) -> None:
        self._handlers.append((pattern, handler))

    # --- жизненный цикл ----------------------------------------------------

    async def on_start(self) -> None:
        """Хук модуля: reconcile незавершённых задач, инициализация."""

    async def on_stop(self) -> None:
        """Хук модуля: корректное завершение."""

    async def start(self) -> None:
        reply_sub = self.bus.subscribe(self._reply_topic)
        self._subs.append(reply_sub)
        self._tasks.append(asyncio.create_task(
            self._consume_replies(reply_sub), name=f"{self.name}:replies"))
        for pattern, handler in self._handlers:
            sub = self.bus.subscribe(pattern)
            self._subs.append(sub)
            self._tasks.append(asyncio.create_task(
                self._consume(sub, handler), name=f"{self.name}:{pattern}"))
        await self.on_start()
        self.log.info("модуль запущен (подписок: %d)", len(self._subs))

    async def stop(self) -> None:
        await self.on_stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for sub in self._subs:
            self.bus.unsubscribe(sub)
        self._tasks.clear()
        self._subs.clear()
        self.log.info("модуль остановлен")

    # --- отправка ----------------------------------------------------------

    def publish_event(self, topic: str, payload: Any = None) -> None:
        self.bus.publish(Envelope(topic=topic, kind="event", payload=payload))

    def send_command(self, topic: str, payload: Any = None) -> None:
        self.bus.publish(Envelope(topic=topic, kind="command", payload=payload))

    async def request(self, topic: str, payload: Any = None,
                      timeout: float = 30.0) -> Any:
        env = Envelope(topic=topic, kind="query", payload=payload,
                       reply_to=self._reply_topic)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[env.correlation_id] = fut
        try:
            delivered = self.bus.publish(env)
            if delivered == 0:
                raise BusRequestError(f"нет обработчика для query '{topic}'")
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(env.correlation_id, None)

    # --- потребители -------------------------------------------------------

    async def _consume(self, sub: Subscription, handler: Handler) -> None:
        while True:
            env: Envelope = await sub.queue.get()
            try:
                result = await handler(env)
                if env.kind == "query" and env.reply_to:
                    self.bus.publish(Envelope(
                        topic=env.reply_to, kind="reply", payload=result,
                        correlation_id=env.correlation_id))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — модуль не должен умирать
                self.log.exception("ошибка обработчика '%s' на топике '%s'",
                                   getattr(handler, "__name__", "?"), env.topic)
                if env.kind == "query" and env.reply_to:
                    self.bus.publish(Envelope(
                        topic=env.reply_to, kind="reply",
                        payload={ERROR_KEY: f"{type(exc).__name__}: {exc}"},
                        correlation_id=env.correlation_id))

    async def _consume_replies(self, sub: Subscription) -> None:
        while True:
            env: Envelope = await sub.queue.get()
            fut = self._pending.get(env.correlation_id)
            if fut is None or fut.done():
                continue  # запоздавший ответ после таймаута — игнорируем
            if isinstance(env.payload, dict) and ERROR_KEY in env.payload:
                fut.set_exception(BusRequestError(env.payload[ERROR_KEY]))
            else:
                fut.set_result(env.payload)
