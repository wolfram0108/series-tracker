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
import inspect
from typing import Any, Awaitable, Callable

from .admission import Admission
from .admission import current as current_admission
from .bus import Bus, Subscription
from .contracts.availability import NotAllowed
from .deadlines import deadline_of
from .envelope import Envelope
from .logging import get_logger
from .reasons import describe

Handler = Callable[[Envelope], Awaitable[Any]]

REPLY_PREFIX = "_reply"
ERROR_KEY = "_bus_error"
# The words a request to a topic nobody handles fails with: a caller tells "no such module in this process" by them.
NO_HANDLER = "нет обработчика"

# `request` takes the fuse from the table of `core.deadlines` unless the caller names one (a test, a one-off wrapper).
_FROM_TABLE: Any = object()


class BusRequestError(RuntimeError):
    """Обработчик на той стороне ответил ошибкой."""


class BaseModule:
    name: str = "module"

    def __init__(self, bus: Bus) -> None:
        self.bus = bus
        self.log = get_logger(self.name)
        self._handlers: list[tuple[str, Handler, bool]] = []
        self._tasks: list[asyncio.Task] = []
        self._bg: set[asyncio.Task] = set()
        self._subs: list[Subscription] = []
        self._pending: dict[str, asyncio.Future] = {}
        # Requests sent under an admission whose answer has not come yet (it is counted however late it comes).
        self._admitted: dict[str, Admission] = {}
        self._reply_topic = f"{REPLY_PREFIX}.{self.name}"
        self.register()

    # --- объявление интерфейса модуля -------------------------------------

    def register(self) -> None:
        """Переопределяется модулем: здесь объявляются self.handle(...)."""

    def handle(self, pattern: str, handler: Handler, *,
               concurrent: bool = False) -> None:
        """concurrent=True — каждый конверт обрабатывается отдельной
        задачей: долгий обработчик (скан, загрузка) не блокирует
        очередь топика для следующих запросов. По умолчанию —
        последовательно: порядок обработки сохраняется (важно, например,
        для свёрток статусов)."""
        self._handlers.append((pattern, handler, concurrent))

    # --- жизненный цикл ----------------------------------------------------

    async def on_start(self) -> None:
        """Хук модуля: reconcile незавершённых задач, инициализация."""

    async def on_stop(self) -> None:
        """Хук модуля: корректное завершение."""

    async def start(self) -> None:
        reply_sub = self.bus.subscribe(self._reply_topic, tracked=True)
        self._subs.append(reply_sub)
        self._tasks.append(asyncio.create_task(
            self._consume_replies(reply_sub), name=f"{self.name}:replies"))
        for pattern, handler, concurrent in self._handlers:
            sub = self.bus.subscribe(pattern, tracked=True)
            self._subs.append(sub)
            self._tasks.append(asyncio.create_task(
                self._consume(sub, handler, concurrent),
                name=f"{self.name}:{pattern}"))
        await self.on_start()
        self.log.info("модуль запущен (подписок: %d)", len(self._subs))

    async def stop(self) -> None:
        await self.on_stop()
        for task in (*self._tasks, *self._bg):
            task.cancel()
        await asyncio.gather(*self._tasks, *self._bg,
                             return_exceptions=True)
        self._bg.clear()
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

    async def request(self, topic: str, payload: Any = None, timeout: float | None = _FROM_TABLE, *,
                      units: int = 1) -> Any:
        """The answer of the owner of `topic`. The fuse is the owner's (`core.deadlines`), times `units` for a topic
        whose work is one step for each unit; `None` waits without one."""
        if timeout is _FROM_TABLE:
            timeout = deadline_of(topic, units)
        admission = current_admission.get()
        if admission is not None and not admission.covers():
            admission = None  # a task started inside the change is other work
        env = Envelope(topic=topic, kind="query", payload=payload,
                       reply_to=self._reply_topic, admission=admission.token if admission else None)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[env.correlation_id] = fut
        unanswerable = False
        if admission is not None:
            admission.sent(env.correlation_id)
            self._admitted[env.correlation_id] = admission
        try:
            delivered = self.bus.publish(env)
            if delivered == 0:
                unanswerable = True
                raise BusRequestError(f"{NO_HANDLER} для query '{topic}'")
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(env.correlation_id, None)
            # A fuse or a cancellation of the caller cancels the future, not the work: the admission waits for the answer.
            if admission is not None and (unanswerable or (fut.done() and not fut.cancelled())):
                self._admitted.pop(env.correlation_id, None)
                admission.answered(env.correlation_id)

    def background(self) -> set[asyncio.Task]:
        """Задачи, в которых модуль работает вне обработчиков шины; тот, кто ждёт тишины, ждёт и их."""
        return set(self._bg)

    # --- потребители -------------------------------------------------------

    async def _consume(self, sub: Subscription, handler: Handler,
                       concurrent: bool = False) -> None:
        while True:
            env: Envelope = await sub.queue.get()
            if concurrent:
                task = asyncio.create_task(self._handle_one(env, handler))
                self._bg.add(task)
                task.add_done_callback(self._bg.discard)
                task.add_done_callback(lambda _task, sub=sub: self.bus.processed(sub))
            else:
                try:
                    await self._handle_one(env, handler)
                finally:
                    self.bus.processed(sub)

    async def _handle_one(self, env: Envelope, handler: Handler) -> None:
        try:
            result = handler(env)
            if inspect.isawaitable(result):
                result = await result
            if env.kind == "query" and env.reply_to:
                self.bus.publish(Envelope(
                    topic=env.reply_to, kind="reply", payload=result,
                    correlation_id=env.correlation_id))
        except asyncio.CancelledError:
            raise
        except NotAllowed as exc:
            # A refusal by the table of what is allowed is the module's answer, not its failure: no trace is due.
            self.log.info("отказ на топике '%s': %s", env.topic, describe(str(exc)))
            if env.kind == "query" and env.reply_to:
                self.bus.publish(Envelope(
                    topic=env.reply_to, kind="reply",
                    payload={ERROR_KEY: f"{type(exc).__name__}: {exc}"},
                    correlation_id=env.correlation_id))
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
            try:
                late = self._admitted.pop(env.correlation_id, None)
                if late is not None and env.correlation_id not in self._pending:
                    late.answered(env.correlation_id)  # the caller stopped waiting; the admission did not
                fut = self._pending.get(env.correlation_id)
                if fut is None or fut.done():
                    continue  # a reply that came after the fuse is ignored
                if isinstance(env.payload, dict) and ERROR_KEY in env.payload:
                    fut.set_exception(BusRequestError(env.payload[ERROR_KEY]))
                else:
                    fut.set_result(env.payload)
            finally:
                self.bus.processed(sub)
