"""Ratchet for the law of interface texts: Russian literals in screens may only go down.

Texts a user sees live in the catalog web/src/i18n/ru.ts; the literals that predate it are counted per file
and must not grow, and a file without literals must stay without.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "web" / "src"
BASELINE = Path(__file__).with_name("ui_literals_baseline.json")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
COMMENT = re.compile(r"^\s*(//|/\*|\*|<!--)")


def literals_of(path: Path) -> int:
    """Lines with Russian text that are not comments."""
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if CYRILLIC.search(line) and not COMMENT.match(line))


def counted() -> dict[str, int]:
    found = {}
    for path in sorted(SOURCES.rglob("*")):
        relative = path.relative_to(SOURCES).as_posix()
        if (path.suffix not in (".vue", ".ts") or relative.startswith("i18n/") or relative.endswith(".test.ts")
                or relative.startswith("api/")):
            continue
        if count := literals_of(path):
            found[relative] = count
    return found


def test_russian_literals_in_screens_do_not_grow():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    grown = {path: (baseline.get(path, 0), count) for path, count in counted().items()
             if count > baseline.get(path, 0)}
    assert not grown, f"тексты экранов — ключами каталога i18n/ru.ts; выросло (было, стало): {grown}"
