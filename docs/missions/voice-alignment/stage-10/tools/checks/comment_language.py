"""Guard: comments in the lines added or changed since the base commit have no Cyrillic.

Python: `#` comments (found by the tokenizer, so a `#` inside a string is not one) of added lines in `*.py`.
Web: `//`, `/* */` and, in .vue templates, `<!-- -->` comments of added lines in .ts/.js/.css/.vue under web/.
The generated web/src/api/schema.d.ts is skipped (its comments are the API descriptions). Docstrings, strings and
exception messages are not comments. The top-level docstring of a module.py is the contract
of the module and is Russian by the convention of the project; other docstrings are checked only with --docstrings.
"""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

from guard_common import (CYRILLIC, GENERATED_FILES, WORKTREE, Outcome, Violation, added_lines, file_text, parse_python, run_guard,
                          web_comments)

NAME = "comment_language"
DEFAULT_BASE = "fa9231d"
PYTHON_SPECS = ["*.py"]
WEB_SPECS = [f"web/*.{suffix}" for suffix in ("ts", "tsx", "js", "mjs", "css", "vue")]


def python_comments(text: str) -> list[tuple[int, str]]:
    return [(token.start[0], token.string) for token in tokenize.generate_tokens(io.StringIO(text).readline)
            if token.type == tokenize.COMMENT]


def python_docstrings(text: str, filename: str) -> list[tuple[int, int, str]]:
    """(first line, last line, text) of docstrings, without the top-level docstring of a file named module.py."""
    tree = parse_python(text, filename)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) \
                    and isinstance(node.body[0].value.value, str):
                if isinstance(node, ast.Module) and filename.endswith("module.py"):
                    continue
                expr = node.body[0]
                found.append((expr.lineno, expr.end_lineno or expr.lineno, expr.value.value))
    return found


def check_comment_language(root: Path, base: str = DEFAULT_BASE, head: str = "HEAD", docstrings: bool = False) -> Outcome:
    outcome = Outcome()
    for path, lines in sorted(added_lines(root, base, head, PYTHON_SPECS).items()):
        text = file_text(root, head, path)
        try:
            comments = python_comments(text)
            for line, comment in comments:
                if line in lines and CYRILLIC.search(comment):
                    outcome.violations.append(Violation(path, line, f"комментарий с кириллицей: {comment.strip()[:80]}"))
            if docstrings:
                for first, last, doc in python_docstrings(text, path):
                    hit = sorted(n for n in range(first, last + 1) if n in lines)
                    if hit and CYRILLIC.search(doc):
                        outcome.violations.append(Violation(path, hit[0], "докстринг с кириллицей (режим --docstrings)"))
        except (tokenize.TokenError, SyntaxError, IndentationError) as error:
            outcome.violations.append(Violation(path, 0, f"файл не разобран: {error}"))
    for path, lines in sorted(added_lines(root, base, head, WEB_SPECS).items()):
        if path in GENERATED_FILES:
            continue
        for comment in web_comments(file_text(root, head, path), path):
            hit = sorted(n for n in range(comment.first_line, comment.last_line + 1) if n in lines)
            if hit and CYRILLIC.search(comment.text):
                shown = " ".join(comment.text.split())[:80]
                outcome.violations.append(Violation(path, hit[0], f"комментарий с кириллицей: {shown}"))
    return outcome


def main(argv: list[str] | None = None) -> int:
    def add(parser):
        parser.add_argument("--base", default=DEFAULT_BASE, help="commit before the stage")
        parser.add_argument("--head", default="HEAD", help=f"end of the range; {WORKTREE} adds the uncommitted work")
        parser.add_argument("--docstrings", action="store_true", help="check docstrings too")

    return run_guard(NAME, lambda a: check_comment_language(a.root, a.base, a.head, a.docstrings), argv, add)


if __name__ == "__main__":
    raise SystemExit(main())
