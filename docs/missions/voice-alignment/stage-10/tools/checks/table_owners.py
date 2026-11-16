"""Guard: every table made by the alembic migrations is named in the SQL of exactly one module, its owner; the
columns of a shared table belong to one module each.

The tables are read from a database that the real `alembic upgrade head` builds in a temporary directory. The
owner of each table is fixed in OWNERS with the ground for the choice. SQL is found in the string literals and
f-strings of modules/ (docstrings are not SQL): `FROM`, `INTO`, `UPDATE`, `JOIN` (so `DELETE FROM`,
`INSERT OR ... INTO` and `REPLACE INTO` too) followed by a table name. A mention in a module that is not the
owner is a violation and is never excused here: the guard shows the state of the code as it is.

One table is owned by columns (project rule: a table or a column belongs to exactly one module): media_items, see
COLUMN_OWNERS. Its rows belong to the owner of the table, so creating and deleting rows is the owner's; a write of
a column goes to the owner of that column; a foreign write is a violation. Reading rows is permitted only to the
modules in PLAN_READERS and is printed as ДОПУЩЕНО with its ground.
"""
from __future__ import annotations

import ast
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from guard_common import (UNKNOWN, Evaluator, Outcome, Violation, global_constants, is_docstring, parent_of,
                          parse_python, python_files, qualname_of, rel, run_guard)

NAME = "table_owners"
GUARD_PATH = "docs/missions/voice-alignment/stage-10/tools/checks/table_owners.py"

# table -> (owner module, ground). The ground is where the ownership is stated: a module docstring, the migration
# that creates the table, or ARCHITECTURE.md (stage 10), section 3.
OWNERS: dict[str, tuple[str, str]] = {
    "series": ("catalog", "docstring of modules/catalog/module.py: owner of series and series_sources"),
    "series_sources": ("catalog", "migration 0025 (catalog); docstring of modules/catalog/module.py"),
    "series_statuses": ("catalog", "baseline table of the series statuses; modules/catalog/repository.py names it as dead (R-11)"),
    "torrents": ("torrents", "docstring of modules/torrents/repository.py"),
    "torrent_files": ("torrents", "docstring of modules/torrents/module.py: 'torrent_files is ours'"),
    "agent_tasks": ("torrents", "docstring of modules/torrents/module.py: the pipeline machine over agent_tasks"),
    "torrent_progress": ("torrents", "migration 0031: 'owned by torrents alone'; docstring of modules/torrents/repository.py"),
    **dict.fromkeys(("torrent_releases", "torrent_release_files", "torrent_release_versions"),
                    ("torrents", "migration 0069 and the docstring of modules/torrents/module.py (stage 15)")),
    **dict.fromkeys(("unpack_streams", "unpack_folders", "unpack_assignments", "unpack_sources"),
                    ("unpack", "migration 0069 and the docstring of modules/unpack/module.py (stage 15)")),
    "media_items": ("scan", "ARCHITECTURE.md section 3 and docstring of modules/scan/repository.py: the rows belong to scan"),
    "download_tasks": ("downloads", "docstring of modules/downloads/module.py: 'Владение: download_tasks'"),
    "pack_publications": ("downloads", "migration 0038: 'The table belongs to downloads'; docstring of modules/downloads/packing.py"),
    "slicing_tasks": ("slicing", "docstring of modules/slicing/module.py: 'Владение: slicing_tasks, sliced_files'"),
    "sliced_files": ("slicing", "docstring of modules/slicing/module.py"),
    "renaming_tasks": ("renaming", "docstring of modules/renaming/repository.py"),
    "relocation_tasks": ("library", "docstring of modules/library/repository.py"),
    "scan_tasks": ("scan", "docstring of modules/scan/repository.py"),
    "scan_references": ("scan", "migration 0028 (scan); docstring of modules/scan/repository.py"),
    "parser_profiles": ("rules", "docstring of modules/rules/repository.py"),
    "parser_rules": ("rules", "docstring of modules/rules/repository.py"),
    "parser_rule_conditions": ("rules", "docstring of modules/rules/repository.py"),
    "auth": ("trackerauth", "docstring of modules/trackerauth/repository.py: 'свои таблицы — tracker_sessions и auth'"),
    "tracker_sessions": ("trackerauth", "migration 0002: 'Владелец таблицы — модуль trackerauth'"),
    "admin_user": ("auth", "migration 0006: 'Владелец таблицы — модуль auth'"),
    "settings": ("settings", "docstring of modules/settings/module.py: owner of the table settings"),
    "saved_paths": ("settings", "migration 0004: 'Владелец таблицы — модуль settings'"),
    "trackers": ("sources", "docstring of modules/sources/module.py: sources.trackers.list, owner of the table trackers"),
    "series_tmdb_mappings": ("metadata", "docstring of modules/metadata/module.py: owner of series_tmdb_mappings"),
    "voice_dubs": ("voices", "migration 0016: 'Owned by the voices module'"),
    "voice_files": ("voices", "migration 0029 (voices); docstring of modules/voices/repository.py"),
    "voice_cleanup": ("voices", "migration 0019 (voice_dubs_intents): the journal of files and jobs of voices"),
    "voice_epoch": ("voices", "migration 0022: counts the starts of the voices module"),
    "release_tables": ("sources", "migration 0026 (sources); ARCHITECTURE.md section 3"),
    "release_rows": ("sources", "migration 0026 (sources); ARCHITECTURE.md section 3"),
    "composition_state": ("composition", "migration 0027 (composition); docstring of modules/composition/repository.py"),
    "composition_sources": ("composition", "migration 0027 (composition)"),
    "composition_rows": ("composition", "migration 0027 (composition)"),
    "voice_orders": ("composition", "migration 0036 (composition): the order of the voice-overs of a season"),
    "unknown_voices": ("composition", "migration 0027 (composition)"),
    "output_settings": ("composition", "migration 0027 (composition)"),
}

# table -> {column: (owner module, ground)}: columns whose owner is not the owner of the table.
COLUMN_OWNERS: dict[str, dict[str, tuple[str, str]]] = {
    "media_items": {
        "status": ("downloads", "docstring of modules/downloads/repository.py: media_items.status / final_filename"),
        "rename_to": ("downloads", "docstring of modules/downloads/repository.py: media_items.status / final_filename / rename_to"),
        "final_filename": ("downloads", "docstring of modules/downloads/repository.py: media_items.status / final_filename"),
        "file_rev": ("downloads", "migration 0039: the revision of the file of a row, counted by downloads"),
        "slicing_status": ("slicing", "docstring of modules/slicing/repository.py: media_items.slicing_status / chapters / chapters_filtered"),
        "chapters": ("slicing", "docstring of modules/slicing/repository.py: media_items.slicing_status / chapters / chapters_filtered"),
        "chapters_filtered": ("slicing", "docstring of modules/slicing/repository.py: media_items.slicing_status / chapters / chapters_filtered"),
    },
}

# (table, module) -> ground: a module that is not the owner but may read the rows of the table.
PLAN_READERS: dict[tuple[str, str], str] = {
    ("media_items", "downloads"): "docstring of modules/downloads/repository.py: the executor of the plan reads the rows of the plan, which are scan's",
    ("media_items", "slicing"): "docstring of modules/slicing/repository.py: the other columns are read; slicing is outside the architecture of stage 10 (D-34: the code is not touched)",
}

# (file, function, ground): a write whose columns are not known statically, by the owner of the table.
DYNAMIC_COLUMNS_ALLOWED: list[tuple[str, str, str]] = [
    ("modules/scan/repository.py", "ScanRepository.save_outcome",
     "the columns are the keys of the plan row fields built by scan; the docstring says the columns of other modules are never written (not provable statically)"),
]

# Names that follow FROM/INTO/UPDATE/JOIN but are not tables of the application.
NOT_APPLICATION_TABLES = frozenset({"sqlite_master", "sqlite_schema", "sqlite_sequence", "sqlite_temp_master"})

# (file, function, ground): places where the table name is not known statically.
DYNAMIC_ALLOWED: list[tuple[str, str, str]] = [
    ("modules/gateway/api_settings.py", "build_router.database_table.read_table",
     "admin DB tab: deliberate bypass of R-7, a debug tool of the owner (contracts/revision.md, R-22); the name is checked against the live table list"),
    ("modules/gateway/api_settings.py", "build_router.database_clear_table.clear",
     "admin DB tab: deliberate bypass of R-7, a debug tool of the owner (contracts/revision.md, R-22); the name is checked against the live table list"),
]

_NAME = r'["`\[]?([A-Za-z_\x00][A-Za-z0-9_\x00]*)'
_UPPER = re.compile(r"(?<![A-Za-z0-9_])(?<!DO )(?:FROM|INTO|JOIN|UPDATE(?:\s+OR\s+[A-Z]+)?)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?" + _NAME)
_ANY_CASE = re.compile(r"(?<![A-Za-z0-9_])(?<!do )(?:from|into|join|update(?:\s+or\s+[a-z]+)?)\s+" + _NAME, re.I)
_LOWERCASE_STATEMENT = re.compile(
    r"^\s*(?:select\b.*\bfrom|insert\b.*\binto|update\s+\w+\s+set|delete\s+from|replace\s+into)\b", re.I | re.S)
_KEYWORDS = frozenset({"SET", "SELECT", "VALUES", "WHERE", "DEFAULT"})


@dataclass(frozen=True)
class Mention:
    table: str
    line: int
    function: str
    dynamic: bool
    access: str = "read"  # read, insert, update or delete
    columns: tuple[str, ...] | None = None  # columns written; None when they are not known statically


def _access_of(text: str, match: re.Match) -> str:
    word = match.group(0).split()[0].upper()
    if word == "FROM":
        return "delete" if re.search(r"DELETE\s*$", text[:match.start()], re.I) else "read"
    return {"JOIN": "read", "INTO": "insert", "UPDATE": "update"}[word]


def _split_top(text: str) -> list[str]:
    """Split at commas outside parentheses and quotes."""
    parts, current, depth, quote = [], [], 0, ""
    for char in text:
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def _written_columns(text: str, match: re.Match, access: str) -> tuple[str, ...] | None:
    tail = text[match.end():].lstrip("\"`]")
    if access == "insert":
        listed = re.match(r"\s*\(([^)]*)\)", tail)
        return tuple(c.strip().strip('"`[]') for c in listed.group(1).split(",")) if listed else None
    if access == "update":
        opening = re.match(r"\s*(?:AS\s+\w+\s+)?SET\s+", tail, re.I)
        if not opening:
            return None
        names = []
        for part in _split_top(_set_clause(tail[opening.end():])):
            one = re.match(r'\s*["`\[]?(\w+)["`\]]?\s*=', part)
            if not one:
                return None
            names.append(one.group(1))
        return tuple(names)
    return None


def _set_clause(text: str) -> str:
    """The assignments of a SET clause: up to WHERE, RETURNING or FROM outside parentheses and quotes."""
    depth, quote = 0, ""
    for index, char in enumerate(text):
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and char.isspace() and re.match(r"\s+(?:WHERE|RETURNING|FROM)\b", text[index:], re.I):
            return text[:index]
    return text


def _mentions_in_text(text: str) -> list[tuple[str, str, tuple[str, ...] | None]]:
    """(table, access, written columns) for every table named in one SQL text."""
    found = [m for m in _UPPER.finditer(text)]
    if _LOWERCASE_STATEMENT.match(text):
        found += [m for m in _ANY_CASE.finditer(text) if m.start() not in {f.start() for f in found}]
    result = []
    for match in found:
        name = match.group(1)
        if text[match.end():match.end() + 1] == "(" or name.upper() in _KEYWORDS:
            continue  # a table function such as json_each(...), or a keyword
        access = _access_of(text, match)
        result.append((name, access, _written_columns(text, match, access)))
    return result


def _line_of(source_lines: list[str], node: ast.AST, name: str) -> int:
    """The source line that carries `FROM name`; the statement start when the name is built dynamically."""
    first = getattr(node, "lineno", 1)
    last = getattr(node, "end_lineno", first)
    pattern = re.compile(r"(?:FROM|INTO|JOIN|UPDATE)\s+[\"`\[]?" + re.escape(name.replace(UNKNOWN, "")), re.I)
    for number in range(first, last + 1):
        if pattern.search(source_lines[number - 1]):
            return number
    return first


def sql_mentions(tree: ast.Module, source: str, known: dict[str, set[str]] | None = None) -> list[Mention]:
    """Every table named in the SQL strings of one file."""
    evaluator = Evaluator(tree, known)
    lines = source.splitlines()
    mentions: list[Mention] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            if isinstance(parent_of(node), ast.JoinedStr):
                continue
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if is_docstring(node) or isinstance(parent_of(node), (ast.JoinedStr, ast.FormattedValue)):
                continue
        else:
            continue
        variants = evaluator.values(node, unresolved=UNKNOWN) or set()
        seen: set[tuple] = set()
        for text in variants:
            for name, access, columns in _mentions_in_text(text):
                if (name, access, columns) in seen:
                    continue
                seen.add((name, access, columns))
                mentions.append(Mention(name, _line_of(lines, node, name), qualname_of(node), UNKNOWN in name,
                                        access, columns))
    return mentions


def migrated_tables(root: Path, python: str = sys.executable) -> set[str]:
    """Tables of the schema that the real migrations build; alembic and sqlalchemy must be importable."""
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "guard.db"
        environment = {**os.environ, "ST_DB_URL": f"sqlite:///{database}"}
        done = subprocess.run([python, "-m", "alembic", "upgrade", "head"], cwd=root, env=environment,
                              capture_output=True, text=True, timeout=120)
        if done.returncode != 0:
            raise RuntimeError(f"alembic upgrade head failed: {done.stderr.strip()[-400:]}")
        with sqlite3.connect(database) as connection:
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return {name for name in names if not name.startswith(("sqlite_", "alembic_"))}


def _creation_site(root: Path, table: str) -> tuple[str, int]:
    pattern = re.compile(rf"CREATE TABLE (?:IF NOT EXISTS )?{re.escape(table)}\b")
    for path in sorted((root / "alembic" / "versions").glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                return rel(root, path), number
    return "alembic/versions", 0


def _check_columns(outcome: Outcome, table: str, owner: str, module: str, where: str, mention: Mention,
                   columns: dict[str, tuple[str, str]], readers: dict[tuple[str, str], str],
                   dynamic_columns: list[tuple[str, str, str]]) -> None:
    """The rules of a table owned by columns, for one mention of it in a module."""
    def violate(reason: str) -> None:
        outcome.violations.append(Violation(where, mention.line, reason))

    def permit(reason: str) -> None:
        outcome.allowed.append(Violation(where, mention.line, reason))

    owner_of = lambda column: columns.get(column, (owner, ""))[0]  # noqa: E731
    if mention.access == "read":
        if module == owner:
            return
        if (table, module) in readers:
            permit(f"чтение строк «{table}» модулем {module}: {readers[(table, module)]}")
        else:
            violate(f"таблица «{table}» принадлежит модулю {owner}; чтение её строк модулем {module} не допущено")
    elif mention.access == "delete":
        if module != owner:
            violate(f"таблица «{table}»: строки удаляет модуль {owner}, а не {module}")
    elif mention.access == "insert":
        if module != owner:
            violate(f"таблица «{table}»: строки создаёт модуль {owner}, а не {module}")
        elif mention.columns and any(owner_of(c) != module for c in mention.columns):
            foreign = ", ".join(c for c in mention.columns if owner_of(c) != module)
            permit(f"создание строки «{table}» модулем {module} задаёт начальные значения колонок других модулей ({foreign}): "
                   "строка принадлежит владельцу таблицы, дальше колонки пишут их владельцы")
    elif mention.columns is None:
        ground = next((g for f, fn, g in dynamic_columns if f == where and fn == mention.function), None)
        if module == owner and ground is not None:
            permit(f"запись «{table}» с колонками, не заданными статически, в {mention.function}: {ground}")
        else:
            violate(f"запись «{table}» в {mention.function or 'модуле'}: колонки не определены статически")
    else:
        for column in mention.columns:
            if owner_of(column) != module:
                violate(f"запись чужой колонки «{table}.{column}»: владелец {owner_of(column)}, пишет модуль {module}")


def check_table_owners(root: Path, tables: set[str], owners: dict[str, tuple[str, str]] | None = None,
                       dynamic_allowed: list[tuple[str, str, str]] | None = None,
                       column_owners: dict[str, dict[str, tuple[str, str]]] | None = None,
                       plan_readers: dict[tuple[str, str], str] | None = None,
                       dynamic_columns: list[tuple[str, str, str]] | None = None) -> Outcome:
    owners = OWNERS if owners is None else owners
    dynamic_allowed = DYNAMIC_ALLOWED if dynamic_allowed is None else dynamic_allowed
    column_owners = COLUMN_OWNERS if column_owners is None else column_owners
    plan_readers = PLAN_READERS if plan_readers is None else plan_readers
    dynamic_columns = DYNAMIC_COLUMNS_ALLOWED if dynamic_columns is None else dynamic_columns
    outcome = Outcome()
    known = global_constants(root)
    mentioned_by: dict[str, dict[str, list[tuple[str, Mention]]]] = {}
    for path in python_files(root / "modules"):
        parts = path.relative_to(root).parts
        if len(parts) < 3:
            continue
        module = parts[1]
        source = path.read_text(encoding="utf-8")
        try:
            tree = parse_python(source, str(path))
        except SyntaxError as error:
            outcome.violations.append(Violation(rel(root, path), error.lineno or 0, f"файл не разобран: {error.msg}"))
            continue
        for mention in sql_mentions(tree, source, known):
            where = rel(root, path)
            if mention.dynamic:
                ground = next((g for f, fn, g in dynamic_allowed if f == where and fn == mention.function), None)
                note = f"динамическое имя таблицы «{mention.table.replace(UNKNOWN, '{…}')}»"
                if ground is None:
                    outcome.violations.append(Violation(where, mention.line, f"{note} в {mention.function or 'модуле'}: имя не определено статически"))
                else:
                    outcome.allowed.append(Violation(where, mention.line, f"{note} в {mention.function}: {ground}"))
                continue
            if mention.table in NOT_APPLICATION_TABLES:
                continue
            mentioned_by.setdefault(mention.table, {}).setdefault(module, []).append((where, mention))
    for table in sorted(set(mentioned_by) - tables):
        for module, places in mentioned_by[table].items():
            for where, mention in places:
                outcome.violations.append(Violation(where, mention.line, f"таблица «{table}» не создаётся миграциями (модуль {module})"))
    for table in sorted(tables):
        if table not in owners:
            where, line = _creation_site(root, table)
            outcome.violations.append(Violation(where, line, f"таблица «{table}» создана миграциями, владелец не назначен в сторожe"))
            continue
        owner, _ground = owners[table]
        by_module = mentioned_by.get(table, {})
        if owner not in by_module:
            outcome.violations.append(Violation(f"modules/{owner}", 0, f"таблица «{table}»: в SQL модуля-владельца {owner} её нет"))
        for module, places in sorted(by_module.items()):
            for where, mention in places:
                if table in column_owners:
                    _check_columns(outcome, table, owner, module, where, mention, column_owners[table], plan_readers,
                                   dynamic_columns)
                elif module != owner:
                    outcome.violations.append(Violation(where, mention.line, f"таблица «{table}» принадлежит модулю {owner}; SQL модуля {module} её называет"))
    for table in sorted(set(owners) - tables):
        outcome.violations.append(Violation(GUARD_PATH, 0, f"владелец назначен таблице «{table}», которой нет в миграциях"))
    for table in sorted(set(column_owners) - set(owners)):
        outcome.violations.append(Violation(GUARD_PATH, 0, f"колонки таблицы «{table}» заданы, а таблицы нет в словаре владельцев"))
    return outcome


def main(argv: list[str] | None = None) -> int:
    return run_guard(NAME, lambda args: check_table_owners(args.root, migrated_tables(args.root)), argv)


if __name__ == "__main__":
    raise SystemExit(main())
