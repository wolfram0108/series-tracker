"""The stalls of the event loop: work that holds the one loop serving every module and window.

A thread of its own watches the loop. The loop marks a short heartbeat; a stall is a heartbeat that does not come. While the
loop stands, the thread samples the stack of the loop's thread, and once the loop moves again a stall longer than
`STALL_S` is reported with the chain of frames of the project that held it in most samples (the innermost first). Time is
the subject here: the watch measures how long the loop stood, it synchronises nothing.

A stall shows a class of defect — pure work over thousands of rows done in the loop instead of the pool of computation
(core/compute) — wherever it is, including in code written after this watch.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from collections import Counter
from typing import Callable

BEAT_S = 0.02
STALL_S = 0.1
SAMPLE_S = 0.005
# The watch gets the interpreter every few milliseconds while Python code runs; rarer than this, one call of C code kept it.
STARVED_S = 0.05
CHAIN_DEPTH = 8

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep
_OUTSIDE = (os.sep + ".venv" + os.sep, os.sep + "site-packages" + os.sep)

Report = Callable[[float, tuple[str, ...], int, int, tuple[str, ...]], None]


def project_chain(frame, depth: int = CHAIN_DEPTH) -> tuple[str, ...]:
    """The frames of the project's own code on a stack, the innermost first: `path:line function`."""
    chain = []
    while frame is not None and len(chain) < depth:
        path = frame.f_code.co_filename
        if frame.f_code.co_name == "_run_once" and path.endswith(os.path.join("asyncio", "base_events.py")):
            break  # what lies below is whoever runs the loop, not the work of one turn of it
        if path.startswith(ROOT) and not any(part in path for part in _OUTSIDE) and path != __file__:
            chain.append(f"{path[len(ROOT):]}:{frame.f_lineno} {frame.f_code.co_name}")
        frame = frame.f_back
    return tuple(chain)


class LoopWatch:
    """The watch of one running loop; `report(seconds, chain, samples_of_chain, samples, other)` is told of every stall.
    `other` is the thread that held the interpreter in one call of C code meanwhile (the loop's own chain is then only
    where it waited for it), with its chain; empty when the loop ran its own code."""

    def __init__(self, loop: asyncio.AbstractEventLoop, report: Report) -> None:
        self.loop = loop
        self.report = report
        self._beat_at = time.monotonic()
        self._loop_thread = threading.get_ident()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="loopwatch", daemon=True)
        self._handle: asyncio.TimerHandle | None = None

    def start(self) -> None:
        """Called from the loop's own thread."""
        self._loop_thread = threading.get_ident()
        self._beat()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._handle is not None:
            self._handle.cancel()
        self._thread.join(timeout=1)

    def _beat(self) -> None:
        self._beat_at = time.monotonic()
        self._handle = self.loop.call_later(BEAT_S, self._beat)

    def _watch(self) -> None:
        stalled_beat: float | None = None
        samples: Counter = Counter()
        others: Counter = Counter()
        woke = time.monotonic()
        while not self._stop.wait(SAMPLE_S):
            # This thread was kept from the interpreter between two wakes: whoever holds it now held it then.
            now = time.monotonic()
            starved, woke = now - woke >= STARVED_S, now
            beat = self._beat_at
            if stalled_beat is not None and beat != stalled_beat:
                held = beat - stalled_beat - BEAT_S
                if held >= STALL_S and samples:
                    chain, count = samples.most_common(1)[0]
                    other = others.most_common(1)[0][0] if others else ()
                    self.report(held, chain, count, sum(samples.values()), other)
                stalled_beat, samples, others = None, Counter(), Counter()
            if time.monotonic() - beat - BEAT_S < STALL_S:
                continue
            stalled_beat = beat
            frames = sys._current_frames()
            frame = frames.get(self._loop_thread)
            if frame is not None:
                samples[project_chain(frame)] += 1
            if not starved:
                continue
            names = {thread.ident: thread.name for thread in threading.enumerate()}
            for ident, other in frames.items():
                chain = project_chain(other) if ident not in (self._loop_thread, threading.get_ident()) else ()
                if chain:
                    others[(f"поток {names.get(ident, ident)}",) + chain] += 1


def logged(log) -> Report:
    """The report of a stall as a line of the journal."""
    def report(held: float, chain: tuple[str, ...], count: int, samples: int, other: tuple[str, ...]) -> None:
        log.warning("цикл событий стоял %d мс; держал (%d из %d замеров): %s%s", round(held * 1000), count, samples,
                    " ← ".join(chain) or "код вне проекта",
                    f"; интерпретатор в это время держал {other[0]}: {' ← '.join(other[1:])}" if other else "")
    return report
