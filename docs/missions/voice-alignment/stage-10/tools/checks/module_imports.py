"""Guard: a module under modules/<m>/ imports only core.*, itself and third-party libraries.

An import of modules.<other> (absolute, or relative with a path that leaves the package of the module), of a
project top-level file such as run, or a dynamic import of such a name is a violation. run.py is not checked.
"""
from __future__ import annotations

import ast
from pathlib import Path

from guard_common import Outcome, Violation, parse_python, python_files, rel, run_guard

NAME = "module_imports"
# Top-level project names a module must not reach; `core` is the allowed shared layer and `modules` is checked apart.
FOREIGN_TOP_LEVEL = frozenset({"run", "tests", "conftest", "scripts", "legacy_frontend", "contracts"})


def _target_package(package: tuple[str, ...], level: int, module: str | None) -> tuple[str, ...] | None:
    """Absolute dotted path of a `from` import; None when a relative import climbs above the top package."""
    if level == 0:
        return tuple(module.split(".")) if module else ()
    if level - 1 > len(package) - 1:
        return None
    base = package[: len(package) - (level - 1)]
    return base + (tuple(module.split(".")) if module else ())


def foreign_imports(tree: ast.Module, package: tuple[str, ...], own: str) -> list[tuple[int, str]]:
    """(line, text) of every import in one file that leaves the module `own`; package is the file's package path."""
    found: list[tuple[int, str]] = []

    def judge(line: int, path: tuple[str, ...] | None, text: str, names: list[str] | None = None) -> None:
        if path is None:
            found.append((line, f"{text}: относительный импорт выходит за пределы пакета"))
        elif path[:1] == ("modules",):
            if len(path) > 1:
                if path[1] != own:
                    found.append((line, f"{text}: чужой модуль {path[1]}"))
            elif names is None:
                found.append((line, f"{text}: пакет modules целиком"))
            else:
                for name in names:
                    if name != own:
                        found.append((line, f"{text}: чужой модуль {name}"))
        elif path[:1] and path[0] in FOREIGN_TOP_LEVEL:
            found.append((line, f"{text}: верхний уровень проекта {path[0]}"))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                judge(node.lineno, tuple(alias.name.split(".")), f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            path = _target_package(package, node.level, node.module)
            dots = "." * node.level
            text = f"from {dots}{node.module or ''} import {', '.join(a.name for a in node.names)}"
            judge(node.lineno, path, text, [a.name for a in node.names])
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"import_module", "__import__"} and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                judge(node.lineno, tuple(node.args[0].value.split(".")), f"{name}({node.args[0].value!r})")
    return found


def check_module_imports(root: Path) -> Outcome:
    outcome = Outcome()
    for path in python_files(root / "modules"):
        parts = path.relative_to(root).parts  # ('modules', '<m>', ..., 'file.py')
        if len(parts) < 3:
            continue
        own = parts[1]
        package = parts[:-1]
        try:
            tree = parse_python(path.read_text(encoding="utf-8"), str(path))
        except SyntaxError as error:
            outcome.violations.append(Violation(rel(root, path), error.lineno or 0, f"файл не разобран: {error.msg}"))
            continue
        for line, text in foreign_imports(tree, package, own):
            outcome.violations.append(Violation(rel(root, path), line, f"модуль {own} импортирует вне core — {text}"))
    return outcome


def main(argv: list[str] | None = None) -> int:
    return run_guard(NAME, lambda args: check_module_imports(args.root), argv)


if __name__ == "__main__":
    raise SystemExit(main())
