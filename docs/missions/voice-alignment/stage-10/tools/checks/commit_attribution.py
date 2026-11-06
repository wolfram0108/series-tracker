"""Guard: no trace of an AI assistant in the commit messages of base..HEAD.

Words are matched case-insensitively with word boundaries: the assistant's names, co-author trailers, "generated
with/by", the Russian "сгенерировано", "ИИ", "AI", "AI-" and the like. "сгенерированные типы" (generated code as a
thing) is not attribution; "сгенерировано" alone and "сгенерировано с помощью ..." are.
"""
from __future__ import annotations

import re
from pathlib import Path

from guard_common import Outcome, Violation, git, run_guard

NAME = "commit_attribution"
DEFAULT_BASE = "fa9231d"
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # CLAUDE.md is the name of the instructions file of the project, not a trace of who wrote the commit.
    ("имя ассистента", re.compile(r"\b(?:claude(?!\.md\b)|anthropic|chatgpt|openai|gpt|copilot|gemini|llm)\b", re.I)),
    ("трейлер соавтора", re.compile(r"\bco-authored-by\b", re.I)),
    ("«generated with/by»", re.compile(r"\bgenerated\s+(?:with|by)\b", re.I)),
    ("«сгенерировано»", re.compile(r"\bсгенерировано\b", re.I)),
    ("«сгенерировано <кем>»", re.compile(r"\bсгенерирован\w*\s+(?:с\s+помощью|при\s+помощи|ии|ai|нейросет\w*|ассистент\w*|claude)\b", re.I)),
    ("«ИИ»", re.compile(r"\bии\b", re.I)),
    ("«AI»", re.compile(r"\bai\b(?!\w)", re.I)),
    ("«нейросеть»", re.compile(r"\b(?:нейросет\w*|искусственн\w+\s+интеллект\w*)", re.I)),
    ("значок робота", re.compile("\U0001F916")),
]


def attribution_hits(message: str) -> list[tuple[int, str, str]]:
    """(line in the message, kind, text) of every trace found."""
    hits = []
    for number, line in enumerate(message.splitlines(), 1):
        for kind, pattern in PATTERNS:
            match = pattern.search(line)
            if match:
                hits.append((number, kind, match.group(0)))
    return hits


def check_commits(root: Path, base: str = DEFAULT_BASE, head: str = "HEAD") -> Outcome:
    outcome = Outcome()
    log = git(root, "log", "--format=%H%x1f%s%x1f%B%x1e", f"{base}..{head}")
    count = 0
    for record in log.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        count += 1
        sha, subject, body = (record.split("\x1f") + ["", ""])[:3]
        for number, kind, text in attribution_hits(body):
            outcome.violations.append(Violation(f"commit {sha[:7]}", number, f"{kind}: «{text}» (тема: {subject[:60]})"))
    outcome.notes.append(f"проверено коммитов: {count}")
    return outcome


def check_message(text: str) -> Outcome:
    """A message that is not committed yet, for the check before `git commit`."""
    outcome = Outcome()
    for number, kind, found in attribution_hits(text):
        outcome.violations.append(Violation("сообщение", number, f"{kind}: «{found}»"))
    return outcome


def main(argv: list[str] | None = None) -> int:
    def add(parser):
        parser.add_argument("--base", default=DEFAULT_BASE, help="commit before the stage")
        parser.add_argument("--head", default="HEAD")
        parser.add_argument("--message-file", type=Path, help="check this message file instead of the commits")

    def build(args) -> Outcome:
        if args.message_file is not None:
            return check_message(args.message_file.read_text(encoding="utf-8"))
        return check_commits(args.root, args.base, args.head)

    return run_guard(NAME, build, argv, add)


if __name__ == "__main__":
    raise SystemExit(main())
