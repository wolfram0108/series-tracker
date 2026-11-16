"""Guard: no pause or polling as a way to wait for another module (ARCHITECTURE.md of stage 10, section 7).

In modules/ and core/: a call of `asyncio.sleep`, `time.sleep` or `wait_for` outside ALLOWED_WAITS is a violation;
`sleep(0)` is a yield to the loop, not a pause. Every permit names its category (SUBJECT, RETRY, BACKOFF, POLL, SAFEGUARD,
PROCESS, PROTOCOL below). In web/src: `setInterval`, `setTimeout` and the VueUse polling helpers outside
ALLOWED_TIMERS are violations (the interface has no timers, law law-ui). The permitted places are listed below with
the ground of each; everything else is a violation, and permitted places are printed on every run.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from guard_common import (Outcome, Violation, blank_comments, callee_name, dotted_name, parse_python, python_files,
                          qualname_of, rel, run_guard, web_comments)

NAME = "no_wait_no_poll"
SECTION_7 = "ARCHITECTURE.md (stage 10), section 7"

# Categories of a permitted wait. A wait that fits none of them is a violation.
SUBJECT = "предмет задачи"  # the time itself is what the task is about: a schedule, the pace of requests to a host
RETRY = "политика повторов"  # the pause between attempts, counted by core.retry
BACKOFF = "пауза/бэкофф к внешней системе"  # the other side is not ours; the growing pause is the safeguard
POLL = "опрос внешней системы без push"  # the other side offers no notification, so it is asked on a timer
SAFEGUARD = "предохранитель на ожидание сигнала"  # a signal ends the wait; the time only stops waiting for ever
PROCESS = "ожидание процесса"  # a child process is awaited with a safeguard
PROTOCOL = "интервал протокола"  # the interval is a part of a wire protocol
DETECTION = "обнаружение отказа"  # the time tells a wait that is a failure (law-arch); the wait itself goes on

CATEGORIES = frozenset({SUBJECT, RETRY, BACKOFF, POLL, SAFEGUARD, PROCESS, PROTOCOL, DETECTION})

# (file, function qualname, category, ground). '*' as the function allows the whole file. Searched in modules/ and core/.
ALLOWED_WAITS: list[tuple[str, str, str, str]] = [
    ("modules/scan/module.py", "ScanModule._edits_finished", DETECTION,
     "a sync waits for the changes admitted before it; past EDIT_STUCK_S the wait is told in the log, and goes on"),
    ("modules/scan/module.py", "ScanModule._scheduler_loop", SUBJECT,
     f"schedule of the auto scan ({SECTION_7}, 'Расписание автоскана')"),
    ("modules/sources/module.py", "SourcesModule._polite", SUBJECT,
     f"pace of requests to a host, 0.6 s (R-33) ({SECTION_7})"),
    ("modules/sources/policy.py", "*", RETRY,
     f"the retry policy and the pace of requests to sites and players ({SECTION_7}); it sleeps through core.retry"),
    ("modules/scan/sync.py", "TableSignals.verdict", SAFEGUARD,
     f"wait for the signal of a table, safeguard of twice the measured read ({SECTION_7}, last row)"),
    ("modules/catalog/module.py", "CatalogModule._await_purge_reports", SAFEGUARD,
     "the event of the last owner's report ends the wait; silence longer than PURGE_REPORT_TIMEOUT is a broken cascade"),
    ("modules/downloads/ytdlp.py", "_next_line", PROCESS,
     f"wait for a line of the yt-dlp process; silence longer than STALL_TIMEOUT_S is the safeguard ({SECTION_7})"),
    ("modules/sources/players/vibix_protocol.py", "capture_with_browser", SAFEGUARD,
     f"response signal of the browser page with a safeguard ({SECTION_7}, 'Таймауты запросов к плеерам')"),
    ("modules/sources/players/vibix_protocol.py", "_manifest", SAFEGUARD,
     f"manifest response signal with a safeguard ({SECTION_7}, 'Таймауты запросов к плеерам')"),
    ("modules/conform/module.py", "ConformModule._run", BACKOFF,
     "reconnect to the alignment daemon, an external system, with a growing pause up to BACKOFF_MAX_S"),
    ("modules/gateway/module.py", "GatewayModule._sse_batches", PROTOCOL,
     "the keepalive interval of the SSE stream: a comment line keeps the connection alive through proxies"),
    ("modules/torrents/module.py", "TorrentsModule._pipeline_loop", POLL,
     "qBittorrent, an external system, has no push: the state of the torrents is asked on a timer"),
    ("modules/torrents/module.py", "TorrentsModule._monitor_loop", POLL,
     "qBittorrent, an external system, has no push: the progress is asked on a timer"),
    ("modules/torrents/module.py", "TorrentsModule._release_loop", POLL,
     "qBittorrent has no push: while a release of a torrent source moves its state is asked on a timer, sooner on a word"),
    ("modules/torrents/module.py", "TorrentsModule._watch_loop", SUBJECT,
     "the time is the subject: a watched tracker page is read again once a term of the sources"),
    ("core/module.py", "BaseModule.request", SAFEGUARD,
     "the reply of a query ends the wait; the timeout is the safeguard of the caller"),
    ("core/retry.py", "retrying", RETRY, "the retry policy itself: the pause between attempts"),
    ("core/proc.py", "run_capture", PROCESS, "wait for a finished program with a deadline"),
    ("core/proc.py", "_reap", PROCESS, "wait for a killed program to be reaped, with a safeguard"),
    ("core/storage.py", "call", SAFEGUARD,
     "the end of a storage call ends the wait; the timeout stops waiting for a stuck call"),
    ("core/clock.py", "beat_while", SUBJECT,
     "a display of minutes and seconds changes once a second: the beat is what is shown"),
    ("core/clock.py", "every", PROTOCOL, "the period an external protocol prescribes, given by the caller"),
    ("core/proc.py", "_ended", SAFEGUARD,
     "the report of the loop on a reaped child ends the wait; REAP_TIMEOUT_S stops waiting for a report never made"),
    ("core/proc.py", "run_lines", PROCESS, "wait for a program read line by line, with its deadline"),
    ("modules/downloads/hls_native.py", "fetch", DETECTION,
     "a line of the segment reader ends the wait; silence longer than SILENCE_S is the failure of the download"),
    ("modules/downloads/ytdlp.py", "_stderr_tail", SAFEGUARD,
     "the end of stderr of an ended program ends the wait; a child that keeps the pipe open is not waited for"),
    ("modules/sources/players/alloha_session.py", "AllohaSession.play", SAFEGUARD,
     "the event of the edge on the socket of the player ends the wait; EDGE_TIMEOUT_S stops it"),
    ("modules/sources/provider/connector.py", "Connector.run", BACKOFF,
     "reconnect to the provider of the socket, an external system, with a growing pause"),
    ("core/db.py", "Database._ensure_wal", BACKOFF,
     "PRAGMA journal_mode does not wait for the busy timeout: the lock of the database file is retried until _WAL_TIMEOUT"),
]

# (file, mark, ground). The mark is a text that must stand on the line of the call (a variable or function name).
ALLOWED_TIMERS: list[tuple[str, str, str]] = [
    ("web/src/components/AddSeriesModal.vue", "debounceTimer",
     "debounce of the address input (500 ms): the delay of typing is the subject, not a poll of data"),
    ("web/src/composables/useSSE.ts", "retryTimer",
     "a dropped stream is connected again after a growing pause (backoff to the server); no data is polled"),
    ("web/src/lib/gather.ts", "setTimeout(run, ms)",
     "the pause of a debounce: changes made in quick succession go together once the user is quiet (law-ui)"),
    ("web/src/Gallery.vue", "simTimer",
     "the gallery page animates a card through its states by hand; no data is polled"),
]

WAIT_NAMES = {"sleep", "wait_for"}
_TIMER = re.compile(r"(?<![\w.$])(?:window\.|globalThis\.)?(setInterval|setTimeout|useIntervalFn|useTimeoutPoll|useTimeoutFn)\s*\(")


def _predicate_wait(node: ast.Call) -> bool:
    """`condition.wait_for(predicate)` of asyncio.Condition: a wait for a state a signal changes, with no time in it (one
    argument, no timeout); `asyncio.wait_for(awaitable, timeout)` is the timed one."""
    receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
    named_asyncio = isinstance(receiver, ast.Name) and receiver.id == "asyncio"
    return receiver is not None and not named_asyncio and len(node.args) == 1 and not node.keywords


def find_waits(tree: ast.Module) -> list[tuple[int, str, str]]:
    """(line, function, call) of every sleep or wait_for call that is not `sleep(0)`."""
    aliases = {alias.asname: alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
               for alias in node.names if alias.asname and alias.name in WAIT_NAMES}
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = callee_name(node.func)
        name = aliases.get(name, name) if isinstance(node.func, ast.Name) else name
        if name not in WAIT_NAMES:
            continue
        if name == "sleep" and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == 0:
            continue
        if name == "wait_for" and _predicate_wait(node):
            continue
        found.append((node.lineno, qualname_of(node), dotted_name(node.func) or name))
    return found


def find_timers(text: str, filename: str) -> list[tuple[int, str, str]]:
    """(line, call, line text) of every timer call in web source, comments excluded."""
    clean = blank_comments(text, web_comments(text, filename))
    originals = text.splitlines()
    found = []
    for number, line in enumerate(clean.splitlines(), 1):
        for match in _TIMER.finditer(line):
            found.append((number, match.group(1), originals[number - 1].strip()))
    return found


def timer_ground(path: str, line_text: str, allowed: list[tuple[str, str, str]]) -> str | None:
    return next((ground for file, mark, ground in allowed if file == path and mark in line_text), None)


def timer_entry(path: str, line_text: str, allowed: list[tuple[str, str, str]]) -> tuple[str, str] | None:
    return next(((file, mark) for file, mark, _ground in allowed if file == path and mark in line_text), None)


def is_test_file(path: str) -> bool:
    return bool(re.search(r"\.(test|spec)\.[jt]sx?$", path))


def check_waits(root: Path, allowed_waits: list[tuple[str, str, str, str]] | None = None,
                allowed_timers: list[tuple[str, str, str]] | None = None) -> Outcome:
    allowed_waits = ALLOWED_WAITS if allowed_waits is None else allowed_waits
    allowed_timers = ALLOWED_TIMERS if allowed_timers is None else allowed_timers
    outcome = Outcome()
    used: set[tuple[str, str]] = set()
    used_timers: set[tuple[str, str] | None] = set()
    for file, function, category, _ground in allowed_waits:
        if category not in CATEGORIES:
            outcome.violations.append(Violation(file, 0, f"допуск «{function}»: категория «{category}» не из списка категорий сторожа"))
    for path in [*python_files(root / "modules"), *python_files(root / "core")]:
        where = rel(root, path)
        try:
            tree = parse_python(path.read_text(encoding="utf-8"), str(path))
        except SyntaxError as error:
            outcome.violations.append(Violation(where, error.lineno or 0, f"файл не разобран: {error.msg}"))
            continue
        for line, function, call in find_waits(tree):
            entry = next((e for e in allowed_waits if e[0] == where and e[1] in {function, "*"}), None)
            if entry is None:
                outcome.violations.append(Violation(where, line, f"{call} в {function or 'модуле'}: пауза или ожидание по времени вне допущенных мест"))
            else:
                used.add((entry[0], entry[1]))
                outcome.allowed.append(Violation(where, line, f"{call} в {function}: [{entry[2]}] {entry[3]}"))
    for file, function, _category, _ground in allowed_waits:
        if function != "*" and (file, function) not in used:
            outcome.violations.append(Violation(file, 0, f"допуск «{function}» не используется: в сторожe устаревшая запись"))
    web = root / "web" / "src"
    if web.is_dir():
        for path in sorted(web.rglob("*")):
            if path.suffix not in {".vue", ".ts", ".js"} or is_test_file(path.name) or "node_modules" in path.parts:
                continue
            where = rel(root, path)
            for line, call, line_text in find_timers(path.read_text(encoding="utf-8"), where):
                ground = timer_ground(where, line_text, allowed_timers)
                if ground is None:
                    outcome.violations.append(Violation(where, line, f"{call}( вне допущенных мест: интерфейс без таймеров, изменения приходят событием"))
                else:
                    used_timers.add(timer_entry(where, line_text, allowed_timers))
                    outcome.allowed.append(Violation(where, line, f"{call}( : {ground}"))
    for file, mark, _ground in allowed_timers:
        if (file, mark) not in used_timers:
            outcome.violations.append(Violation(file, 0, f"допуск «{mark}» не используется: в сторожe устаревшая запись"))
    return outcome


def main(argv: list[str] | None = None) -> int:
    return run_guard(NAME, lambda args: check_waits(args.root), argv)


if __name__ == "__main__":
    raise SystemExit(main())
