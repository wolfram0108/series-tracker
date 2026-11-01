"""Guard: REVISION.md of the stage agrees with the code.

1. Every table row of REVISION.md has a status: подтверждена, перепроектирована or удалена.
2. Every row with the status «удалена» marked «сделано (П-N)» (after a dash or as the whole cell) has at least one line in tools/data/removed_symbols.txt
   (a symbol, or `none:` with the reason why no symbol can be checked).
3. Every line of that file points at an existing row (`откуда: <section>/<row>`), names a symbol that stands in the
   text of that row, and the symbol is not defined in the code: modules/, core/ and web/src (a definition, not a
   mention in a comment).

Line format of the data file (the separator is ` — `):
    <kind>:<symbol> — откуда: <section>/<row> [— допуск: <path>[, <path>] — причина: <text>]
Kinds: def (function or method; `Class.method` pins the class), class, const (module constant, TS declaration or
object key), topic (not handled, not published, not requested on the bus), literal (no such string literal in the
Python code, e.g. a route, whose `{parameters}` are compared unnamed, or an SSE name), file (path does not exist), none (nothing to check).
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from bus_catalog import code_topics
from guard_common import FUNCTIONS, Outcome, Violation, is_docstring, parse_python, python_files, rel, run_guard

NAME = "revision_registry"
REVISION_DOC = "docs/missions/voice-alignment/stage-10/REVISION.md"
SYMBOLS_FILE = "docs/missions/voice-alignment/stage-10/tools/data/removed_symbols.txt"
STATUS_WORDS = ("подтверждена", "перепроектирована", "удалена")
DONE_MARK = re.compile(r"сделано\s*\(П-\d+\)")
KINDS = {"def", "class", "const", "topic", "literal", "file", "none"}


@dataclass
class Row:
    section: str
    index: int
    line: int
    status: str
    text: str


@dataclass
class Symbol:
    kind: str
    name: str
    section: str
    index: int
    line: int
    allowed_paths: list[str] = field(default_factory=list)
    reason: str = ""


def parse_revision(text: str) -> tuple[list[Row], list[tuple[int, str]]]:
    """Rows of the tables that have a «Статус» column, and the problems met while parsing: (line, text)."""
    rows: list[Row] = []
    problems: list[tuple[int, str]] = []
    section = ""
    header: list[str] | None = None
    counter = 0
    for number, line in enumerate(text.splitlines(), 1):
        heading = re.match(r"^#{2,3}\s+(\d+(?:\.\d+)?)\.", line)
        if heading:
            section, header, counter = heading.group(1), None, 0
            continue
        if not line.lstrip().startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        if "Статус" not in header:
            continue
        if len(cells) != len(header):
            problems.append((number, f"строка таблицы разобрана на {len(cells)} столбцов вместо {len(header)}"))
            continue
        counter += 1
        rows.append(Row(section, counter, number, cells[header.index("Статус")], " | ".join(cells)))
    return rows, problems


def parse_symbols(text: str) -> tuple[list[Symbol], list[tuple[int, str]]]:
    symbols: list[Symbol] = []
    problems: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = [f.strip() for f in line.split(" — ")]
        head = re.fullmatch(r"(\w+):(.+)", fields[0])
        origin = re.fullmatch(r"откуда:\s*(\d+(?:\.\d+)?)/(\d+)", fields[1]) if len(fields) > 1 else None
        if not head or head.group(1) not in KINDS or not origin:
            problems.append((number, "строка не разобрана: ожидается «вид:символ — откуда: раздел/строка»"))
            continue
        symbol = Symbol(head.group(1), head.group(2).strip(), origin.group(1), int(origin.group(2)), number)
        for extra in fields[2:]:
            if extra.startswith("допуск:"):
                symbol.allowed_paths = [p.strip() for p in extra[len("допуск:"):].split(",") if p.strip()]
            elif extra.startswith("причина:"):
                symbol.reason = extra[len("причина:"):].strip()
        if symbol.allowed_paths and not symbol.reason:
            problems.append((number, "допуск без причины"))
        symbols.append(symbol)
    return symbols, problems


def normalized(text: str) -> str:
    """A route with its path parameters unnamed: `/api/x/{id}` and `/api/x/{series_id}` are one route."""
    return re.sub(r"\{[^}]*\}", "{}", text)


def anchor_of(symbol: Symbol) -> str:
    """The text of the symbol that must stand in the row it came from."""
    if symbol.kind == "def":
        return symbol.name.split(".")[-1]
    if symbol.kind == "file":
        return symbol.name.rstrip("/").split("/")[-1]
    return normalized(symbol.name) if symbol.kind == "literal" else symbol.name


@dataclass
class Index:
    """What the code defines, collected once: (name, class, path, line) of functions, (name, path, line) of the rest."""
    functions: list[tuple[str, str, str, int]] = field(default_factory=list)
    classes: list[tuple[str, str, int]] = field(default_factory=list)
    constants: list[tuple[str, str, int]] = field(default_factory=list)
    strings: list[tuple[str, str, int]] = field(default_factory=list)
    topics: list = field(default_factory=list)


_TS_DECLARATION = re.compile(r"\b(?:const|let|var|function|class|enum|interface|type)\s+(\w+)|^\s*(\w+)\??:\s*\{\s*$")


def build_index(root: Path) -> Index:
    index = Index(topics=code_topics(root)[0])
    for path in [*python_files(root / "modules"), *python_files(root / "core")]:
        try:
            tree = parse_python(path.read_text(encoding="utf-8"), str(path))
        except SyntaxError:
            continue
        where = rel(root, path)
        for node in ast.walk(tree):
            owner = getattr(node, "_parent", None)
            if isinstance(node, FUNCTIONS):
                index.functions.append((node.name, owner.name if isinstance(owner, ast.ClassDef) else "", where, node.lineno))
            elif isinstance(node, ast.ClassDef):
                index.classes.append((node.name, where, node.lineno))
            elif isinstance(node, ast.Assign) and isinstance(owner, ast.Module):
                index.constants += [(t.id, where, node.lineno) for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 200 \
                    and not is_docstring(node):
                index.strings.append((node.value, where, node.lineno))
    web = root / "web" / "src"
    for path in sorted(web.rglob("*")) if web.is_dir() else []:
        if path.suffix in {".ts", ".vue", ".js"} and not re.search(r"\.(test|spec)\.", path.name):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                index.constants += [(m.group(1) or m.group(2), rel(root, path), number) for m in _TS_DECLARATION.finditer(line)]
    return index


def definitions(index: Index, root: Path, symbol: Symbol) -> list[tuple[str, int]]:
    """(path, line) where the symbol is defined; for a topic the places where it is used on the bus."""
    if symbol.kind == "file":
        return [(symbol.name, 0)] if (root / symbol.name).exists() else []
    if symbol.kind == "topic":
        return sorted({(u.path, u.line) for u in index.topics if u.topic == symbol.name})
    if symbol.kind == "def":
        class_name, _, member = symbol.name.rpartition(".")
        return [(path, line) for name, owner, path, line in index.functions
                if name == member and (not class_name or owner == class_name)]
    if symbol.kind == "class":
        return [(path, line) for name, path, line in index.classes if name == symbol.name]
    if symbol.kind == "literal":
        return [(path, line) for value, path, line in index.strings if normalized(value) == normalized(symbol.name)]
    return [(path, line) for name, path, line in index.constants if name == symbol.name]


def check_revision(root: Path, revision_doc: str = REVISION_DOC, symbols_file: str = SYMBOLS_FILE) -> Outcome:
    outcome = Outcome()
    revision_path, symbols_path = root / revision_doc, root / symbols_file
    if not revision_path.is_file():
        return Outcome([Violation(revision_doc, 0, "реестр ревизии не найден")])
    rows, row_problems = parse_revision(revision_path.read_text(encoding="utf-8"))
    for line, problem in row_problems:
        outcome.violations.append(Violation(revision_doc, line, problem))
    if not rows:
        outcome.violations.append(Violation(revision_doc, 0, "не разобрано ни одной строки с колонкой «Статус»"))
    for row in rows:
        if not row.status.lower().startswith(STATUS_WORDS):
            outcome.violations.append(Violation(revision_doc, row.line, f"у точки (раздел {row.section}, строка {row.index}) нет допустимого статуса: «{row.status}»"))
    if not symbols_path.is_file():
        outcome.violations.append(Violation(symbols_file, 0, "файл данных не найден"))
        return outcome
    symbols, symbol_problems = parse_symbols(symbols_path.read_text(encoding="utf-8"))
    for line, problem in symbol_problems:
        outcome.violations.append(Violation(symbols_file, line, problem))
    by_key = {(row.section, row.index): row for row in rows}
    mapped: set[tuple[str, int]] = set()
    index = build_index(root)
    for symbol in symbols:
        row = by_key.get((symbol.section, symbol.index))
        if row is None:
            outcome.violations.append(Violation(symbols_file, symbol.line, f"«откуда» указывает на несуществующую строку {symbol.section}/{symbol.index}"))
            continue
        mapped.add((symbol.section, symbol.index))
        if symbol.kind == "none":
            continue
        if anchor_of(symbol) not in (normalized(row.text) if symbol.kind == "literal" else row.text):
            outcome.violations.append(Violation(symbols_file, symbol.line, f"«{anchor_of(symbol)}» не стоит в тексте строки {symbol.section}/{symbol.index} реестра"))
        for where, line in definitions(index, root, symbol):
            if where in symbol.allowed_paths:
                outcome.allowed.append(Violation(where, line, f"«{symbol.name}» ({symbol.kind}) допущено: {symbol.reason}"))
            else:
                outcome.violations.append(Violation(where, line, f"удалённый символ «{symbol.name}» ({symbol.kind}) есть в коде (реестр {symbol.section}/{symbol.index})"))
    for row in rows:
        if row.status.lower().startswith("удалена") and DONE_MARK.search(row.text) and (row.section, row.index) not in mapped:
            outcome.violations.append(Violation(revision_doc, row.line, f"строка {row.section}/{row.index} «удалена — сделано», но в {symbols_file} для неё нет ни символа, ни «none»"))
    return outcome


def main(argv: list[str] | None = None) -> int:
    def add(parser):
        parser.add_argument("--revision-doc", default=REVISION_DOC)
        parser.add_argument("--symbols-file", default=SYMBOLS_FILE)

    return run_guard(NAME, lambda args: check_revision(args.root, args.revision_doc, args.symbols_file), argv, add)


if __name__ == "__main__":
    raise SystemExit(main())
