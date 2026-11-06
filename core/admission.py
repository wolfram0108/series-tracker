"""A change of a series admitted by the owner of its runs, held until every request it sent has been answered (И-60).

An admission is not over when its caller stops waiting: a fuse that ran out or an HTTP request the client dropped leaves
the request in the hands of its handler, which may still write. So every request a module sends while an admission is
current carries the admission's number (`Envelope.admission`) and is counted, and the admission ends — its `release` is
called once — when its block is left and every counted request has been answered, in time or late. A handler always
answers (an error is an answer too); one that never does holds the admission, and a sync of the series waits for it.
Only the requests of the task that opened the admission are its own: a task started inside the block (it inherits the
context) is other work — a sync started so would wait for the admission that waits for it. The handler of a request that
carries the number works under it too (`BaseModule._handle_one`): its own requests carry the number, and its answer goes
back once they are answered, so the admission lasts until the last write of the change at any depth. An answer is counted
once, when it comes, whatever its caller is doing then.
"""
from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from typing import Callable


class Admission:
    def __init__(self, token: str, release: Callable[[], None], log: logging.Logger | None = None) -> None:
        self.token = token
        self._owner = asyncio.current_task()
        self._release = release
        self._log = log
        self._late = False
        self._pending: set[str] = set()
        self._closed = False
        self._released = False

    def covers(self) -> bool:
        """Whether a request sent now belongs to the change: it is sent by the task that opened the admission."""
        return asyncio.current_task() is self._owner

    def sent(self, request_id: str) -> None:
        self._pending.add(request_id)

    def answered(self, request_id: str) -> None:
        self._pending.discard(request_id)
        self._settle()

    def close(self) -> None:
        """The block of the change is left; the admission ends once what it sent is answered."""
        self._closed = True
        if self._pending and self._log is not None:
            self._late = True
            self._log.warning("допуск %s: ожидание правки окончено, а ответа ждут ещё %d запросов — допуск держится до "
                              "них", self.token, len(self._pending))
        self._settle()

    def _settle(self) -> None:
        if self._closed and not self._pending and not self._released:
            self._released = True
            if self._late and self._log is not None:
                self._log.info("допуск %s: поздние ответы пришли — допуск снят", self.token)
            self._release()


# The admission of the change the running code belongs to; requests sent under it are counted by it.
current: ContextVar[Admission | None] = ContextVar("admission", default=None)
