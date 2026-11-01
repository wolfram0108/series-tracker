"""Guard: the added or changed lines of web/src (.vue, .css, .ts) keep the layout intrinsic (law-ui).

Violations, per added line since the base commit:
  - `@media` or `@container` conditioned on width (min-width, max-width, width, ranges, inline-size), and
    `matchMedia` with a width;
  - a size in px outside `clamp()` in a layout property (width/height families, margin, padding, gap, offsets,
    flex-basis, grid templates and the like) or in a custom property named like a size;
  - `setInterval`, `setTimeout` and polling helpers (same places and tolerances as no_wait_no_poll.py).

Tolerances (not violations): `0px`; any px inside `clamp()`; border and outline widths up to 1px; typography
(font-*, line-height, letter-spacing), border-radius, shadows, filters, transforms and transitions are not layout
properties and are not examined; sizes in rem, em, ch, %, fr, vw, vh and values from var(--…) are relative.
"""
from __future__ import annotations

import re
from pathlib import Path

from guard_common import GENERATED_FILES, WORKTREE, Outcome, Violation, added_lines, blank_comments, file_text, run_guard, web_comments
from no_wait_no_poll import ALLOWED_TIMERS, find_timers, is_test_file, timer_ground

NAME = "ui_styles"
DEFAULT_BASE = "fa9231d"
SPECS = ["web/src/*.vue", "web/src/*.css", "web/src/*.ts"]

_LAYOUT_EXACT = {
    "width", "min-width", "max-width", "height", "min-height", "max-height", "block-size", "inline-size",
    "min-block-size", "max-block-size", "min-inline-size", "max-inline-size", "gap", "row-gap", "column-gap",
    "grid-gap", "grid-row-gap", "grid-column-gap", "top", "right", "bottom", "left", "flex-basis", "flex",
    "grid-template-columns", "grid-template-rows", "grid-auto-columns", "grid-auto-rows", "column-width", "columns",
    "border-spacing",
}
_LAYOUT_PREFIXES = ("margin", "padding", "inset", "scroll-margin", "scroll-padding")
_THIN = re.compile(r"^(?:border|outline)(?:-(?:top|right|bottom|left|block|inline)(?:-(?:start|end))?)?(?:-width)?$")
_SIZE_WORDS = re.compile(r"(width|height|size|gap|space|spacing|padding|margin|inset|offset|columns?|rows?|gutter|sidebar)")
_NOT_LAYOUT_WORDS = re.compile(r"(font|text|line|letter|icon|radius|shadow|border|outline)")
_DECLARATION = re.compile(r"(?<![\w-])(--[\w-]+|[A-Za-z][\w-]*)\s*:\s*")
_PX = re.compile(r"(?<![\w.#-])(\d+(?:\.\d+)?|\.\d+)px\b")
_MEDIA = re.compile(r"@(?:media|container)\b([^{]*)", re.I)
_WIDTH_CONDITION = re.compile(r"(?:min-|max-)?(?:device-)?width|inline-size|width\s*[<>]|[<>]=?\s*width", re.I)
_MATCH_MEDIA = re.compile(r"matchMedia\s*\(\s*[`'\"][^`'\"]*width", re.I)


def kebab(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", lambda m: "-" + m.group(1).lower(), name).lower()


def _without_clamp(value: str) -> str:
    """The value with every clamp(...) call, balanced, removed."""
    result, depth, index = [], 0, 0
    lowered = value.lower()
    while index < len(value):
        if depth == 0 and lowered.startswith("clamp(", index):
            depth, index = 1, index + 6
            continue
        char = value[index]
        if depth:
            depth += (char == "(") - (char == ")")
        else:
            result.append(char)
        index += 1
    return "".join(result)


def _value_end(text: str, start: int) -> int:
    """End of a declaration value: `;`, `}` or `{`, or a comma outside parentheses (next key of an object)."""
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char in ";{}" or (char == "," and depth == 0):
            return index
    return len(text)


def px_violations(line: str) -> list[str]:
    """Reasons for one source line; empty when it keeps the tolerances."""
    reasons = []
    for declaration in _DECLARATION.finditer(line):
        raw = declaration.group(1)
        prop = raw.lower() if raw.startswith("--") else kebab(raw)
        if prop.startswith("--"):
            layout = bool(_SIZE_WORDS.search(prop)) and not _NOT_LAYOUT_WORDS.search(prop)
            thin = False
        else:
            layout = prop in _LAYOUT_EXACT or prop in _LAYOUT_PREFIXES or prop.startswith(tuple(p + "-" for p in _LAYOUT_PREFIXES))
            thin = bool(_THIN.match(prop))
        if not (layout or thin):
            continue
        value = _without_clamp(line[declaration.end():_value_end(line, declaration.end())])
        for match in _PX.finditer(value):
            number = float(match.group(1))
            if number == 0 or (thin and number <= 1):
                continue
            reasons.append(f"{prop}: {match.group(0)} вне clamp() — размер раскладки задаётся относительно (rem, %, fr, clamp)")
    return reasons


def width_conditions(line: str) -> list[str]:
    reasons = []
    for match in _MEDIA.finditer(line):
        if _WIDTH_CONDITION.search(match.group(1)):
            reasons.append(f"условие по ширине: {match.group(0).strip()[:60]} — пороги по ширине не применяются")
    if _MATCH_MEDIA.search(line):
        reasons.append("matchMedia по ширине — пороги по ширине не применяются")
    return reasons


def check_ui_styles(root: Path, base: str = DEFAULT_BASE, head: str = "HEAD",
                    allowed_timers: list[tuple[str, str, str]] | None = None) -> Outcome:
    allowed_timers = ALLOWED_TIMERS if allowed_timers is None else allowed_timers
    outcome = Outcome()
    for path, lines in sorted(added_lines(root, base, head, SPECS).items()):
        if path in GENERATED_FILES or is_test_file(path):
            continue
        text = file_text(root, head, path)
        clean = blank_comments(text, web_comments(text, path)).splitlines()
        for number in sorted(lines):
            if number > len(clean):
                continue
            for reason in [*width_conditions(clean[number - 1]), *px_violations(clean[number - 1])]:
                outcome.violations.append(Violation(path, number, reason))
        for number, call, line_text in find_timers(text, path):
            if number in lines:
                ground = timer_ground(path, line_text, allowed_timers)
                if ground is None:
                    outcome.violations.append(Violation(path, number, f"{call}( : интерфейс без таймеров и опроса, изменения приходят событием"))
                else:
                    outcome.allowed.append(Violation(path, number, f"{call}( : {ground}"))
    return outcome


def main(argv: list[str] | None = None) -> int:
    def add(parser):
        parser.add_argument("--base", default=DEFAULT_BASE, help="commit before the stage")
        parser.add_argument("--head", default="HEAD", help=f"end of the range; {WORKTREE} adds the uncommitted work")

    return run_guard(NAME, lambda a: check_ui_styles(a.root, a.base, a.head), argv, add)


if __name__ == "__main__":
    raise SystemExit(main())
