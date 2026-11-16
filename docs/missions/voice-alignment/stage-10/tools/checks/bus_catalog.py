"""Guard: the bus topics of the code and the catalogs of the documents agree in both directions, and no topic is dead.

Code side (modules/, core/, run.py): a topic is HANDLED by `self.handle("…")`, PUBLISHED by `publish_event("…")`, a
module-level `*_TOPIC = "…"` constant (the topic given to a publish callback) or `Envelope(topic="…", kind="event")`,
COMMANDED by `send_command("…")` and REQUESTED by `request("…")` (or any callee named `*_request`, such as an injected `_request`). Names are resolved statically, an f-string with an
unknown part becomes a pattern with `*`, and what cannot be resolved is printed as a note, not as a violation.

Documents: contracts/bus_topics.md (both directions) and section 5 of ARCHITECTURE.md of the stage (every row must
have code and a row in bus_topics.md). A topic in a document is a dotted token inside backticks, taken from the
second cell of a table headed `Модуль` and from the first cell of any other table; its first segment must be a known
namespace (a module directory or the first segment of a topic of the code), other dotted tokens are prose and are
listed as notes. Annotations in braces, parentheses and brackets are dropped. `a.b/c` stands for `a.b` and `a.c`;
`.c` after `a.b` of the same cell stands for the sibling `a.c` (one reading, nothing else); `*` and `#` make a
pattern with the rules of the bus.

Dead topics: a handler (`self.handle`) that nobody publishes, commands or requests, and a key of the SSE_MAP of the
gateway that nobody publishes, are violations unless the topic stands in UNSENT_ALLOWED with its ground. The
gateway is a sender like any module (`gw.request`, `gw.send_command` for a topic called over HTTP); tests are not.
Each such violation tells in how many test files the topic is named.
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
GUARD_PATH = "docs/missions/voice-alignment/stage-10/tools/checks/bus_catalog.py"

HANDLE, PUBLISH, COMMAND, REQUEST = "handle", "publish", "command", "request"
OWNED = (HANDLE, PUBLISH, COMMAND)  # the code carries out the topic: somebody handles it or puts it on the bus
SENT = (PUBLISH, COMMAND, REQUEST)
_CALL_KINDS = {"handle": HANDLE, "publish_event": PUBLISH, "send_command": COMMAND, "request": REQUEST}
_ENVELOPE_KINDS = {"event": PUBLISH, "command": COMMAND, "query": REQUEST}

# (topic or pattern, ground): a handler or an SSE_MAP key that nobody in the code sends and that is kept on purpose.
UNSENT_ALLOWED: list[tuple[str, str]] = [
    ("slicing.files.drop_for_source", "slicing вне архитектуры этапа, Д-34"),
    ("unpack.table.get", "его спрашивает сайт трекера через ReadContext.ask (modules/sources/sites/trackers.py), не self.request"),
    ("torrents.info.get", "публичный контракт управления торрентами, записан в докстринге модуля torrents и в contracts/bus_topics.md"),
    ("torrents.pause", "публичный контракт управления торрентами, записан в докстринге модуля torrents и в contracts/bus_topics.md"),
    ("torrents.resume", "публичный контракт управления торрентами, записан в докстринге модуля torrents и в contracts/bus_topics.md"),
    ("torrents.recheck", "публичный контракт управления торрентами, записан в докстринге модуля torrents и в contracts/bus_topics.md"),
    ("torrents.delete", "публичный контракт управления торрентами, записан в докстринге модуля torrents и в contracts/bus_topics.md"),
]
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


def call_kind(name: str) -> str | None:
    """What a call does with its first argument, by the name of the callee; `_request` of an injected callable too."""
    if name in _CALL_KINDS:
        return _CALL_KINDS[name]
    return REQUEST if name.endswith("_request") else None


def code_files(root: Path) -> list[Path]:
    files = [*python_files(root / "modules"), *python_files(root / "core")]
    return files + ([root / "run.py"] if (root / "run.py").is_file() else [])


def code_topics(root: Path) -> tuple[list[Use], list[tuple[str, int, str]]]:
    """Topics of modules/, core/ and run.py, and the places where the topic could not be resolved."""
    uses: list[Use] = []
    unresolved: list[tuple[str, int, str]] = []
    known = global_constants(root)
    for path in code_files(root):
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
            if not isinstance(node, ast.Call):
                continue
            if callee_name(node.func) == "Envelope":
                topic = next((k.value for k in node.keywords if k.arg == "topic"), node.args[0] if node.args else None)
                kind = next((k.value.value for k in node.keywords if k.arg == "kind" and isinstance(k.value, ast.Constant)), None)
                found = evaluator.values(topic) if topic is not None and kind in _ENVELOPE_KINDS else None
                uses += [Use(t, _ENVELOPE_KINDS[kind], where, node.lineno, False) for t in sorted(found or ()) if _TOPIC.match(t)]
                continue
            if not node.args:
                continue
            kind = call_kind(callee_name(node.func) or "")
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


def sse_keys(root: Path) -> list[tuple[str, str, int]]:
    """(topic, path, line) of every key of a module-level SSE_MAP dict in modules/."""
    keys = []
    for path in python_files(root / "modules"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for statement in tree.body:
            targets = statement.targets if isinstance(statement, ast.Assign) else [getattr(statement, "target", None)]
            value = getattr(statement, "value", None)
            if isinstance(value, ast.Dict) and any(isinstance(t, ast.Name) and t.id == "SSE_MAP" for t in targets):
                keys += [(k.value, rel(root, path), k.lineno) for k in value.keys
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    return keys


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
    dot is a shorthand for the last full topic of the same module in the cell and has one reading: the sibling (the
    last segment of that topic replaced by the tail). An entry keeps a tuple of readings for the callers' sake.
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
                        entries.append(Entry((".".join(base.split(".")[:-1] + [tail]),), number))
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


def check_bus_catalog(root: Path, topics_doc: str = TOPICS_DOC, architecture_doc: str = ARCHITECTURE_DOC,
                      unsent_allowed: list[tuple[str, str]] | None = None) -> Outcome:
    # Repository permits come only from main(): a tree judged in isolation must not inherit them.
    unsent_allowed = [] if unsent_allowed is None else unsent_allowed
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
    owned = [u for u in uses if u.kind in OWNED]
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
    check_dead_topics(root, uses, outcome, unsent_allowed)
    return outcome


def tests_naming(root: Path, topic: str) -> int:
    """In how many files of tests/ the topic stands as a quoted string."""
    quoted = re.compile(r"""["']""" + re.escape(topic) + r"""["']""")
    tests = root / "tests"
    return sum(1 for path in python_files(tests) if quoted.search(path.read_text(encoding="utf-8", errors="replace")))


def check_dead_topics(root: Path, uses: list[Use], outcome: Outcome, unsent_allowed: list[tuple[str, str]]) -> None:
    senders = [u for u in uses if u.kind in SENT]
    used_permits: set[str] = set()

    def judge(topic: str, where: str, line: int, what: str) -> None:
        if any(topics_related(topic, s.topic) for s in senders):
            return
        permit = next((e for e in unsent_allowed if topics_related(topic, e[0])), None)
        if permit is not None:
            used_permits.add(permit[0])
            outcome.allowed.append(Violation(where, line, f"{what} «{topic}» без отправителя: {permit[1]}"))
        else:
            outcome.violations.append(Violation(where, line, (
                f"{what} «{topic}» без отправителя: в modules/, core/ и run.py его никто не публикует, не шлёт и не "
                f"запрашивает (в тестах назван в {tests_naming(root, topic)} файлах)")))

    first_handler: dict[str, Use] = {}
    for use in uses:
        if use.kind == HANDLE:
            first_handler.setdefault(use.topic, use)
    keys = sse_keys(root)
    outcome.notes.append(f"тупики: проверено обработчиков {len(first_handler)}, ключей SSE_MAP {len(keys)}")
    for topic, use in sorted(first_handler.items()):
        judge(topic, use.path, use.line, "обработчик")
    publishers = [u for u in uses if u.kind == PUBLISH]
    for topic, where, line in keys:
        if not any(topics_related(topic, p.topic) for p in publishers):
            permit = next((e for e in unsent_allowed if topics_related(topic, e[0])), None)
            if permit is not None:
                used_permits.add(permit[0])
                outcome.allowed.append(Violation(where, line, f"ключ SSE_MAP «{topic}» без издателя: {permit[1]}"))
            else:
                outcome.violations.append(Violation(where, line, f"ключ SSE_MAP «{topic}» без издателя: событие никто не публикует"))
    for topic, _ground in unsent_allowed:
        if topic not in used_permits:
            outcome.violations.append(Violation(GUARD_PATH, 0, f"допуск «{topic}» не используется: в сторожe устаревшая запись"))


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
        return check_bus_catalog(args.root, args.topics_doc, args.architecture_doc, UNSENT_ALLOWED)

    return run_guard(NAME, build, argv, add)


if __name__ == "__main__":
    raise SystemExit(main())
