"""Сверка словаря конструктора: панель и движок должны знать одно и то же.

Словарь написан в двух местах — в движке правил (engine.py) и в панели
(web/src/components/settings). Расхождение опаснее всего в одну сторону:
если панель предлагает значение, которого движок не понимает, правило
не сохранится (API отвечает 400). В другую сторону расхождение безобидно —
движок умеет больше, чем показывает панель.

Тест ловит оба направления: недопустимые значения панели — ошибка сразу,
непокрытые возможности движка — ошибка, если их не внесли в ENGINE_ONLY.
"""
import re
from pathlib import Path

from modules.rules.engine import (ACTION_TYPES, ASSIGN_TYPES, BLOCK_TYPES,
                                  CONDITION_TYPES, EXTRACT_TYPES)

SETTINGS = (Path(__file__).resolve().parent.parent / "web" / "src"
            / "components" / "settings")
CARD = SETTINGS / "ParserRuleCard.vue"
PARSER = SETTINGS / "SettingsParser.vue"
EDITOR = SETTINGS / "ParserPatternEditor.vue"

# Типы, которые движок умеет, а панель сознательно не предлагает.
ENGINE_ONLY = {"extract_part"}


def _const_block(path: Path, name: str) -> str | None:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"const\s+{name}\b[^=\n]*=\s*\[", text)
    if not match:
        return None
    start = match.end() - 1
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _values(path: Path, name: str, key: str | None = None) -> set[str]:
    """Значения константы панели: key='value' для списков объектов."""
    block = _const_block(path, name)
    assert block is not None, f"в {path.name} нет константы {name}"
    if key:
        return set(re.findall(rf'{key}\s*:\s*"([^"]+)"', block))
    return set(re.findall(r'"([^"]+)"', block))


def test_panel_condition_types_known_to_engine():
    panel = _values(CARD, "CONDITION_TYPES", "value")
    assert panel <= set(CONDITION_TYPES), f"панель знает лишние: {panel - set(CONDITION_TYPES)}"


def test_panel_action_types_known_to_engine():
    panel = _values(CARD, "ACTION_TYPES", "value")
    assert panel <= set(ACTION_TYPES), f"панель знает лишние: {panel - set(ACTION_TYPES)}"


def test_panel_extract_and_assign_types_known_to_engine():
    assert _values(CARD, "EXTRACT_TYPES") <= set(EXTRACT_TYPES)
    assert _values(CARD, "ASSIGN_TYPES") <= set(ASSIGN_TYPES)
    assert _values(PARSER, "EXTRACT_TYPES") <= set(EXTRACT_TYPES)


def test_panel_block_types_known_to_engine():
    panel = _values(EDITOR, "BLOCK_PALETTE", "type")
    assert panel <= set(BLOCK_TYPES), f"панель знает лишние: {panel - set(BLOCK_TYPES)}"


def test_panel_offers_every_engine_action():
    panel = _values(CARD, "ACTION_TYPES", "value")
    missing = set(ACTION_TYPES) - panel - ENGINE_ONLY
    assert not missing, (f"панель не предлагает: {sorted(missing)} — "
                         "добавьте в панель или в ENGINE_ONLY теста")
