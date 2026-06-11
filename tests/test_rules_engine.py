"""Юнит-тесты семантики движка правил (Р-8).

Дифф со старым движком на 1088 реальных названиях — tests/verify_rules_diff.py
(0 расхождений). Здесь — синтетика для случаев, которых нет в живых
данных: исправления А/Б/В, OR-группы, модификаторы, first-wins.
"""
import json

import pytest

from modules.rules import compile_profile, process_title


def _rule(rule_id, name, actions, conditions=(), continue_after=False):
    return {
        "id": rule_id, "name": name, "priority": rule_id,
        "continue_after_match": continue_after,
        "action_pattern": json.dumps(actions, ensure_ascii=False),
        "conditions": list(conditions),
    }


def _cond(blocks, ctype="contains", op="AND"):
    return {"condition_type": ctype, "logical_operator": op,
            "pattern": json.dumps(blocks, ensure_ascii=False)}


def _blocks(*specs):
    out = []
    for s in specs:
        if s == "N":
            out.append({"type": "number"})
        elif s == "_":
            out.append({"type": "whitespace"})
        elif s == "*":
            out.append({"type": "any_text"})
        elif isinstance(s, tuple):
            out.append({"type": s[0], "value": s[1]})
        else:
            out.append({"type": "text", "value": s})
    return out


def _extract(action_type, *specs):
    return {"action_type": action_type,
            "action_pattern": json.dumps(_blocks(*specs), ensure_ascii=False)}


# --- фикс А: несколько извлечений в одном правиле --------------------------------

def test_fix_a_second_extraction_not_lost():
    profile = compile_profile(1, [_rule(1, "серия+сезон", [
        _extract("extract_single", "серия", "_", "N"),
        _extract("extract_season", "сезон", "_", "N"),
    ])])
    result = process_title(profile, "Тайтл сезон 2 серия 15")
    # старый движок молча терял season (IndexError во втором действии)
    assert result["extracted"] == {"episode": 15, "season": 2}
    assert not result["errors"]


def test_first_successful_variant_wins_within_rule():
    profile = compile_profile(1, [_rule(1, "каскад", [
        _extract("extract_single", "эпизод", "_", "N"),
        _extract("extract_single", "серия", "_", "N"),
    ])])
    # совпадают оба шаблона — побеждает первый (приоритет вариантов)
    result = process_title(profile, "эпизод 7 он же серия 8")
    assert result["extracted"] == {"episode": 7}


# --- фикс Б: битое условие — громко -----------------------------------------------

def test_fix_b_broken_condition_disables_rule_loudly():
    broken = _rule(1, "битое", [_extract("extract_single", "e", "N")],
                   conditions=[{"condition_type": "contains",
                                "logical_operator": "AND",
                                "pattern": "не json"}])
    healthy = _rule(2, "здоровое", [_extract("extract_single", "серия", "_", "N")])
    profile = compile_profile(1, [broken, healthy])

    assert len(profile.invalid_rules) == 1
    assert profile.invalid_rules[0]["name"] == "битое"
    assert "условие повреждено" in profile.invalid_rules[0]["error"]
    # здоровое правило работает, битое не применяется (а не «ширеет»)
    result = process_title(profile, "серия 3")
    assert result["extracted"] == {"episode": 3}


# --- фикс В: каскад продолжается без результата -----------------------------------

def test_fix_v_no_result_does_not_stop_cascade():
    rule_single = _rule(1, "одиночная", [_extract("extract_single", "серия", "_", "N")],
                        conditions=[_cond(_blocks("сери"))])   # условие ШИРЕ шаблона
    rule_range = _rule(2, "компиляция", [_extract("extract_range",
                                                  "N", ("text", "-"), "N", "_", "сери")])
    profile = compile_profile(1, [rule_single, rule_range])

    result = process_title(profile, "Тайтл 1-12 серии")
    # старый движок остановился бы на «одиночной» (условие совпало) впустую
    assert result["extracted"] == {"start": 1, "end": 12}


def test_stop_after_result_still_works():
    profile = compile_profile(1, [
        _rule(1, "первое", [_extract("extract_single", "серия", "_", "N")]),
        _rule(2, "второе", [{"action_type": "assign_season",
                             "action_pattern": "99"}]),
    ])
    result = process_title(profile, "серия 5")
    assert result["extracted"] == {"episode": 5}, \
        "после результата с continue=0 обход должен остановиться"


# --- остальная семантика -----------------------------------------------------------

def test_or_groups_semantics():
    rule = _rule(1, "или", [{"action_type": "assign_voiceover",
                             "action_pattern": "X"}],
                 conditions=[
                     _cond(_blocks("аниме"), op="AND"),
                     _cond(_blocks("сезон"), op="OR"),   # (аниме И сезон) ИЛИ (фильм)
                     _cond(_blocks("фильм"), op="AND"),
                 ])
    profile = compile_profile(1, [rule])
    assert process_title(profile, "аниме второй сезон")["extracted"]
    assert process_title(profile, "просто фильм")["extracted"]
    assert not process_title(profile, "аниме без слова о периоде")["extracted"]


def test_exclude_stops_everything():
    profile = compile_profile(1, [
        _rule(1, "исключение", [{"action_type": "exclude",
                                 "action_pattern": "[]"}],
              conditions=[_cond(_blocks("трейлер"))]),
        _rule(2, "серия", [_extract("extract_single", "серия", "_", "N")]),
    ])
    result = process_title(profile, "трейлер серия 1")
    assert result["excluded"] is True and result["extracted"] == {}


def test_negative_modifier_ignored_user_rule():
    # серия 3 с модификатором -10: результат был бы -7 → операция игнорируется
    profile = compile_profile(1, [_rule(1, "минус", [
        _extract("extract_single", "серия", "_", "N", ("subtract", "10")),
    ])])
    assert process_title(profile, "серия 3")["extracted"] == {"episode": 3}
    # а серия 13 — честно уменьшается
    assert process_title(profile, "серия 13")["extracted"] == {"episode": 3}


def test_later_rule_overwrites_earlier():
    profile = compile_profile(1, [
        _rule(1, "первое", [{"action_type": "assign_season",
                             "action_pattern": "1"}], continue_after=True),
        _rule(2, "второе", [{"action_type": "assign_season",
                             "action_pattern": "2"}]),
    ])
    assert process_title(profile, "что угодно")["extracted"] == {"season": 2}


def test_case_insensitive():
    profile = compile_profile(1, [_rule(1, "регистр", [
        _extract("extract_single", "СЕРИЯ", "_", "N")])])
    assert process_title(profile, "серия 4")["extracted"] == {"episode": 4}
