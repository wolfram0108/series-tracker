"""Guard: the fuse of a request over the bus is named by the owner of the topic, once (`core/deadlines.py`).

In modules/: a call of `request`, `_step`, `_ask` or `_request` whose `timeout` is a number or a bare name (a constant of
the module) is a violation: the caller does not know the price of the answer, and two callers of one topic gave it
different numbers. A `timeout` that is computed (an expression forwarded from outside) is not looked at here.
In core/deadlines.py: a topic of the table that no module answers (`self.handle`) is a violation: the entry is a stale
one, and a stale entry hides the topic that was renamed.
Nesting: a topic that is asked for with `request` and whose handler awaits other requests (directly or through the methods of
its class that it awaits) has a fuse of at least the sum of the fuses of those requests, each counted once where it is
awaited; an asker that gave up before the answer finished would report a timeout for an action that was applied. A step
that has no fuse makes the topic unbounded. A step in a loop counts once (a step repeated for every unit of the request
is scaled with `units` in the table). Events and commands nobody waits for are not asked for with `request`, so not checked.
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
    """The `timeout` of a request call when it is a number or a name; None when the call has none or computes it. A call
    with `headers` is a request of an HTTP client (`client.request(method, url, headers=...)`), not one over the bus."""
    if any(keyword.arg == "headers" for keyword in node.keywords):
        return None
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


def table_module(root: Path):
    """The table of deadlines of the tree that is checked, as a module; None when the tree has no such table."""
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
    return module


def table_topics(root: Path) -> frozenset[str] | None:
    """The topics the table of deadlines names, read from the tree that is checked; None when it has no such table."""
    table = table_module(root)
    return None if table is None else table.topics()


def requested_topics(tree: ast.Module) -> set[str]:
    """The topics some call of this file asks for by literal name."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and callee_name(node.func) in CALLEES and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
    return found


def handlers_of(cls: ast.ClassDef) -> dict[str, ast.AST]:
    """{topic: method} for each `self.handle("topic", self.method)` of the class."""
    methods = {item.name: item for item in cls.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}
    found: dict[str, ast.AST] = {}
    for call in ast.walk(cls):
        if not (isinstance(call, ast.Call) and callee_name(call.func) == "handle" and len(call.args) >= 2):
            continue
        topic, target = call.args[0], call.args[1]
        if isinstance(topic, ast.Constant) and isinstance(topic.value, str) and isinstance(target, ast.Attribute) \
                and isinstance(target.value, ast.Name) and target.value.id == "self" and target.attr in methods:
            found[topic.value] = methods[target.attr]
    return found


# What a stretch of code waits for: (the sum of the fuses on its longest path, None when a step has none; the steps of that path).
Cost = tuple[float | None, list[str]]


def _plus(first: Cost, second: Cost) -> Cost:
    total = None if first[0] is None or second[0] is None else first[0] + second[0]
    return total, first[1] + second[1]


def _longest(options: list[Cost]) -> Cost:
    """The longest of alternatives (branches, or requests made together): a step without a fuse is the longest."""
    if not options:
        return 0.0, []
    return max(options, key=lambda each: float("inf") if each[0] is None else each[0])


class Waits:
    """The steps a method waits for with `await self.request(...)` (or `_step`, ...), and those of the methods of its class that it
    awaits. Branches count by their longest, a loop body once, requests gathered together by their longest; what is only
    started (`create_task(self.work())`) is not waited for, so it is not a step."""

    def __init__(self, methods: dict[str, ast.AST], deadline_of) -> None:
        self.methods, self.deadline_of = methods, deadline_of

    def method(self, name: str, seen: frozenset[str] = frozenset()) -> Cost:
        if name in seen or name not in self.methods:
            return 0.0, []
        return self.block(list(self.methods[name].body), seen | {name})

    def block(self, statements: list[ast.stmt], seen: frozenset[str]) -> Cost:
        """The longest path through the statements: a branch that returns or raises does not go on to what follows it."""
        if not statements:
            return 0.0, []
        node, rest = statements[0], statements[1:]
        if isinstance(node, ast.If):
            return _plus(self.expression(node.test, seen), _longest([
                self.block(node.body + rest, seen), self.block(node.orelse + rest, seen)]))
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            head = self.expression(node.test if isinstance(node, ast.While) else node.iter, seen)
            return _plus(_plus(head, self.block(node.body, seen)), self.block(node.orelse + rest, seen))
        if isinstance(node, (ast.With, ast.AsyncWith)):
            items: Cost = (0.0, [])
            for item in node.items:
                items = _plus(items, self.expression(item.context_expr, seen))
            return _plus(items, self.block(node.body + rest, seen))
        if isinstance(node, ast.Try):
            normal = self.block(node.body + node.orelse + node.finalbody + rest, seen)
            failed = [_plus(self.block(node.body, seen), self.block(handler.body + node.finalbody + rest, seen))
                      for handler in node.handlers]
            return _longest([normal, *failed])
        if isinstance(node, ast.Match):
            return _longest([self.block(case.body + rest, seen) for case in node.cases])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return self.block(rest, seen)
        here = self.expression(node, seen)
        if isinstance(node, (ast.Return, ast.Raise)):
            return here
        return _plus(here, self.block(rest, seen))

    def expression(self, node: ast.AST, seen: frozenset[str]) -> Cost:
        """The awaits inside a statement or an expression, in order; the arguments of an awaited helper that are themselves
        calls of the class (`await asyncio.gather(self.a(), self.b())`) are waited for together."""
        total: Cost = (0.0, [])
        for awaited in self._awaits(node):
            call = awaited.value
            if not isinstance(call, ast.Call):
                continue
            if self._is_self(call):
                total = _plus(total, self.call(call, seen))
            else:
                together = [self.call(argument, seen) for argument in call.args
                            if isinstance(argument, ast.Call) and self._is_self(argument)]
                total = _plus(total, _longest(together))
        return total

    @staticmethod
    def _awaits(node: ast.AST):
        """The awaits of a statement that are not inside another await, a nested function or a lambda."""
        stack = [node]
        while stack:
            current = stack.pop()
            if isinstance(current, ast.Await):
                yield current
                continue
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)) and current is not node:
                continue
            stack.extend(reversed(list(ast.iter_child_nodes(current))))

    @staticmethod
    def _is_self(call: ast.Call) -> bool:
        return isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id == "self"

    def call(self, call: ast.Call, seen: frozenset[str]) -> Cost:
        name = call.func.attr
        if name in CALLEES and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
            topic = call.args[0].value
            return self.deadline_of(topic), [topic]
        return self.method(name, seen)


def nesting_violations(root: Path, table, parsed: list[tuple[Path, ast.Module]]) -> list[Violation]:
    deadline_of = getattr(table, "deadline_of", None)
    if deadline_of is None:
        return []
    asked = set().union(*(requested_topics(tree) for _, tree in parsed)) if parsed else set()
    found: list[Violation] = []
    for path, tree in parsed:
        for cls in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
            methods = {item.name: item for item in cls.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}
            for topic, method in handlers_of(cls).items():
                if topic not in asked or deadline_of(topic) is None:
                    continue
                total, steps = Waits(methods, deadline_of).method(method.name)
                if not steps:
                    continue
                if total is None:
                    reason = (f"тема «{topic}» имеет срок {deadline_of(topic):.0f} с, а её обработчик ждёт запрос без срока "
                              f"({', '.join(sorted(set(steps)))}): тема тоже без срока")
                elif deadline_of(topic) < total:
                    reason = (f"тема «{topic}»: срок {deadline_of(topic):.0f} с короче суммы сроков запросов, которых её обработчик "
                              f"ждёт ({total:.0f} с: {', '.join(sorted(set(steps)))}); срок составной темы выводит "
                              "таблица (_NESTED в core/deadlines.py)")
                else:
                    continue
                found.append(Violation(rel(root, path), getattr(method, "lineno", 0), reason))
    return found


def check_deadlines(root: Path) -> Outcome:
    outcome = Outcome()
    handled: set[str] = set()
    parsed: list[tuple[Path, ast.Module]] = []
    for path in python_files(root / "modules"):
        where = rel(root, path)
        try:
            tree = parse_python(path.read_text(encoding="utf-8"), str(path))
        except SyntaxError as error:
            outcome.violations.append(Violation(where, error.lineno or 0, f"файл не разобран: {error.msg}"))
            continue
        handled |= handled_topics(tree)
        parsed.append((path, tree))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or callee_name(node.func) not in CALLEES:
                continue
            value = named_timeout(node)
            if value is not None:
                outcome.violations.append(Violation(
                    where, node.lineno, f"{callee_name(node.func)} в {qualname_of(node) or 'модуле'}: срок запроса "
                    f"{ast.unparse(value)} назван вызывающим; срок называет владелец темы в core/deadlines.py"))
    table = table_module(root)
    if table is None:
        outcome.notes.append("таблицы сроков нет (core/deadlines.py): проверка её тем пропущена")
    else:
        outcome.violations.extend(nesting_violations(root, table, parsed))
        for topic in sorted(table.topics() - handled):
            outcome.violations.append(Violation("core/deadlines.py", 0, f"тема «{topic}» таблицы сроков: её не обрабатывает ни один модуль"))
    return outcome


def main(argv: list[str] | None = None) -> int:
    return run_guard(NAME, lambda args: check_deadlines(args.root), argv)


if __name__ == "__main__":
    raise SystemExit(main())
