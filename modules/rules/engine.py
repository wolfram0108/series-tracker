"""Движок правил — написан с нуля под существующий блочный формат (Р-8).

Формат данных (констрейнт): профиль → правила (по priority) → условия;
шаблоны — JSON-списки блоков визуального конструктора. Семантика
повторяет фактическую семантику старой системы, кроме трёх
согласованных исправлений (А, Б, В — см. contracts/revision.md Р-8).

Профиль компилируется один раз (regex'ы — на этапе загрузки), дальше
названия обрабатываются без повторной компиляции.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from core.contracts.torrent import SUBTITLE_TYPES
from core.reasons import reason

EXTRACT_KEYS = {
    "extract_single": "episode",
    "extract_season": "season",
    "extract_part": "part",
}

# Словарь конструктора: значения, которые понимает движок. Источник — сам
# движок, а не списки панелей: панель предлагает подмножество, а клиент API
# может прислать что угодно.
CONDITION_TYPES = ("contains", "not_contains")
EXTRACT_TYPES = ("extract_single", "extract_range", "extract_season",
                 "extract_part")
ASSIGN_TYPES = ("assign_voiceover", "assign_episode", "assign_season",
                "assign_part", "assign_quality", "assign_resolution",
                "assign_range")
# The actions of the torrent editor (stage 15): a value set as it is, under its own key of the result.
EDITOR_ASSIGN = {"assign_voice_folder": "voice_folder", "assign_subtitle_folder": "subtitle_folder",
                 "assign_language": "language", "assign_subtitle_type": "subtitle_type",
                 # «Тег видео» of the tab «Файлы»: a version of an episode (DIR), an episode of its own with its tag.
                 "assign_video_tag": "video_tag"}
ASSIGN_TYPES = ASSIGN_TYPES + tuple(EDITOR_ASSIGN)
ACTION_TYPES = ("exclude",) + EXTRACT_TYPES + ASSIGN_TYPES
BLOCK_TYPES = ("text", "number", "range", "add", "subtract", "whitespace",
               "any_text", "start_of_line", "end_of_line", "or")
MODIFIER_TYPES = ("add", "subtract")


class ProfileValidationError(ValueError):
    """Профиль содержит неисправные правила (решение Б — громко)."""


# Video quality and resolution suffixes (Latin and Cyrillic)
_RES_SUFFIX_PAT = re.compile(r"^[pPkKiIрРкК](?:[^\w]|$)", re.IGNORECASE)

# Range delimiters indicating compilations in release titles
_LOOKAHEAD_COMP = re.compile(r"^\s*(?:[-–—]|по|to)\s*(\d+)(?![pPkKiIрРкК\d])", re.IGNORECASE)
_LOOKBEHIND_COMP = re.compile(r"(\d+)\s*(?:[-–—]|по|to)\s*$", re.IGNORECASE)

# Range specification parsing patterns
_RANGE_DASH_PAT = re.compile(r"^(\d+)\s*[-–—]\s*(\d+)$")
_RANGE_SINGLE_PAT = re.compile(r"^(\d+)$")


def _has_quality_suffix(tail: str) -> bool:
    """Return True if string immediately starts with a resolution or quality marker."""
    return bool(_RES_SUFFIX_PAT.match(tail))


def parse_assigned_range(raw: str | None) -> tuple[int, int]:
    """The range a rule gives to a row: one interval «A-B» with A below B, not a list and not a single number (that is an
    episode, and «Назначить номер серии» gives it). The numbers are the constructor's alone: the table is not read for them."""
    found = _RANGE_DASH_PAT.match((raw or "").strip())
    if not found:
        raise ValueError(reason("range_shape", raw=str(raw or "")))
    start, end = int(found.group(1)), int(found.group(2))
    if start >= end:
        raise ValueError(reason("range_order", raw=str(raw or "")))
    return start, end


def parse_range_spec(raw: str | None) -> list[tuple[int, int]]:
    """Parse range specification string like '1-180', '1, 3-10', '5, 10-20'.

    Returns a sorted list of non-overlapping intervals [(start, end), ...].
    Raises ValueError for empty or malformed input.
    """
    if not raw or not raw.strip():
        raise ValueError(reason("range_empty"))
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError(reason("range_empty"))

    intervals: list[tuple[int, int]] = []

    for part in parts:
        m_dash = _RANGE_DASH_PAT.match(part)
        if m_dash:
            a, b = int(m_dash.group(1)), int(m_dash.group(2))
            # The user types this spec, so bounds in any order mean one interval; site data like "10-5" is refused.
            intervals.append((min(a, b), max(a, b)))
            continue
        m_single = _RANGE_SINGLE_PAT.match(part)
        if m_single:
            val = int(m_single.group(1))
            intervals.append((val, val))
            continue
        raise ValueError(reason("range_item", part=part))

    intervals.sort()
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if not merged:
            merged.append((start, end))
        else:
            prev_s, prev_e = merged[-1]
            if start <= prev_e + 1:
                merged[-1] = (prev_s, max(prev_e, end))
            else:
                merged.append((start, end))
    return merged


# --- блоки -> regex ------------------------------------------------------------

def _or_groups(blocks: list[dict]) -> dict[int, list[int]]:
    """The texts joined by «или», by the index of the first text of each group: «A» «или» «B» «или» «C» is one place
    of the title that reads A, B or C. «или» stands between two texts, and a text of a group is not empty."""
    for index, block in enumerate(blocks):
        if block.get("type") != "or":
            continue
        around = blocks[index - 1:index] + blocks[index + 1:index + 2]
        if index == 0 or len(around) < 2 or any(each.get("type") != "text" for each in around):
            raise ValueError(reason("or_place"))
    groups: dict[int, list[int]] = {}
    index = 0
    while index < len(blocks):
        if blocks[index].get("type") == "text" and index + 1 < len(blocks) and blocks[index + 1].get("type") == "or":
            members = [index]
            while members[-1] + 2 < len(blocks) and blocks[members[-1] + 1].get("type") == "or":
                members.append(members[-1] + 2)
            if any(not (blocks[member].get("value") or "") for member in members):
                raise ValueError(reason("or_text_empty"))
            groups[index] = members
            index = members[-1] + 1
        else:
            index += 1
    return groups


def _blocks_to_regex(blocks: list[dict], *, capture_numbers: bool,
                     allow_range: bool = False) -> str:
    parts = []
    groups = _or_groups(blocks)
    joined = {member for members in groups.values() for member in members[1:]}
    for index, block in enumerate(blocks):
        b_type = block.get("type")
        if b_type not in BLOCK_TYPES:
            raise ValueError(reason("block_unknown", type=str(b_type)))
        if index in groups:
            # One place of the title, any of the texts: a group that captures nothing, so the numbers keep their order.
            parts.append("(?:" + "|".join(re.escape(blocks[member]["value"]) for member in groups[index]) + ")")
            continue
        if b_type == "or" or index in joined:
            continue
        if b_type in ("add", "subtract"):
            continue  # Number modifiers are evaluated in actions, not part of regex pattern
        elif b_type == "range":
            if not allow_range:
                raise ValueError(reason("range_in_action"))
            continue  # Range filters are evaluated in condition checks, not part of regex pattern
        elif b_type == "text":
            parts.append(re.escape(block.get("value", "")))
        elif b_type == "number":
            parts.append(r"(\d+)" if capture_numbers else r"\d+")
        elif b_type == "whitespace":
            parts.append(r"\s+")
        elif b_type == "any_text":
            parts.append(r".*?")
        elif b_type == "start_of_line":
            parts.append(r"^")
        elif b_type == "end_of_line":
            parts.append(r"$")
        else:
            raise ValueError(reason("block_unknown", type=str(b_type)))
    regex = "".join(parts)
    if not regex:
        raise ValueError(reason("template_empty"))
    return regex


def _parse_blocks(raw: str | None) -> list[dict]:
    blocks = json.loads(raw or "")
    if not isinstance(blocks, list):
        raise ValueError(reason("template_not_list"))
    return blocks


def _suffix_block_index(blocks: list[dict], kinds: tuple[str, ...], block: str,
                        place: str) -> int | None:
    """Index of the attachment block (range, +/-) when it hangs on a number.

    One attachment per template, standing right after the only number:
    with several numbers the block would point at an ambiguous one.
    """
    indices = [i for i, b in enumerate(blocks) if b.get("type") in kinds]
    if not indices:
        return None
    if len(indices) > 1:
        raise ValueError(reason("block_repeated", place=place, block=block))
    idx = indices[0]
    if idx == 0:
        raise ValueError(reason("block_first", block=block))
    if blocks[idx - 1].get("type") != "number":
        raise ValueError(reason("block_after_number", block=block, after=str(blocks[idx - 1].get("type"))))
    if any(b.get("type") == "number" for b in blocks[idx + 1:]):
        raise ValueError(reason("block_after_last_number", block=block, place=place))
    if sum(1 for b in blocks if b.get("type") == "number") != 1:
        raise ValueError(reason("block_one_number", block=block))
    return idx


# --- скомпилированная модель -----------------------------------------------------

@dataclass(slots=True)
class ConditionRangeCheck:
    intervals: list[tuple[int, int]]


@dataclass(slots=True)
class CompiledCondition:
    regex: re.Pattern
    negate: bool                 # not_contains
    or_after: bool               # оператор OR закрывает И-группу ПОСЛЕ этого условия
    range_check: ConditionRangeCheck | None = None


@dataclass(slots=True)
class CompiledAction:
    action_type: str
    raw_value: str | None = None          # для assign_*
    regex: re.Pattern | None = None       # для extract_*
    modifiers: list[int | None] = field(default_factory=list)
    # modifiers[i] — добавка к i-му числу шаблона (None = нет операции)


@dataclass(slots=True)
class CompiledRule:
    rule_id: int
    name: str
    continue_after_match: bool
    conditions: list[CompiledCondition]
    actions: list[CompiledAction]


@dataclass(slots=True)
class CompiledProfile:
    profile_id: int
    rules: list[CompiledRule]
    invalid_rules: list[dict]   # [{rule_id, name, error}] — решение Б


def compile_profile(profile_id: int, raw_rules: list[dict]) -> CompiledProfile:
    """raw_rules — в формате таблиц parser_rules/parser_rule_conditions."""
    compiled, invalid = [], []
    for rule in raw_rules:
        try:
            compiled.extend(_compile_any(rule))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            invalid.append({"rule_id": rule.get("id"),
                            "name": rule.get("name"), "error": str(exc)})
    return CompiledProfile(profile_id=profile_id, rules=compiled,
                           invalid_rules=invalid)


def _compile_any(rule: dict) -> list[CompiledRule]:
    """A rule «Таблица» is the ordinary rules of its rows; any other rule is itself."""
    if rule.get("rule_table"):
        return _compile_table(rule)
    return [_compile_rule(rule)]


def table_rules(rule: dict) -> list[dict]:
    """The rows of a rule «Таблица» as the ordinary rules they are, in their order, at the place of the table: each row is
    one condition «содержит» (its template «ЕСЛИ») and one action of the column (its value «ТО»), with the table's own
    `continue_after_match`. Matching them is the code of every rule; nothing of it is the table's own. A row carries
    both its cells whole; the marks `if_joined` / `then_joined` (a cell joined with the one above) are the editor's way
    of writing the table and mean nothing here."""
    table = rule["rule_table"]
    if not isinstance(table, dict):
        raise ValueError(reason("table_broken"))
    a_type = table.get("action_type")
    if a_type not in ACTION_TYPES:
        raise ValueError(reason("action_unknown", type=str(a_type)))
    rows = table.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError(reason("table_empty"))
    expanded = []
    for number, row in enumerate(rows, 1):
        blocks = row.get("if") if isinstance(row, dict) else None
        if not blocks:
            raise ValueError(reason("table_row_if_empty", number=number))
        then = row.get("then")
        if a_type == "exclude":
            pattern = "[]"
        elif a_type in ASSIGN_TYPES:
            pattern = str(then if then is not None else "").strip()
            if not pattern:
                raise ValueError(reason("table_row_then_empty", number=number))
        else:
            if not then:
                raise ValueError(reason("table_row_template_empty", number=number))
            pattern = json.dumps(then, ensure_ascii=False)
        expanded.append({
            "id": rule["id"], "name": f"{rule['name']} · {number}",
            "continue_after_match": rule.get("continue_after_match"),
            "action_pattern": json.dumps([{"action_type": a_type, "action_pattern": pattern}], ensure_ascii=False),
            "conditions": [{"condition_type": "contains", "logical_operator": "AND",
                            "pattern": json.dumps(blocks, ensure_ascii=False)}],
        })
    return expanded


def _compile_table(rule: dict) -> list[CompiledRule]:
    compiled = []
    for number, row_rule in enumerate(table_rules(rule), 1):
        try:
            compiled.append(_compile_rule(row_rule))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(reason("table_row", number=number, detail=str(exc))) from exc
    return compiled


def _compile_rule(rule: dict) -> CompiledRule:
    conditions = []
    for cond in rule.get("conditions", []):
        ctype = cond.get("condition_type")
        if ctype not in CONDITION_TYPES:
            raise ValueError(reason("condition_type_unknown", type=str(ctype)))
        try:
            blocks = _parse_blocks(cond["pattern"])
            if any(b.get("type") in MODIFIER_TYPES for b in blocks):
                raise ValueError(reason("modifier_in_condition"))
            range_check: ConditionRangeCheck | None = None

            r_idx = _suffix_block_index(blocks, ("range",), "range", "condition")
            if r_idx is not None:
                intervals = parse_range_spec(blocks[r_idx].get("value"))
                range_check = ConditionRangeCheck(intervals=intervals)
                regex = _blocks_to_regex(blocks, capture_numbers=True, allow_range=True)
            else:
                regex = _blocks_to_regex(blocks, capture_numbers=False, allow_range=True)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            # Решение Б: битое условие делает НЕИСПРАВНЫМ всё правило.
            raise ValueError(reason("condition_broken", detail=str(exc))) from exc

        conditions.append(CompiledCondition(
            regex=re.compile(regex, re.IGNORECASE),
            negate=cond["condition_type"] != "contains",
            or_after=cond.get("logical_operator") == "OR",
            range_check=range_check))

    actions = []
    for action in json.loads(rule.get("action_pattern") or "[]"):
        a_type = action.get("action_type")
        raw_pattern = action.get("action_pattern", "[]")
        if a_type not in ACTION_TYPES:
            raise ValueError(reason("action_unknown", type=str(a_type)))
        if a_type == "exclude":
            actions.append(CompiledAction(action_type="exclude"))
        elif a_type in ASSIGN_TYPES:
            if a_type == "assign_range":
                parse_assigned_range(raw_pattern)  # a wrong range is refused on saving, not found in a title
            actions.append(CompiledAction(action_type=a_type,
                                          raw_value=raw_pattern))
        else:  # extract_*
            blocks = _parse_blocks(raw_pattern)
            _suffix_block_index(blocks, MODIFIER_TYPES, "modifier", "action")
            regex = _blocks_to_regex(blocks, capture_numbers=True, allow_range=False)
            modifiers: list[int | None] = []
            for i, block in enumerate(blocks):
                if block.get("type") != "number":
                    continue
                mod = None
                if i + 1 < len(blocks):
                    nxt = blocks[i + 1]
                    if nxt.get("type") in ("add", "subtract"):
                        sign = "+" if nxt["type"] == "add" else "−"
                        try:
                            value = int(nxt.get("value", 0))
                        except (ValueError, TypeError):
                            raise ValueError(reason(
                                "modifier_not_number", sign=sign, value=str(nxt.get("value")))) from None
                        mod = value if nxt["type"] == "add" else -value
                modifiers.append(mod)
            actions.append(CompiledAction(
                action_type=a_type, modifiers=modifiers,
                regex=re.compile(regex, re.IGNORECASE)))

    return CompiledRule(
        rule_id=rule["id"], name=rule["name"],
        continue_after_match=bool(rule.get("continue_after_match")),
        conditions=conditions, actions=actions)


def validate_rule(data: dict) -> None:
    """Проверяет правило до записи: поднимает ValueError с причиной.

    Компилирует правило тем же кодом, что и применение профиля, —
    отдельного валидатора нет, чтобы проверка и исполнение не разошлись.
    """
    rule = {
        "id": 0,
        "name": data.get("name") or "Новое правило",
        "action_pattern": data.get("action_pattern") or "[]",
        "continue_after_match": data.get("continue_after_match") or 0,
        "conditions": data.get("conditions") or [],
        "rule_table": data.get("rule_table"),
    }
    try:
        _compile_any(rule)
    except json.JSONDecodeError as exc:
        raise ValueError(reason("rule_json", detail=str(exc))) from exc


# --- выполнение -----------------------------------------------------------------

def _match_condition(title: str, cond: CompiledCondition) -> bool:
    if not cond.range_check:
        return bool(cond.regex.search(title))

    rc = cond.range_check
    for m in cond.regex.finditer(title):
        # A range filter always hangs on the only number of the template:
        # the compilation around it is detected from the title itself.
        if _has_quality_suffix(title[m.end(1):]):
            continue
        try:
            val = int(m.group(1))
        except (ValueError, TypeError, IndexError):
            continue
        c_start, c_end = val, val
        m_after = _LOOKAHEAD_COMP.match(title[m.end(1):])
        if m_after and not _has_quality_suffix(title[m.end(1) + m_after.end(1):]):
            try:
                after_val = int(m_after.group(1))
                c_start, c_end = min(val, after_val), max(val, after_val)
            except (ValueError, TypeError):
                pass
        else:
            m_before = _LOOKBEHIND_COMP.search(title[:m.start(1)])
            if m_before:
                try:
                    before_val = int(m_before.group(1))
                    c_start, c_end = min(val, before_val), max(val, before_val)
                except (ValueError, TypeError):
                    pass

        if any(max(c_start, r_start) <= min(c_end, r_end)
               for r_start, r_end in rc.intervals):
            return True
    return False


def _conditions_match(title: str, conditions: list[CompiledCondition]) -> bool:
    if not conditions:
        return True
    # Оператор OR у условия закрывает текущую И-группу; итог —
    # «хотя бы одна И-группа целиком истинна» (семантика старой системы).
    or_groups, current = [], []
    for cond in conditions:
        hit = _match_condition(title, cond)
        current.append(hit if not cond.negate else not hit)
        if cond.or_after:
            or_groups.append(all(current))
            current = []
    or_groups.append(all(current))
    return any(or_groups)


def _apply_modifier(value: int, modifier: int | None) -> int:
    if modifier is None:
        return value
    result = value + modifier
    # Именное правило пользователя: отрицательный результат — игнор операции.
    return result if result >= 0 else value


# The number of an episode is one value in one of two forms: a single episode, or a range (start, end). Consumers read a
# single episode before a range, so a result never holds both: whatever sets one form takes the other away.
_SINGLE_KEYS, _RANGE_KEYS = ("episode",), ("start", "end")


def _other_form(key: str) -> tuple[str, ...]:
    return _RANGE_KEYS if key in _SINGLE_KEYS else _SINGLE_KEYS


def _run_actions(title: str, rule: CompiledRule,
                 extracted: dict, errors: list[str]) -> str:
    """Выполняет действия правила; возвращает 'exclude' | 'data' | 'nothing'.

    Внутри одного правила извлечения работают по принципу «первый
    успешный вариант побеждает»: каскад из нескольких extract-действий —
    это альтернативы по приоритету, а не перезапись (фактическое
    поведение старой системы, осмысленное как замысел). Между
    правилами — наоборот: позднее правило перезаписывает раннее.
    """
    produced = False
    set_by_this_rule: set[str] = set()

    def put_extracted(key: str, value) -> bool:
        if key in set_by_this_rule:
            return False  # первый успешный вариант уже победил
        if key in _SINGLE_KEYS + _RANGE_KEYS:
            if any(other in set_by_this_rule for other in _other_form(key)):
                return False  # the first variant of this rule keeps its form of the number
            for other in _other_form(key):
                extracted.pop(other, None)  # a later rule's form replaces the earlier rule's
        extracted[key] = value
        set_by_this_rule.add(key)
        return True

    for action in rule.actions:
        a_type = action.action_type
        if a_type == "exclude":
            return "exclude"
        if a_type == "assign_voiceover":
            extracted["voiceover"] = action.raw_value
            produced = True
        elif a_type == "assign_quality":
            extracted["quality"] = action.raw_value
            produced = True
        elif a_type == "assign_resolution":
            extracted["resolution"] = action.raw_value
            produced = True
        elif a_type in EDITOR_ASSIGN:
            value = str(action.raw_value or "").strip()
            if not value or (a_type == "assign_subtitle_type" and value not in SUBTITLE_TYPES):
                errors.append(reason("rule_value_bad", rule=rule.name, action=a_type, value=value))
                continue
            extracted[EDITOR_ASSIGN[a_type]] = value
            produced = True
        elif a_type == "assign_range":
            extracted.pop("episode", None)
            extracted["start"], extracted["end"] = parse_assigned_range(action.raw_value)
            produced = True
        elif a_type in ("assign_episode", "assign_season", "assign_part"):
            key = "episode" if a_type == "assign_episode" else (
                "season" if a_type == "assign_season" else "part")
            try:
                extracted[key] = int(action.raw_value)
                if key == "episode":
                    extracted.pop("start", None)
                    extracted.pop("end", None)
                produced = True
            except (ValueError, TypeError):
                errors.append(reason("rule_number_bad", rule=rule.name, action=a_type, value=str(action.raw_value)))
        else:  # extract_*: у каждого действия СВОЙ матч и СВОИ группы (фикс А)
            if action.regex is None:
                errors.append(reason("rule_no_template", rule=rule.name, action=a_type))
                continue
            match = action.regex.search(title)
            if not match:
                continue
            numbers = [
                _apply_modifier(int(match.group(i + 1)), action.modifiers[i])
                for i in range(len(action.modifiers))]
            if a_type == "extract_range":
                if len(numbers) > 1 and numbers[1] < numbers[0]:
                    # Swapping the bounds would hide a wrong template; the line stays without a range.
                    errors.append(reason("rule_range_reversed", rule=rule.name, start=numbers[0], end=numbers[1]))
                    continue
                if numbers and put_extracted("start", numbers[0]):
                    produced = True
                if len(numbers) > 1:
                    put_extracted("end", numbers[1])
            elif numbers and put_extracted(EXTRACT_KEYS[a_type], numbers[0]):
                produced = True
    return "data" if produced else "nothing"


def process_title(profile: CompiledProfile, title: str) -> dict[str, Any]:
    """Результат: {excluded, extracted, events, errors}."""
    extracted: dict[str, Any] = {}
    events, errors = [], []
    # `order`: the place of the rule in its profile — what came first decides between rules that gave one thing to two lines.
    for order, rule in enumerate(profile.rules):
        if not _conditions_match(title, rule.conditions):
            continue
        before = dict(extracted)
        outcome = _run_actions(title, rule, extracted, errors)
        if outcome == "exclude":
            events.append({"rule": rule.name, "order": order, "action": "exclude"})
            return {"excluded": True, "extracted": {}, "events": events,
                    "errors": errors}
        if outcome == "data":
            events.append({"rule": rule.name, "order": order, "action": "extract",
                           "extracted": {k: v for k, v in extracted.items()
                                         if before.get(k) != v}})
            # Фикс В: остановка — только если правило ДАЛО результат.
            if not rule.continue_after_match:
                # The rules after this one were not tried: said in the event, so the test of a profile can show why a
                # rule placed lower did nothing.
                events[-1]["stopped"] = True
                break
    return {"excluded": False, "extracted": extracted, "events": events,
            "errors": errors}
