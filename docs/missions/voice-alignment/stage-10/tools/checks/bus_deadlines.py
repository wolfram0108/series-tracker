"""Guard: the fuse of a request over the bus is named by the owner of the topic, once (`core/deadlines.py`).

In modules/: a call of `request`, `_step`, `_ask` or `_request` whose `timeout` is a number or a bare name (a constant of
the module) is a violation: the caller does not know the price of the answer, and two callers of one topic gave it
different numbers. A `timeout` that is computed (an expression forwarded from outside) is not looked at here.
In core/deadlines.py: a topic of the table that no module answers (`self.handle`) is a violation: the entry is a stale
one, and a stale entry hides the topic that was renamed.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

from guard_common import Outcome, Violation, callee_name, parse_python, python_files, qualname_of, rel, run_guard

NAME = "bus_deadlines"
CALLEES = frozenset({"request", "_step", "_ask", "_request"})


def named_timeout(node: ast.Call) -> ast.expr | None:
    """The `timeout` of a request call when it is a number or a name; None when the call has none or computes it."""
    for keyword in node.keywords:
        if keyword.arg == "timeout" and isinstance(keyword.value, (ast.Constant, ast.Name, ast.Attribute)):
            return keyword.value
    return None


def handled_topics(tree: ast.Module) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and callee_name(node.func) == "handle" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
    return found


def table_topics(root: Path) -> frozenset[str] | None:
    """The topics the table of deadlines names, read from the tree that is checked; None when it has no such table."""
    path = root / "core" / "deadlines.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_checked_deadlines", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module.topics()


def check_deadlines(root: Path) -> Outcome:
    outcome = Outcome()
    handled: set[str] = set()
    for path in python_files(root / "modules"):
        where = rel(root, path)
        try:
            tree = parse_python(path.read_text(encoding="utf-8"), str(path))
        except SyntaxError as error:
            outcome.violations.append(Violation(where, error.lineno or 0, f"файл не разобран: {error.msg}"))
            continue
        handled |= handled_topics(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or callee_name(node.func) not in CALLEES:
                continue
            value = named_timeout(node)
            if value is not None:
                outcome.violations.append(Violation(
                    where, node.lineno, f"{callee_name(node.func)} в {qualname_of(node) or 'модуле'}: срок запроса "
                    f"{ast.unparse(value)} назван вызывающим; срок называет владелец темы в core/deadlines.py"))
    topics = table_topics(root)
    if topics is None:
        outcome.notes.append("таблицы сроков нет (core/deadlines.py): проверка её тем пропущена")
    else:
        for topic in sorted(topics - handled):
            outcome.violations.append(Violation("core/deadlines.py", 0, f"тема «{topic}» таблицы сроков: её не обрабатывает ни один модуль"))
    return outcome


def main(argv: list[str] | None = None) -> int:
    return run_guard(NAME, lambda args: check_deadlines(args.root), argv)


if __name__ == "__main__":
    raise SystemExit(main())
