"""Guard: every table made by the alembic migrations is named in the SQL of exactly one module, its owner.

The tables are read from a database that the real `alembic upgrade head` builds in a temporary directory. The
owner of each table is fixed in OWNERS with the ground for the choice. SQL is found in the string literals and
f-strings of modules/ (docstrings are not SQL): `FROM`, `INTO`, `UPDATE`, `JOIN` (so `DELETE FROM`,
`INSERT OR ... INTO` and `REPLACE INTO` too) followed by a table name. A mention in a module that is not the
owner is a violation and is never excused here: the guard shows the state of the code as it is.
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
    "media_items": ("scan", "ARCHITECTURE.md section 3 and docstring of modules/scan/repository.py: the rows belong to scan"),
    "download_tasks": ("downloads", "docstring of modules/downloads/module.py: 'Владение: download_tasks'"),
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
    "unknown_voices": ("composition", "migration 0027 (composition)"),
    "output_settings": ("composition", "migration 0027 (composition)"),
}

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


def _mentions_in_text(text: str) -> list[str]:
    found = [m for m in _UPPER.finditer(text)]
    if _LOWERCASE_STATEMENT.match(text):
        found += [m for m in _ANY_CASE.finditer(text) if m.start() not in {f.start() for f in found}]
    names = []
    for match in found:
        name = match.group(1)
        if text[match.end():match.end() + 1] == "(" or name.upper() in _KEYWORDS:
            continue  # a table function such as json_each(...), or a keyword
        names.append(name)
    return names


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
        seen: set[str] = set()
        for text in variants:
            for name in _mentions_in_text(text):
                if name in seen:
                    continue
                seen.add(name)
                mentions.append(Mention(name, _line_of(lines, node, name), qualname_of(node), UNKNOWN in name))
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


def check_table_owners(root: Path, tables: set[str], owners: dict[str, tuple[str, str]] | None = None,
                       dynamic_allowed: list[tuple[str, str, str]] | None = None) -> Outcome:
    owners = OWNERS if owners is None else owners
    dynamic_allowed = DYNAMIC_ALLOWED if dynamic_allowed is None else dynamic_allowed
    outcome = Outcome()
    known = global_constants(root)
    mentioned_by: dict[str, dict[str, list[tuple[str, int]]]] = {}
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
            mentioned_by.setdefault(mention.table, {}).setdefault(module, []).append((where, mention.line))
    for table in sorted(set(mentioned_by) - tables):
        for module, places in mentioned_by[table].items():
            for where, line in places:
                outcome.violations.append(Violation(where, line, f"таблица «{table}» не создаётся миграциями (модуль {module})"))
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
            if module == owner:
                continue
            for where, line in places:
                outcome.violations.append(Violation(where, line, f"таблица «{table}» принадлежит модулю {owner}; SQL модуля {module} её называет"))
    for table in sorted(set(owners) - tables):
        outcome.violations.append(Violation(GUARD_PATH, 0, f"владелец назначен таблице «{table}», которой нет в миграциях"))
    return outcome


def main(argv: list[str] | None = None) -> int:
    return run_guard(NAME, lambda args: check_table_owners(args.root, migrated_tables(args.root)), argv)


if __name__ == "__main__":
    raise SystemExit(main())
