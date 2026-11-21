"""BaseModule: the module's wrapper over the bus.

What it gives a module:
- declarative subscriptions (handle("exact.topic")); a module is subscribed to exactly what it handles;
- publish_event / send_command: sending without waiting for an answer;
- request(): query or command -> reply with a correlation_id and a fuse, built over pub/sub; the envelope kind and
  the fuse come from the topic registry (core/topics.py); reply_of() returns the answer as the model of the record;
- an automatic reply to a query and to a command with reply_to: the value returned by the handler travels as a
  reply envelope to the request's reply_to;
- start/stop lifecycle and the on_start hook (reconcile: pick up unfinished tasks from the DB; the rule "the bus
  is a signal, the DB is the truth").

Isolation rule: a module imports only core/*, never another module. All communication between modules goes
through the bus.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable

from . import compute
from .admission import Admission
from .admission import current as current_admission
from .bus import Bus, Subscription, is_exact
from .contracts.availability import NotAllowed
from .envelope import Envelope
from .logging import get_logger
from .reasons import EXTERNAL, describe, parse, reason
from .topics import TOPICS, Topic, deadline_of

Handler = Callable[[Envelope], Awaitable[Any]]

REPLY_PREFIX = "_reply"
ERROR_KEY = "_bus_error"
# The code a request to a topic nobody handles fails with: a caller tells "no such module in this process" by it.
NO_HANDLER = "no_handler"

# The kinds of an answer that refuses rather than fails: a missing entity, a state that does not allow, a refused value.
_REFUSALS = ("LookupError", "NotAllowed", "ValueError")


def is_refusal(exc: BaseException) -> bool:
    """A refusal the module gives as its answer, not a failure of its own: a refusal of the table of what is allowed, a «not found» of
    its own (LookupError itself; its KeyError and IndexError are slips of code), a value refused with a coded reason
    (ValueError itself with a code of core.reasons; the ValueError of a library carries none)."""
    if isinstance(exc, NotAllowed) or type(exc) is LookupError:
        return True
    return type(exc) is ValueError and (parse(str(exc)) or {}).get("code") not in (None, EXTERNAL)

# `request` takes the fuse from the record of the topic (`core.topics`) unless the caller names one (a test, a one-off wrapper).
_FROM_TABLE: Any = object()


def _name_of(topic: Topic | str) -> str:
    return topic.name if isinstance(topic, Topic) else topic


class BusRequestError(RuntimeError):
    """The handler on the other side answered with an error."""


class BaseModule:
    name: str = "module"

    def __init__(self, bus: Bus) -> None:
        self.bus = bus
        self.log = get_logger(self.name)
        self._handlers: list[tuple[str, Handler, bool]] = []
        self._tasks: list[asyncio.Task] = []
        self._bg: set[asyncio.Task] = set()
        self._stopping = False
        self._subs: list[Subscription] = []
        self._pending: dict[str, asyncio.Future] = {}
        # Requests sent under an admission whose answer has not come yet (it is counted however late it comes).
        self._admitted: dict[str, Admission] = {}
        self._reply_topic = f"{REPLY_PREFIX}.{self.name}"
        self.register()

    # --- module interface declaration -------------------------------------

    def register(self) -> None:
        """Overridden by the module: declare self.handle(...) here."""

    def handle(self, pattern: Topic | str, handler: Handler, *,
               concurrent: bool = False) -> None:
        """concurrent=True: each envelope is handled in its own task, so a long handler (a scan, a download) does
        not block the topic's queue for the next requests. By default handling is sequential, which keeps the order
        (important, e.g., for folding statuses)."""
        pattern = _name_of(pattern)
        # A family of topics would bring the module envelopes it does not handle, and an answer to a query of any of them.
        if not is_exact(pattern):
            raise ValueError(f"модуль {self.name}: подписка '{pattern}' — не точный топик")
        self._handlers.append((pattern, handler, concurrent))

    # --- lifecycle ----------------------------------------------------

    async def on_start(self) -> None:
        """Module hook: reconcile unfinished tasks, initialize."""

    async def on_stop(self) -> None:
        """Module hook: orderly shutdown."""

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

    def spawn(self, coro, *, name: str | None = None, lasting: bool = False) -> asyncio.Task:
        """The one way a module starts work of its own beside its handlers: the module stops it with itself. Work started
        once the module is stopping — a cancelled task's way out that moves a queue on — is cancelled before its first
        step, so nothing of it runs and nothing outlives the module. Work ends and is waited for by whoever waits for
        quiet (`background`); a `lasting` task is a loop as long as the module's life (a feed, a worker) and is not."""
        task = asyncio.create_task(coro, name=name)
        if lasting:
            self._tasks.append(task)
        else:
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)
        if self._stopping:
            task.cancel()
        return task

    async def stop(self) -> None:
        self._stopping = True
        await self.on_stop()
        # What a cancelled task spawns on its way out is cancelled already and only has to end: at most one more round.
        while pending := [task for task in (*self._tasks, *self._bg) if not task.done()]:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        self._bg.clear()
        for sub in self._subs:
            self.bus.unsubscribe(sub)
        self._tasks.clear()
        self._subs.clear()
        self.log.info("модуль остановлен")

    # --- sending ----------------------------------------------------------

    def publish_event(self, topic: Topic | str, payload: Any = None) -> None:
        self.bus.publish(Envelope(topic=_name_of(topic), kind="event", payload=payload))

    def send_command(self, topic: Topic | str, payload: Any = None) -> None:
        """A command sent without waiting for an answer."""
        self.bus.publish(Envelope(topic=_name_of(topic), kind="command", payload=payload))

    async def request(self, topic: Topic | str, payload: Any = None, timeout: float | None = _FROM_TABLE, *,
                      units: int = 1) -> Any:
        """The answer of the owner of `topic`. The envelope is of the kind of the topic's record (`core.topics`): a
        command for a command, a query for a query and for a string the registry does not know (a test, a one-off
        wrapper). The fuse is the owner's, times `units` for a topic whose work is one step for each unit; `None` waits
        without one. An event is told, not asked: a request for one is a mistake of the caller."""
        record = topic if isinstance(topic, Topic) else TOPICS.get(topic)
        if record is not None and record.kind == "event":
            raise ValueError(f"модуль {self.name}: запрос к событию '{record.name}' — на события не отвечают")
        kind = "query" if record is None else record.kind
        if timeout is _FROM_TABLE:
            timeout = deadline_of(topic, units)
        admission = current_admission.get()
        if admission is not None and not admission.covers():
            admission = None  # a task started inside the change is other work
        env = Envelope(topic=_name_of(topic), kind=kind, payload=payload,
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
                raise BusRequestError(reason(NO_HANDLER, topic=env.topic))
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(env.correlation_id, None)
            # A fuse or a cancellation of the caller cancels the future, not the work: the admission counts the request
            # when its answer comes (`_consume_replies`); only a request nobody received has no answer to wait for.
            if unanswerable and self._admitted.pop(env.correlation_id, None) is not None:
                admission.answered(env.correlation_id)

    async def reply_of(self, topic: Topic, payload: Any = None, *, units: int = 1) -> Any:
        """The answer of the owner of `topic` as the model of its record (`Topic.reply`). An answer that came as that model is
        taken as it is; a dictionary is checked off the loop (a table or the answers of the rules are thousands of rows). A
        topic with no model gives its answer as it came. `units` is the one of `request`."""
        answer = await self.request(topic, payload, units=units)
        if topic.reply is None or isinstance(answer, topic.reply):
            return answer
        return await compute.run(topic.reply.model_validate, answer)

    def background(self) -> set[asyncio.Task]:
        """Tasks in which the module works outside the bus handlers; whoever waits for quiet waits for them too."""
        return set(self._bg)


    # --- consumers -------------------------------------------------------

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
        # A request of an admitted change runs under that admission; its answer goes back only after its own requests
        # are answered, so the change ends with its last write, however deep.
        asked = env.kind in ("query", "command")
        relay = _Relay(env.admission, self.log) if env.admission and asked else None
        context = current_admission.set(relay.admission) if relay is not None else None
        try:
            try:
                result = handler(env)
                if inspect.isawaitable(result):
                    result = await result
                payload = result
            except asyncio.CancelledError:
                raise
            except BusRequestError as exc:
                # The answer of another module goes on as it came, its kind first: a "not found" relayed is still a
                # "not found" to the caller. Its trace, if any, is in the journal of the module that failed.
                refused = str(exc).startswith(_REFUSALS)
                (self.log.info if refused else self.log.error)("топик '%s': ответ другого модуля передан дальше: %s",
                                                             env.topic, describe(str(exc)))
                payload = {ERROR_KEY: str(exc)}
            except Exception as exc:  # noqa: BLE001 - a module does not die of one handler
                if is_refusal(exc):
                    # The module's answer, worded by its caller: no trace is due.
                    self.log.info("отказ на топике '%s': %s", env.topic, describe(str(exc)))
                else:
                    self.log.exception("ошибка обработчика '%s' на топике '%s'",
                                       getattr(handler, "__name__", "?"), env.topic)
                payload = {ERROR_KEY: f"{type(exc).__name__}: {exc}"}
        finally:
            if context is not None:
                current_admission.reset(context)
        if relay is not None:
            await relay.over()
        if asked and env.reply_to:
            self.bus.publish(Envelope(
                topic=env.reply_to, kind="reply", payload=payload,
                correlation_id=env.correlation_id))

    async def _consume_replies(self, sub: Subscription) -> None:
        while True:
            env: Envelope = await sub.queue.get()
            try:
                # The answer is counted where it comes, once, whatever its caller is doing: waiting, gone by its fuse, or
                # cancelled with its future still registered.
                admitted = self._admitted.pop(env.correlation_id, None)
                if admitted is not None:
                    admitted.answered(env.correlation_id)
                fut = self._pending.get(env.correlation_id)
                if fut is None or fut.done():
                    continue  # a reply that came after the fuse is ignored
                if isinstance(env.payload, dict) and ERROR_KEY in env.payload:
                    fut.set_exception(BusRequestError(env.payload[ERROR_KEY]))
                else:
                    fut.set_result(env.payload)
            finally:
                self.bus.processed(sub)


class _Relay:
    """The admission a request carried, for the handler of that request: its own requests are counted, and it is over once
    the handler is done and every one of them is answered."""

    def __init__(self, token: str, log) -> None:
        self._over = asyncio.Event()
        self.admission = Admission(token, self._over.set, log=log)

    async def over(self) -> None:
        self.admission.close()
        await self._over.wait()
