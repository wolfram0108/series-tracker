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
                "assign_part", "assign_quality", "assign_resolution")
ACTION_TYPES = ("exclude",) + EXTRACT_TYPES + ASSIGN_TYPES
BLOCK_TYPES = ("text", "number", "range", "add", "subtract", "whitespace",
               "any_text", "start_of_line", "end_of_line")
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


def parse_range_spec(raw: str | None) -> list[tuple[int, int]]:
    """Parse range specification string like '1-180', '1, 3-10', '5, 10-20'.

    Returns a sorted list of non-overlapping intervals [(start, end), ...].
    Raises ValueError for empty or malformed input.
    """
    if not raw or not raw.strip():
        raise ValueError("диапазон не может быть пустым")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError("диапазон не может быть пустым")

    intervals: list[tuple[int, int]] = []

    for part in parts:
        m_dash = _RANGE_DASH_PAT.match(part)
        if m_dash:
            a, b = int(m_dash.group(1)), int(m_dash.group(2))
            intervals.append((min(a, b), max(a, b)))
            continue
        m_single = _RANGE_SINGLE_PAT.match(part)
        if m_single:
            val = int(m_single.group(1))
            intervals.append((val, val))
            continue
        raise ValueError(f"некорректный элемент диапазона: {part!r}")

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

def _blocks_to_regex(blocks: list[dict], *, capture_numbers: bool,
                     allow_range: bool = False) -> str:
    parts = []
    for block in blocks:
        b_type = block.get("type")
        if b_type not in BLOCK_TYPES:
            raise ValueError(f"неизвестный тип блока: {b_type!r}")
        if b_type in ("add", "subtract"):
            continue  # Number modifiers are evaluated in actions, not part of regex pattern
        elif b_type == "range":
            if not allow_range:
                raise ValueError("блок «Диапазон» недопустим в действиях «ТО»")
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
            raise ValueError(f"тип блока не поддерживается: {b_type!r}")
    regex = "".join(parts)
    if not regex:
        raise ValueError("пустой шаблон")
    return regex


def _parse_blocks(raw: str | None) -> list[dict]:
    blocks = json.loads(raw or "")
    if not isinstance(blocks, list):
        raise ValueError("шаблон — не список блоков")
    return blocks


def _suffix_block_index(blocks: list[dict], kinds: tuple[str, ...], label: str,
                        where: str) -> int | None:
    """Index of the attachment block (range, +/-) when it hangs on a number.

    One attachment per template, standing right after the last number:
    otherwise the number it refers to would be ambiguous.
    """
    indices = [i for i, b in enumerate(blocks) if b.get("type") in kinds]
    if not indices:
        return None
    if len(indices) > 1:
        raise ValueError(f"в одном {where} может быть только один блок «{label}»")
    idx = indices[0]
    if idx == 0:
        raise ValueError(f"блок «{label}» не может стоять первым — "
                         "он должен следовать за числом")
    if blocks[idx - 1].get("type") != "number":
        raise ValueError(f"блок «{label}» должен следовать сразу за блоком «Число», "
                         f"а не {blocks[idx - 1].get('type')!r}")
    if any(b.get("type") == "number" for b in blocks[idx + 1:]):
        raise ValueError(f"блок «{label}» должен стоять после последнего числа "
                         f"в {where}")
    return idx


# --- скомпилированная модель -----------------------------------------------------

@dataclass(slots=True)
class ConditionRangeCheck:
    intervals: list[tuple[int, int]]
    number_count: int  # 1 for single number with lookahead, >=2 for explicit pairs


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
            compiled.append(_compile_rule(rule))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            invalid.append({"rule_id": rule.get("id"),
                            "name": rule.get("name"), "error": str(exc)})
    return CompiledProfile(profile_id=profile_id, rules=compiled,
                           invalid_rules=invalid)


def _compile_rule(rule: dict) -> CompiledRule:
    conditions = []
    for cond in rule.get("conditions", []):
        ctype = cond.get("condition_type")
        if ctype not in CONDITION_TYPES:
            raise ValueError(f"неизвестный тип условия: {ctype!r}")
        try:
            blocks = _parse_blocks(cond["pattern"])
            if any(b.get("type") in MODIFIER_TYPES for b in blocks):
                raise ValueError("блок «±» допустим только в действиях «ТО»")
            range_check: ConditionRangeCheck | None = None

            r_idx = _suffix_block_index(blocks, ("range",), "Диапазон", "условии")
            if r_idx is not None:
                intervals = parse_range_spec(blocks[r_idx].get("value"))
                num_count = sum(1 for b in blocks if b.get("type") == "number")
                range_check = ConditionRangeCheck(intervals=intervals, number_count=num_count)
                regex = _blocks_to_regex(blocks, capture_numbers=True, allow_range=True)
            else:
                regex = _blocks_to_regex(blocks, capture_numbers=False, allow_range=True)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            # Решение Б: битое условие делает НЕИСПРАВНЫМ всё правило.
            raise ValueError(f"условие повреждено: {exc}") from exc

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
            raise ValueError(f"неизвестный тип действия: {a_type!r}")
        if a_type == "exclude":
            actions.append(CompiledAction(action_type="exclude"))
        elif a_type in ASSIGN_TYPES:
            actions.append(CompiledAction(action_type=a_type,
                                          raw_value=raw_pattern))
        else:  # extract_*
            blocks = _parse_blocks(raw_pattern)
            _suffix_block_index(blocks, MODIFIER_TYPES, "±", "действии")
            regex = _blocks_to_regex(blocks, capture_numbers=True, allow_range=False)
            modifiers: list[int | None] = []
            for i, block in enumerate(blocks):
                if block.get("type") != "number":
                    continue
                mod = None
                if i + 1 < len(blocks):
                    nxt = blocks[i + 1]
                    if nxt.get("type") in ("add", "subtract"):
                        try:
                            value = int(nxt.get("value", 0))
                            mod = value if nxt["type"] == "add" else -value
                        except (ValueError, TypeError):
                            mod = None  # нечисловой модификатор игнорируется
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
    }
    try:
        _compile_rule(rule)
    except json.JSONDecodeError as exc:
        raise ValueError(f"шаблоны правила — некорректный JSON: {exc}") from exc


# --- выполнение -----------------------------------------------------------------

def _match_condition(title: str, cond: CompiledCondition) -> bool:
    if not cond.range_check:
        return bool(cond.regex.search(title))

    rc = cond.range_check
    for m in cond.regex.finditer(title):
        if rc.number_count >= 2 and m.lastindex and m.lastindex >= 2:
            # Symmetrical guard: if either number has a resolution suffix, skip this match
            if _has_quality_suffix(title[m.end(1):]) or _has_quality_suffix(title[m.end(2):]):
                continue
            try:
                a = int(m.group(1))
                b = int(m.group(2))
                c_start, c_end = min(a, b), max(a, b)
            except (ValueError, TypeError):
                continue
        else:
            # Single captured number: verify it is not immediately suffixed by a quality marker (e.g. 1080p)
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
        elif a_type in ("assign_episode", "assign_season", "assign_part"):
            key = "episode" if a_type == "assign_episode" else (
                "season" if a_type == "assign_season" else "part")
            try:
                extracted[key] = int(action.raw_value)
                produced = True
            except (ValueError, TypeError):
                errors.append(f"правило «{rule.name}»: нечисловое значение "
                              f"{a_type}: {action.raw_value!r}")
        else:  # extract_*: у каждого действия СВОЙ матч и СВОИ группы (фикс А)
            if action.regex is None:
                errors.append(f"правило «{rule.name}»: действие {a_type!r} "
                              "без шаблона")
                continue
            match = action.regex.search(title)
            if not match:
                continue
            numbers = [
                _apply_modifier(int(match.group(i + 1)), action.modifiers[i])
                for i in range(len(action.modifiers))]
            if a_type == "extract_range":
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
    for rule in profile.rules:
        if not _conditions_match(title, rule.conditions):
            continue
        before = dict(extracted)
        outcome = _run_actions(title, rule, extracted, errors)
        if outcome == "exclude":
            events.append({"rule": rule.name, "action": "exclude"})
            return {"excluded": True, "extracted": {}, "events": events,
                    "errors": errors}
        if outcome == "data":
            events.append({"rule": rule.name, "action": "extract",
                           "extracted": {k: v for k, v in extracted.items()
                                         if before.get(k) != v}})
            # Фикс В: остановка — только если правило ДАЛО результат.
            if not rule.continue_after_match:
                break
    return {"excluded": False, "extracted": extracted, "events": events,
            "errors": errors}
