"""Shared parts of the stage-10 guards.

A guard is a pure function over a tree plus a thin main(). What the guards share lives here, so that no rule is
written twice: the report format, file walking, a static string evaluator, the git diff reader and a comment
scanner for web sources.

Exit codes of every guard: 0 - the property holds, 1 - violations found, 2 - the guard itself broke (device
defect, not a verdict about the subject). The last printed line is always `ИЗМЕРЕНИЕ <name>: PASS` or
`ИЗМЕРЕНИЕ <name>: FAIL <n>`, the trigger of the run predicate.
"""
from __future__ import annotations

import argparse
import ast
import bisect
import re
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

CYRILLIC = re.compile("[Ѐ-ӿ]")
# Stand-in for a value that cannot be known statically; chosen so that it cannot occur in source text.
UNKNOWN = "\x00"
WORKTREE = "WORKTREE"
# Files that a generator writes (`npm run gen:api`): their comments are the API descriptions of the Python models.
GENERATED_FILES = ("web/src/api/schema.d.ts",)


@dataclass(frozen=True, order=True)
class Violation:
    path: str
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line} {self.reason}"


@dataclass
class Outcome:
    violations: list[Violation] = field(default_factory=list)
    allowed: list[Violation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def default_root() -> Path:
    # tools/checks/guard_common.py sits six levels below the repository root.
    return Path(__file__).resolve().parents[6]


def run_guard(name: str, build: Callable[[argparse.Namespace], Outcome], argv: list[str] | None = None,
              add_arguments: Callable[[argparse.ArgumentParser], None] | None = None) -> int:
    """Parse arguments, build the outcome, print the report and the trigger line; return the exit code."""
    parser = argparse.ArgumentParser(prog=name)
    parser.add_argument("--root", type=Path, default=default_root(), help="repository root to check")
    if add_arguments is not None:
        add_arguments(parser)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        outcome = build(args)
    except Exception:  # noqa: BLE001 - a broken guard must still print its trigger line
        traceback.print_exc()
        print(f"ИЗМЕРЕНИЕ {name}: FAIL прибор")
        return 2
    for violation in sorted(set(outcome.violations)):
        print(violation)
    for violation in sorted(set(outcome.allowed)):
        print(f"ДОПУЩЕНО {violation}")
    for note in outcome.notes:
        print(f"ИНФО {note}")
    count = len(set(outcome.violations))
    print(f"ИЗМЕРЕНИЕ {name}: PASS" if count == 0 else f"ИЗМЕРЕНИЕ {name}: FAIL {count}")
    return 0 if count == 0 else 1


# --- files and syntax trees --------------------------------------------------------------------------------

def python_files(directory: Path) -> Iterator[Path]:
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def attach_parents(tree: ast.AST) -> None:
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._parent = parent  # type: ignore[attr-defined]


def parse_python(text: str, filename: str = "<string>") -> ast.Module:
    tree = ast.parse(text, filename=filename)
    attach_parents(tree)
    return tree


def parent_of(node: ast.AST) -> ast.AST | None:
    return getattr(node, "_parent", None)


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def enclosing_functions(node: ast.AST) -> Iterator[ast.AST]:
    """Function-like scopes around a node, innermost first (lambdas included)."""
    current = parent_of(node)
    while current is not None:
        if isinstance(current, (*FUNCTIONS, ast.Lambda)):
            yield current
        current = parent_of(current)


def qualname_of(node: ast.AST) -> str:
    """Dotted names of the classes and functions around a node, outermost first; empty at module level."""
    names: list[str] = []
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, (*FUNCTIONS, ast.ClassDef)):
            names.append(current.name)
        current = parent_of(current)
    return ".".join(reversed(names))


def is_docstring(node: ast.AST) -> bool:
    expr = parent_of(node)
    if not isinstance(expr, ast.Expr):
        return False
    owner = parent_of(expr)
    return (isinstance(owner, (ast.Module, ast.ClassDef, *FUNCTIONS)) and bool(owner.body) and owner.body[0] is expr
            and isinstance(node, ast.Constant) and isinstance(node.value, str))


def callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def dotted_name(node: ast.expr) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


# --- static evaluation of strings --------------------------------------------------------------------------

_MISSING = object()
MAX_VALUES = 64
MAX_DEPTH = 8


def _scope_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Nodes that belong to one function scope: nested functions and classes are other scopes."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (*FUNCTIONS, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))


class Evaluator:
    """The set of strings an expression can take, found without running the code.

    Understands literals, f-strings, `+`, conditional expressions, module constants, names bound in the enclosing
    function (assignments and loops over literal tuples), parameters whose every call site in the same file is
    resolvable, and constants imported by name. Anything else is unresolvable.
    """

    def __init__(self, tree: ast.Module, known: dict[str, set[str]] | None = None) -> None:
        attach_parents(tree)
        self.known = known or {}
        self.imported = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                         for alias in node.names}
        self.module_bindings: dict[str, list[ast.expr]] = {}
        for statement in tree.body:
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        self.module_bindings.setdefault(target.id, []).append(statement.value)
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name) and statement.value:
                self.module_bindings.setdefault(statement.target.id, []).append(statement.value)
        self.calls: dict[str, list[ast.Call]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = callee_name(node.func)
                if name:
                    self.calls.setdefault(name, []).append(node)

    def values(self, node: ast.expr, unresolved: str | None = None, depth: int = 0) -> set[str] | None:
        """The possible strings, or None when some part is unknown; `unresolved` replaces unknown f-string parts."""
        if depth > MAX_DEPTH:
            return None
        deeper = depth + 1
        if isinstance(node, ast.Constant):
            return {node.value} if isinstance(node.value, str) else None
        if isinstance(node, ast.JoinedStr):
            result = {""}
            for part in node.values:
                if isinstance(part, ast.Constant):
                    options: set[str] | None = {str(part.value)}
                elif isinstance(part, ast.FormattedValue):
                    options = self.values(part.value, unresolved, deeper)
                    if options is None:
                        if unresolved is None:
                            return None
                        options = {unresolved}
                else:
                    return None
                result = {left + right for left in result for right in options}
                if len(result) > MAX_VALUES:
                    return None
            return result
        if isinstance(node, ast.IfExp):
            return self._union([node.body, node.orelse], unresolved, deeper)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            return self._union(node.values, unresolved, deeper)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self.values(node.left, unresolved, deeper)
            right = self.values(node.right, unresolved, deeper)
            if left is None or right is None or len(left) * len(right) > MAX_VALUES:
                return None
            return {a + b for a in left for b in right}
        if isinstance(node, ast.Name):
            return self._name(node, unresolved, deeper)
        return None

    def _union(self, nodes: list[ast.expr], unresolved: str | None, depth: int) -> set[str] | None:
        result: set[str] = set()
        for node in nodes:
            found = self.values(node, unresolved, depth)
            if found is None:
                return None
            result |= found
        return result

    def items(self, node: ast.expr, unresolved: str | None, depth: int) -> set[str] | None:
        """Strings that iterating over a literal collection yields."""
        if depth > MAX_DEPTH:
            return None
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return self._union(list(node.elts), unresolved, depth + 1)
        if isinstance(node, ast.Name) and node.id in self.module_bindings:
            result: set[str] = set()
            for value in self.module_bindings[node.id]:
                found = self.items(value, unresolved, depth + 1)
                if found is None:
                    return None
                result |= found
            return result
        return None

    def _name(self, node: ast.Name, unresolved: str | None, depth: int) -> set[str] | None:
        name = node.id
        for function in enclosing_functions(node):
            found = self._in_scope(function, name, unresolved, depth)
            if found is not _MISSING:
                return found  # type: ignore[return-value]
        if name in self.module_bindings:
            return self._union(self.module_bindings[name], unresolved, depth)
        if name in self.imported:
            return self.known.get(name)
        return None

    def _in_scope(self, function: ast.AST, name: str, unresolved: str | None, depth: int):
        arguments = function.args  # type: ignore[attr-defined]
        parameters = [a.arg for a in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)]
        parameters += [a.arg for a in (arguments.vararg, arguments.kwarg) if a is not None]
        if name in parameters:
            return self._parameter(function, name, unresolved, depth)
        result: set[str] = set()
        bound = False
        for node in _scope_nodes(function):
            found: set[str] | None
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                bound, found = True, self.values(node.value, unresolved, depth + 1)
            elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name
                  and node.value is not None):
                bound, found = True, self.values(node.value, unresolved, depth + 1)
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
                return None
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)) and isinstance(node.target, ast.Name) \
                    and node.target.id == name:
                bound, found = True, self.items(node.iter, unresolved, depth + 1)
            else:
                continue
            if found is None:
                return None
            result |= found
        return result if bound else _MISSING

    def _parameter(self, function: ast.AST, name: str, unresolved: str | None, depth: int) -> set[str] | None:
        if isinstance(function, ast.Lambda):
            return None
        arguments = function.args  # type: ignore[attr-defined]
        positional = [a.arg for a in (*arguments.posonlyargs, *arguments.args)]
        sites = self.calls.get(function.name, [])  # type: ignore[attr-defined]
        if not sites:
            return None
        result: set[str] = set()
        for call in sites:
            argument = next((kw.value for kw in call.keywords if kw.arg == name), None)
            if argument is None and name in positional:
                index = positional.index(name)
                if positional[:1] in (["self"], ["cls"]) and isinstance(call.func, ast.Attribute):
                    index -= 1
                if 0 <= index < len(call.args) and not isinstance(call.args[index], ast.Starred):
                    argument = call.args[index]
            if argument is None:
                argument = _default_of(arguments, name)
            if argument is None:
                return None
            found = self.values(argument, unresolved, depth + 1)
            if found is None:
                return None
            result |= found
        return result


def _default_of(arguments: ast.arguments, name: str) -> ast.expr | None:
    positional = [*arguments.posonlyargs, *arguments.args]
    defaults = arguments.defaults
    for offset, arg in enumerate(positional[len(positional) - len(defaults):]):
        if arg.arg == name:
            return defaults[offset]
    for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        if arg.arg == name:
            return default
    return None


def global_constants(root: Path, directories: tuple[str, ...] = ("modules", "core")) -> dict[str, set[str]]:
    """Module-level string constants by name, for names that a file imports from another file."""
    found: dict[str, set[str]] = {}
    for directory in directories:
        for path in python_files(root / directory):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for statement in tree.body:
                if (isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant)
                        and isinstance(statement.value.value, str)):
                    for target in statement.targets:
                        if isinstance(target, ast.Name):
                            found.setdefault(target.id, set()).add(statement.value.value)
    return found


# --- bus topics --------------------------------------------------------------------------------------------

def topic_matches(pattern: str, topic: str) -> bool:
    """Same rule as core.envelope.topic_matches: `*` is one segment, `#` is the whole tail (last only)."""
    if pattern == "#":
        return True
    pattern_parts = pattern.split(".")
    topic_parts = topic.split(".")
    for index, part in enumerate(pattern_parts):
        if part == "#":
            return index == len(pattern_parts) - 1
        if index >= len(topic_parts):
            return False
        if part != "*" and part != topic_parts[index]:
            return False
    return len(pattern_parts) == len(topic_parts)


def topics_related(left: str, right: str) -> bool:
    return topic_matches(left, right) or topic_matches(right, left)


# --- git ---------------------------------------------------------------------------------------------------

def git(root: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=off", *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if check and done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_lines(root: Path, base: str, head: str, pathspecs: list[str]) -> dict[str, set[int]]:
    """New-side line numbers of added or changed lines per file between base and head.

    head == WORKTREE compares the working tree with base and counts untracked files whole, so that work that is
    not committed yet can be checked.
    """
    command = ["diff", "--no-color", "--no-ext-diff", "-U0", "--diff-filter=ACMR"]
    command += [base] if head == WORKTREE else [f"{base}..{head}"]
    output = git(root, *command, "--", *pathspecs)
    result: dict[str, set[int]] = {}
    path: str | None = None
    line_no = 0
    in_hunk = False
    for raw in output.splitlines():
        if raw.startswith("diff --git "):
            in_hunk = False
            path = None
        elif not in_hunk and raw.startswith("+++ "):
            target = raw[4:].split("\t")[0]
            path = None if target == "/dev/null" else target[2:]
        elif raw.startswith("@@"):
            match = _HUNK.match(raw)
            if match:
                line_no = int(match.group(1))
                in_hunk = True
        elif in_hunk and path is not None and raw.startswith("+"):
            result.setdefault(path, set()).add(line_no)
            line_no += 1
    if head == WORKTREE:
        for name in git(root, "ls-files", "--others", "--exclude-standard", "--", *pathspecs).splitlines():
            count = len((root / name).read_text(encoding="utf-8", errors="replace").splitlines())
            if count:
                result.setdefault(name, set()).update(range(1, count + 1))
    return result


def file_text(root: Path, head: str, path: str) -> str:
    if head == WORKTREE:
        return (root / path).read_text(encoding="utf-8", errors="replace")
    return git(root, "show", f"{head}:{path}")


# --- comments in web sources ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Comment:
    first_line: int
    last_line: int
    text: str
    start: int
    end: int


_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")


def _skip_string(text: str, index: int, end: int) -> int:
    quote = text[index]
    index += 1
    while index < end:
        char = text[index]
        if char == "\\":
            index += 2
        elif char == quote:
            return index + 1
        elif char == "\n":
            return index
        else:
            index += 1
    return end


def _skip_regex(text: str, index: int, end: int) -> int:
    index += 1
    in_class = False
    while index < end:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "\n":
            return index
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            return index + 1
        index += 1
    return end


def _scan_code(text: str, start: int, end: int, line_comments: bool) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    stack: list[list] = [["code", 0]]  # [mode, braces]; a template literal pushes tpl, its ${ } pushes code
    previous = ""
    index = start
    while index < end:
        mode, _ = stack[-1]
        char = text[index]
        if mode == "tpl":
            if char == "\\":
                index += 2
            elif char == "`":
                stack.pop()
                previous = "`"
                index += 1
            elif text.startswith("${", index):
                stack.append(["code", 0])
                index += 2
            else:
                index += 1
            continue
        if char in "'\"":
            index = _skip_string(text, index, end)
            previous = char
            continue
        if char == "`" and line_comments:
            stack.append(["tpl", 0])
            index += 1
            continue
        if line_comments and text.startswith("//", index):
            stop = text.find("\n", index, end)
            stop = end if stop < 0 else stop
            spans.append((index, stop))
            index = stop
            continue
        if text.startswith("/*", index):
            stop = text.find("*/", index + 2, end)
            stop = end if stop < 0 else stop + 2
            spans.append((index, stop))
            index = stop
            continue
        if line_comments and char == "/" and (previous == "" or previous in _REGEX_AFTER):
            index = _skip_regex(text, index, end)
            previous = "/"
            continue
        if char == "{":
            stack[-1][1] += 1
        elif char == "}":
            if stack[-1][1] == 0 and len(stack) > 1:
                stack.pop()
                index += 1
                continue
            stack[-1][1] = max(0, stack[-1][1] - 1)
        if not char.isspace():
            previous = char
        index += 1
    return spans


def _scan_html(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    index = start
    while True:
        found = text.find("<!--", index, end)
        if found < 0:
            return spans
        stop = text.find("-->", found + 4, end)
        stop = end if stop < 0 else stop + 3
        spans.append((found, stop))
        index = stop


_BLOCK = re.compile(r"<(script|style)\b[^>]*>(.*?)</\1\s*>", re.S)


def web_comments(text: str, filename: str) -> list[Comment]:
    """Comments of a .ts/.js/.css/.vue file: `//` and `/* */` in code, `/* */` in styles, `<!-- -->` in templates."""
    suffix = filename.rsplit(".", 1)[-1].lower()
    if suffix == "vue":
        spans: list[tuple[int, int]] = []
        cursor = 0
        for block in _BLOCK.finditer(text):
            spans += _scan_html(text, cursor, block.start(2))
            spans += _scan_code(text, block.start(2), block.end(2), line_comments=block.group(1) == "script")
            cursor = block.end(2)
        spans += _scan_html(text, cursor, len(text))
    elif suffix == "css":
        spans = _scan_code(text, 0, len(text), line_comments=False)
    elif suffix in {"ts", "tsx", "js", "jsx", "mjs", "cjs"}:
        spans = _scan_code(text, 0, len(text), line_comments=True)
    else:
        return []
    newlines = [i for i, char in enumerate(text) if char == "\n"]
    result = []
    for begin, stop in sorted(spans):
        first = bisect.bisect_left(newlines, begin) + 1
        last = bisect.bisect_left(newlines, max(begin, stop - 1)) + 1
        result.append(Comment(first, last, text[begin:stop], begin, stop))
    return result


def blank_comments(text: str, comments: list[Comment]) -> str:
    """The text with every comment replaced by spaces, so that line numbers and columns stay."""
    pieces: list[str] = []
    cursor = 0
    for comment in comments:
        pieces.append(text[cursor:comment.start])
        pieces.append("".join("\n" if char == "\n" else " " for char in text[comment.start:comment.end]))
        cursor = comment.end
    pieces.append(text[cursor:])
    return "".join(pieces)
