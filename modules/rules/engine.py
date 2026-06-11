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

EXTRACT_KEYS = {"extract_single": "episode", "extract_season": "season"}


class ProfileValidationError(ValueError):
    """Профиль содержит неисправные правила (решение Б — громко)."""


# --- блоки -> regex ------------------------------------------------------------

def _blocks_to_regex(blocks: list[dict], *, capture_numbers: bool) -> str:
    parts = []
    for block in blocks:
        b_type = block.get("type")
        if b_type in ("add", "subtract"):
            continue  # модификаторы чисел — не часть шаблона
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
            raise ValueError(f"неизвестный тип блока: {b_type!r}")
    regex = "".join(parts)
    if not regex:
        raise ValueError("пустой шаблон")
    return regex


def _parse_blocks(raw: str | None) -> list[dict]:
    blocks = json.loads(raw or "")
    if not isinstance(blocks, list):
        raise ValueError("шаблон — не список блоков")
    return blocks


# --- скомпилированная модель -----------------------------------------------------

@dataclass(slots=True)
class CompiledCondition:
    regex: re.Pattern
    negate: bool                 # not_contains
    or_after: bool               # оператор OR закрывает И-группу ПОСЛЕ этого условия


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
        try:
            regex = _blocks_to_regex(_parse_blocks(cond["pattern"]),
                                     capture_numbers=False)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            # Решение Б: битое условие делает НЕИСПРАВНЫМ всё правило.
            raise ValueError(f"условие повреждено: {exc}") from exc
        conditions.append(CompiledCondition(
            regex=re.compile(regex, re.IGNORECASE),
            negate=cond["condition_type"] != "contains",
            or_after=cond.get("logical_operator") == "OR"))

    actions = []
    for action in json.loads(rule.get("action_pattern") or "[]"):
        a_type = action.get("action_type")
        raw_pattern = action.get("action_pattern", "[]")
        if a_type == "exclude":
            actions.append(CompiledAction(action_type="exclude"))
        elif a_type and a_type.startswith("assign_"):
            actions.append(CompiledAction(action_type=a_type,
                                          raw_value=raw_pattern))
        elif a_type in ("extract_single", "extract_season", "extract_range"):
            blocks = _parse_blocks(raw_pattern)
            regex = _blocks_to_regex(blocks, capture_numbers=True)
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
        else:
            raise ValueError(f"неизвестный тип действия: {a_type!r}")

    return CompiledRule(
        rule_id=rule["id"], name=rule["name"],
        continue_after_match=bool(rule.get("continue_after_match")),
        conditions=conditions, actions=actions)


# --- выполнение -----------------------------------------------------------------

def _conditions_match(title: str, conditions: list[CompiledCondition]) -> bool:
    if not conditions:
        return True
    # Оператор OR у условия закрывает текущую И-группу; итог —
    # «хотя бы одна И-группа целиком истинна» (семантика старой системы).
    or_groups, current = [], []
    for cond in conditions:
        hit = bool(cond.regex.search(title))
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
        elif a_type in ("assign_episode", "assign_season"):
            key = "episode" if a_type == "assign_episode" else "season"
            try:
                extracted[key] = int(action.raw_value)
                produced = True
            except (ValueError, TypeError):
                errors.append(f"правило «{rule.name}»: нечисловое значение "
                              f"{a_type}: {action.raw_value!r}")
        else:  # extract_*: у каждого действия СВОЙ матч и СВОИ группы (фикс А)
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
