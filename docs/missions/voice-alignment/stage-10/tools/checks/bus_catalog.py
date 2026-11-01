"""Guard: the bus topics of the code and the catalogs of the documents agree in both directions.

Code side (modules/): a topic is HANDLED by `self.handle("…")`, PUBLISHED by `publish_event("…")`,
`send_command("…")` or a module-level `*_TOPIC = "…"` constant (the topic given to a publish callback), and
REQUESTED by `request("…")`. Names are resolved statically, an f-string with an unknown part becomes a pattern
with `*`, and what cannot be resolved is printed as a note, not as a violation.

Documents: contracts/bus_topics.md (both directions) and section 5 of ARCHITECTURE.md of the stage (every row must
have code and a row in bus_topics.md). A topic in a document is a dotted token inside backticks, taken from the
second cell of a table headed `Модуль` and from the first cell of any other table; its first segment must be a known
namespace (a module directory or the first segment of a topic of the code), other dotted tokens are prose and are
listed as notes. Annotations in braces, parentheses and brackets are dropped. `a.b/c` stands for `a.b` and `a.c`;
`.c` after `a.b` of the same module stands for the sibling `a.c` or the module-relative `<module>.c`, and the entry
is satisfied by either; `*` and `#` make a pattern with the rules of the bus.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from guard_common import (Evaluator, Outcome, Violation, callee_name, global_constants, parse_python, python_files, rel,
                          run_guard, topics_related)

NAME = "bus_catalog"
TOPICS_DOC = "contracts/bus_topics.md"
ARCHITECTURE_DOC = "docs/missions/voice-alignment/stage-10/ARCHITECTURE.md"

HANDLE, PUBLISH, REQUEST = "handle", "publish", "request"
_CALL_KINDS = {"handle": HANDLE, "publish_event": PUBLISH, "send_command": PUBLISH, "request": REQUEST}
_TOPIC = re.compile(r"^[a-z_][a-z0-9_]*(?:\.[a-z0-9_*#]+)+$")
_TOKEN = re.compile(r"(?<![A-Za-z0-9_.*#])(\.?[a-z_][a-z0-9_]*(?:\.[a-z0-9_*#]+)*(?:/[a-z0-9_]+(?:\.[a-z0-9_]+)*)*)(?![A-Za-z0-9_])")


@dataclass(frozen=True)
class Use:
    topic: str
    kind: str
    path: str
    line: int
    pattern: bool


@dataclass(frozen=True)
class Entry:
    readings: tuple[str, ...]
    line: int

    @property
    def topic(self) -> str:
        return self.readings[0]


def code_topics(root: Path) -> tuple[list[Use], list[tuple[str, int, str]]]:
    """Topics of modules/ and the places where the topic could not be resolved: (path, line, expression)."""
    uses: list[Use] = []
    unresolved: list[tuple[str, int, str]] = []
    known = global_constants(root)
    for path in python_files(root / "modules"):
        where = rel(root, path)
        try:
            tree = parse_python(path.read_text(encoding="utf-8"), str(path))
        except SyntaxError:
            unresolved.append((where, 0, "file does not parse"))
            continue
        evaluator = Evaluator(tree, known)
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant) \
                    and isinstance(statement.value.value, str) and _TOPIC.match(statement.value.value) \
                    and any(isinstance(t, ast.Name) and t.id.endswith("_TOPIC") for t in statement.targets):
                uses.append(Use(statement.value.value, PUBLISH, where, statement.lineno, False))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            kind = _CALL_KINDS.get(callee_name(node.func) or "")
            if kind is None:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and not (isinstance(first.value, str) and _TOPIC.match(first.value)):
                continue  # `.request("GET", url)` of an HTTP client and the like
            texts = evaluator.values(first, unresolved="*")
            if texts is None:
                unresolved.append((where, node.lineno, ast.unparse(first)))
                continue
            for text in sorted(texts):
                if _TOPIC.match(text):
                    uses.append(Use(text, kind, where, node.lineno, "*" in text))
    return uses, unresolved


def _split_cells(line: str) -> list[str]:
    cells, current, in_code = [], [], False
    previous = ""
    for char in line.strip().strip("|"):
        if char == "`":
            in_code = not in_code
        escaped, previous = previous == "\\", char
        if char == "|" and not in_code and not escaped:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    return cells


def _drop_annotations(text: str) -> str:
    """Remove (…), {…} and […] with their content, nested or not; an unbalanced bracket ends the text."""
    result, depth = [], 0
    for char in text:
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth = max(0, depth - 1)
        elif depth == 0:
            result.append(char)
    return "".join(result)


def _tails(token: str) -> list[str]:
    """`get/list` -> get, list; `a.b/c` -> a.b, a.c (alternatives replace the last segment)."""
    head, *alternatives = token.split("/")
    prefix = head.split(".")[:-1]
    return [head] + [".".join(prefix + [alternative]) for alternative in alternatives]


def doc_topics(text: str, namespaces: set[str], start: int = 0, stop: int | None = None) -> tuple[list[Entry], list[str]]:
    """Topics listed in the tables of a document between 1-based lines [start, stop), and tokens set aside.

    A topic is a dotted token inside backticks whose first segment is a known namespace. A token that starts with a
    dot is a shorthand for the last full topic of the same module in the cell and gets two readings: the sibling
    (the last segment replaced) and the module-relative one (`.queue.clear` -> `<module>.queue.clear`); the reading
    with more than one segment is tried module-relative first. An entry is satisfied by any of its readings.
    """
    entries: list[Entry] = []
    aside: list[str] = []
    relative = False
    header_seen = False
    for number, line in enumerate(text.splitlines(), 1):
        if not line.lstrip().startswith("|"):
            header_seen = False
            continue
        cells = _split_cells(line)
        if not header_seen:
            relative = cells[0].strip("* ") == "Модуль"
            header_seen = True
            continue
        if number < start or (stop is not None and number >= stop) or set("".join(cells)) <= set("-: "):
            continue
        if relative:
            if len(cells) < 2:
                continue
            module, cell = cells[0].strip("` "), cells[1]
        else:
            module, cell = None, cells[0]
        base: str | None = None
        for span in re.findall(r"`([^`]*)`", cell):
            for match in _TOKEN.finditer(_drop_annotations(span)):
                token = match.group(1)
                if token.startswith("."):
                    owner = module or (base.split(".")[0] if base else None)
                    if base is None or owner is None or base.split(".")[0] != owner:
                        continue
                    for tail in _tails(token[1:]):
                        sibling = ".".join(base.split(".")[:-1] + [tail])
                        module_relative = f"{owner}.{tail}"
                        readings = [module_relative, sibling] if "." in tail else [sibling, module_relative]
                        entries.append(Entry(tuple(dict.fromkeys(readings)), number))
                elif "." in token:
                    for full in _tails(token):
                        if full.split(".")[0] in namespaces:
                            entries.append(Entry((full,), number))
                            if module is None or full.split(".")[0] == module:
                                base = full
                        else:
                            aside.append(f"{full} (строка {number})")
    return entries, aside


def architecture_section(text: str, number: str = "5") -> tuple[int, int]:
    """1-based line range [start, stop) of one numbered section ('## 5. …') of ARCHITECTURE.md."""
    start, stop = 1, None
    lines = text.splitlines()
    for index, line in enumerate(lines, 1):
        if re.match(rf"^##\s+{re.escape(number)}\.\s", line):
            start = index
        elif start > 1 and re.match(r"^##\s", line) and index > start:
            stop = index
            break
    return start, stop if stop is not None else len(lines) + 1


def namespaces(root: Path, uses: list[Use]) -> set[str]:
    """First segments a topic can have: the module directories, the topics seen in code and the event namespaces."""
    found = {use.topic.split(".")[0] for use in uses}
    modules = root / "modules"
    if modules.is_dir():
        found |= {path.name for path in modules.iterdir() if path.is_dir() and path.name != "__pycache__"}
    return found | {"series", "system"}


def _satisfied_by(entry: Entry, uses: list[Use]) -> str | None:
    """The reading of an entry that the code carries out, or None."""
    for reading in entry.readings:
        if any(topics_related(reading, use.topic) for use in uses):
            return reading
    return None


def _documented(topic: str, entries: list[Entry]) -> bool:
    return any(topics_related(topic, reading) for entry in entries for reading in entry.readings)


def check_bus_catalog(root: Path, topics_doc: str = TOPICS_DOC, architecture_doc: str = ARCHITECTURE_DOC) -> Outcome:
    outcome = Outcome()
    uses, unresolved = code_topics(root)
    for where, line, expression in unresolved:
        outcome.notes.append(f"{where}:{line} топик не разобран статически: {expression}")
    known = namespaces(root, uses)
    texts = {}
    for name in (topics_doc, architecture_doc):
        path = root / name
        texts[name] = path.read_text(encoding="utf-8") if path.is_file() else None
    catalog: list[Entry] = []
    if texts[topics_doc] is None:
        outcome.violations.append(Violation(topics_doc, 0, "каталог топиков не найден"))
    else:
        catalog, aside = doc_topics(texts[topics_doc], known)
        outcome.notes += [f"{topics_doc}: «{item}» в ячейке топиков не распознан как топик (неизвестное пространство)" for item in aside]
        if not catalog:
            outcome.violations.append(Violation(topics_doc, 0, "в каталоге не разобрано ни одного топика"))
    owned = [u for u in uses if u.kind in (HANDLE, PUBLISH)]
    first_use: dict[str, Use] = {}
    for use in uses:
        first_use.setdefault(use.topic, use)
    if catalog:
        for topic, use in sorted(first_use.items()):
            if not _documented(topic, catalog):
                kinds = "/".join(sorted({u.kind for u in uses if u.topic == topic}))
                outcome.violations.append(Violation(use.path, use.line, f"топик «{topic}» ({kinds}) есть в коде, записи в {topics_doc} нет"))
        for entry in catalog:
            if _satisfied_by(entry, owned) is None:
                outcome.violations.append(Violation(topics_doc, entry.line, f"запись «{entry.topic}» без кода: ни обработчика, ни публикации в modules/"))
    if texts[architecture_doc] is not None:
        start, stop = architecture_section(texts[architecture_doc])
        section, _ = doc_topics(texts[architecture_doc], known, start, stop)
        for entry in section:
            if _satisfied_by(entry, owned) is None:
                outcome.violations.append(Violation(architecture_doc, entry.line, f"топик «{entry.topic}» раздела 5 без кода: ни обработчика, ни публикации в modules/"))
            if catalog and not any(topics_related(r, c) for r in entry.readings for e in catalog for c in e.readings):
                outcome.violations.append(Violation(architecture_doc, entry.line, f"топик «{entry.topic}» раздела 5 не записан в {topics_doc}"))
    return outcome


def main(argv: list[str] | None = None) -> int:
    def add(parser):
        parser.add_argument("--topics-doc", default=TOPICS_DOC)
        parser.add_argument("--architecture-doc", default=ARCHITECTURE_DOC)
        parser.add_argument("--dump", action="store_true", help="print the parsed catalogs before comparing")

    def build(args) -> Outcome:
        if args.dump:
            uses, _ = code_topics(args.root)
            for use in sorted(set(uses), key=lambda u: (u.topic, u.kind, u.path, u.line)):
                print(f"код {use.kind:8} {use.topic}  {use.path}:{use.line}")
            entries, _ = doc_topics((args.root / args.topics_doc).read_text(encoding="utf-8"), namespaces(args.root, uses))
            for entry in entries:
                print(f"док {' | '.join(entry.readings)}  {args.topics_doc}:{entry.line}")
        return check_bus_catalog(args.root, args.topics_doc, args.architecture_doc)

    return run_guard(NAME, build, argv, add)


if __name__ == "__main__":
    raise SystemExit(main())
