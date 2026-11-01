"""Guard: no pause or polling as a way to wait for another module (ARCHITECTURE.md of stage 10, section 7).

In modules/: a call of `asyncio.sleep`, `time.sleep` or `wait_for` outside ALLOWED_WAITS is a violation; `sleep(0)`
is a yield to the loop, not a pause. In web/src: `setInterval`, `setTimeout` and the VueUse polling helpers outside
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

# (file, function qualname, ground). '*' as the function allows the whole file.
ALLOWED_WAITS: list[tuple[str, str, str]] = [
    ("modules/scan/module.py", "ScanModule._scheduler_loop",
     f"schedule of the auto scan: time is the subject of the task ({SECTION_7}, 'Расписание автоскана')"),
    ("modules/sources/module.py", "SourcesModule._polite",
     f"pace of requests to a host, 0.6 s (R-33): time is the subject of the task ({SECTION_7})"),
    ("modules/sources/policy.py", "*",
     f"the retry policy and the pace of requests to sites and players ({SECTION_7}); it sleeps through core.retry"),
    ("modules/voices/recovery.py", "Recovery._rest",
     f"rest of the cleanup and retry journal; a signal (wake) ends it early, the delay is the safeguard ({SECTION_7})"),
    ("modules/scan/sync.py", "TableSignals.verdict",
     f"wait for the signal of a table with a safeguard of twice the measured read ({SECTION_7}, last row)"),
    ("modules/slicing/module.py", "_cut",
     f"wait for the ffmpeg process with a safeguard ({SECTION_7}, 'Таймауты')"),
    ("modules/downloads/ytdlp.py", "_next_line",
     f"wait for a line of the yt-dlp process; silence longer than STALL_TIMEOUT_S is a safeguard ({SECTION_7})"),
    ("modules/sources/players/vibix_protocol.py", "capture_with_browser",
     f"wait for the response signal of the browser page with a safeguard ({SECTION_7}, 'Таймауты запросов к плеерам')"),
    ("modules/sources/players/vibix_protocol.py", "_manifest",
     f"wait for the manifest response signal with a safeguard ({SECTION_7}, 'Таймауты запросов к плеерам')"),
]

# (file, mark, ground). The mark is a text that must stand on the line of the call (a variable or function name).
ALLOWED_TIMERS: list[tuple[str, str, str]] = [
    ("web/src/components/AddSeriesModal.vue", "debounceTimer",
     "debounce of the address input (500 ms): the delay of typing is the subject, not a poll of data"),
    ("web/src/Gallery.vue", "simTimer",
     "the gallery page animates a card through its states by hand; no data is polled"),
]

WAIT_NAMES = {"sleep", "wait_for"}
_TIMER = re.compile(r"(?<![\w.$])(?:window\.|globalThis\.)?(setInterval|setTimeout|useIntervalFn|useTimeoutPoll|useTimeoutFn)\s*\(")


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


def check_waits(root: Path, allowed_waits: list[tuple[str, str, str]] | None = None,
                allowed_timers: list[tuple[str, str, str]] | None = None) -> Outcome:
    allowed_waits = ALLOWED_WAITS if allowed_waits is None else allowed_waits
    allowed_timers = ALLOWED_TIMERS if allowed_timers is None else allowed_timers
    outcome = Outcome()
    used: set[tuple[str, str]] = set()
    used_timers: set[tuple[str, str] | None] = set()
    for path in python_files(root / "modules"):
        where = rel(root, path)
        try:
            tree = parse_python(path.read_text(encoding="utf-8"), str(path))
        except SyntaxError as error:
            outcome.violations.append(Violation(where, error.lineno or 0, f"файл не разобран: {error.msg}"))
            continue
        for line, function, call in find_waits(tree):
            entry = next(((f, fn, g) for f, fn, g in allowed_waits if f == where and fn in {function, "*"}), None)
            if entry is None:
                outcome.violations.append(Violation(where, line, f"{call} в {function or 'модуле'}: пауза или ожидание по времени вне допущенных мест"))
            else:
                used.add((entry[0], entry[1]))
                outcome.allowed.append(Violation(where, line, f"{call} в {function}: {entry[2]}"))
    for file, function, _ground in allowed_waits:
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
