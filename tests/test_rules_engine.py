"""Юнит-тесты семантики движка правил (Р-8).

Дифф со старым движком на 1088 реальных названиях дал 0 расхождений (коммит
54ec516; старой системы больше нет, сверка не повторяется). Здесь — синтетика
для случаев, которых нет в живых данных: исправления А/Б/В, OR-группы,
модификаторы, first-wins.
"""
import json

import pytest

from core.reasons import parse, reason
from modules.rules import compile_profile, process_title, validate_rule


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
    assert "condition_broken" in profile.invalid_rules[0]["error"]
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


def test_assign_part():
    profile = compile_profile(1, [
        _rule(1, "часть", [{"action_type": "assign_part", "action_pattern": "2"}],
              conditions=[_cond(_blocks("2 часть"))], continue_after=True),
        _rule(2, "серия", [_extract("extract_single", "серия", "_", "N")]),
    ])
    res = process_title(profile, "серия 8 2 часть")
    assert res["extracted"] == {"episode": 8, "part": 2}


def test_extract_part():
    profile = compile_profile(1, [
        _rule(1, "извлечь часть", [_extract("extract_part", "часть", "_", "N")],
              continue_after=True),
        _rule(2, "серия", [_extract("extract_single", "серия", "_", "N")]),
    ])
    res = process_title(profile, "серия 8 часть 2")
    assert res["extracted"] == {"part": 2, "episode": 8}


def test_formatter_part_suffix():
    from modules.rules.formatter import format_name
    series = {"name_en": "Show"}
    assert format_name(series, {"season": 1, "episode": 8}) == "Show s01e08.mkv"
    assert format_name(series, {"season": 1, "episode": 8, "part": 1}) == "Show s01e08 Part 1.mkv"
    assert format_name(series, {"season": 1, "episode": 8, "part": 2}) == "Show s01e08 Part 2.mkv"


# --- тесты блока диапазона («Диапазон») ------------------------------------------

def test_parse_range_spec_valid():
    from modules.rules.engine import parse_range_spec
    assert parse_range_spec("1-180") == [(1, 180)]
    assert parse_range_spec("1, 3-10") == [(1, 1), (3, 10)]
    assert parse_range_spec(" 1 - 5 , 6 - 10 ") == [(1, 10)]  # объединение смежных
    # The user types this spec, so bounds in any order mean one interval; site data like "10-5" is refused instead.
    assert parse_range_spec("10-1") == [(1, 10)]  # нормализация инвертированных
    assert parse_range_spec("1–5, 8—12") == [(1, 5), (8, 12)]  # разные дефисы


def test_parse_range_spec_invalid():
    from modules.rules.engine import parse_range_spec
    with pytest.raises(ValueError, match="range_empty"):
        parse_range_spec("")
    with pytest.raises(ValueError, match="range_empty"):
        parse_range_spec("   ")
    with pytest.raises(ValueError, match="range_item"):
        parse_range_spec("abc")
    with pytest.raises(ValueError, match="range_item"):
        parse_range_spec("1-abc")


def test_range_filter_single_episodes():
    # Правило 1: исключить 1-180
    cond_exclude = {
        "condition_type": "contains",
        "logical_operator": "AND",
        "pattern": json.dumps([
            {"type": "text", "value": "Несравненный боевой дух - "},
            {"type": "number"},
            {"type": "range", "value": "1-180"}
        ], ensure_ascii=False)
    }
    rule_exclude = _rule(1, "исключить 1-180", [{"action_type": "exclude", "action_pattern": "[]"}],
                         conditions=[cond_exclude])

    # Правило 2: извлечь серии 181+
    rule_extract = _rule(2, "извлечь серию", [
        _extract("extract_single", "Несравненный боевой дух - ", "N")
    ])

    profile = compile_profile(43, [rule_exclude, rule_extract])
    assert not profile.invalid_rules

    # Серии 1..180 должны исключаться
    assert process_title(profile, "Несравненный боевой дух - 2 серия")["excluded"] is True
    assert process_title(profile, "Несравненный боевой дух - 180 серия")["excluded"] is True

    # Серии 181..212 не должны исключаться и должны извлекаться
    res_181 = process_title(profile, "Несравненный боевой дух - 181 серия")
    assert res_181["excluded"] is False
    assert res_181["extracted"] == {"episode": 181}

    res_212 = process_title(profile, "Несравненный боевой дух - 212 серия")
    assert res_212["excluded"] is False
    assert res_212["extracted"] == {"episode": 212}


def test_range_filter_compilations_overlap_auto_detected():
    # Проверка ключевого требования: если компиляция задета исключением, она исключается
    cond_exclude = {
        "condition_type": "contains",
        "logical_operator": "AND",
        "pattern": json.dumps([
            {"type": "text", "value": "Тайтл - "},
            {"type": "number"},
            {"type": "range", "value": "1-180"}
        ], ensure_ascii=False)
    }
    profile = compile_profile(1, [
        _rule(1, "исключить 1-180", [{"action_type": "exclude", "action_pattern": "[]"}],
              conditions=[cond_exclude]),
        _rule(2, "серия", [_extract("extract_single", "Тайтл - ", "N")])
    ])

    # 10-30: внутри 1-180 -> исключается
    assert process_title(profile, "Тайтл - 10-30 серия")["excluded"] is True
    # 170-190: пересекает 1-180 (серии 170..180 задеты) -> исключается!
    assert process_title(profile, "Тайтл - 170-190 серия")["excluded"] is True
    # 180-185: серия 180 задета -> исключается!
    assert process_title(profile, "Тайтл - 180-185 серия")["excluded"] is True
    # 181-190: строго после 180 (пересечения нет) -> НЕ исключается!
    assert process_title(profile, "Тайтл - 181-190 серия")["excluded"] is False
    # 200-212: строго после 180 -> НЕ исключается!
    assert process_title(profile, "Тайтл - 200-212 серия")["excluded"] is False


def test_range_rejected_with_explicit_compilation_pattern():
    # Two numbers with a range are rejected: the attachment belongs to the only
    # number of the template. The one-number form detects the compilation itself.
    cond = {
        "condition_type": "contains",
        "logical_operator": "AND",
        "pattern": json.dumps([
            {"type": "number"},
            {"type": "text", "value": "-"},
            {"type": "number"},
            {"type": "range", "value": "1-20"}
        ], ensure_ascii=False)
    }
    profile = compile_profile(1, [
        _rule(1, "исключить компиляции", [{"action_type": "exclude", "action_pattern": "[]"}],
              conditions=[cond])
    ])
    assert len(profile.invalid_rules) == 1
    assert "block_one_number" in profile.invalid_rules[0]["error"]


def test_range_list_intervals():
    # Список интервалов: '1, 3-10'
    cond = {
        "condition_type": "contains",
        "logical_operator": "AND",
        "pattern": json.dumps([
            {"type": "text", "value": "эп "},
            {"type": "number"},
            {"type": "range", "value": "1, 3-10"}
        ], ensure_ascii=False)
    }
    profile = compile_profile(1, [
        _rule(1, "исключить", [{"action_type": "exclude", "action_pattern": "[]"}],
              conditions=[cond])
    ])

    assert process_title(profile, "эп 1")["excluded"] is True
    assert process_title(profile, "эп 2")["excluded"] is False
    assert process_title(profile, "эп 5")["excluded"] is True
    assert process_title(profile, "эп 11")["excluded"] is False
    # Компиляция 2-4 задевает серию 3 и 4 -> исключается
    assert process_title(profile, "эп 2-4")["excluded"] is True


def test_range_foolproofing_validations():
    # 1. Range block placed first without preceding number
    b_first = [{"type": "range", "value": "1-10"}]
    p1 = compile_profile(1, [_rule(1, "r1", [{"action_type": "exclude"}],
                                   conditions=[_cond(b_first)])])
    assert len(p1.invalid_rules) == 1
    assert "block_first" in p1.invalid_rules[0]["error"]

    # 2. Range block follows text rather than number
    b_after_text = [{"type": "number"}, {"type": "text", "value": " "}, {"type": "range", "value": "1-10"}]
    p2 = compile_profile(1, [_rule(1, "r2", [{"action_type": "exclude"}],
                                   conditions=[_cond(b_after_text)])])
    assert len(p2.invalid_rules) == 1
    assert "block_after_number" in p2.invalid_rules[0]["error"]

    # 3. Range block is not after the last number (another number follows)
    b_mid_num = [{"type": "number"}, {"type": "range", "value": "1-10"}, {"type": "number"}]
    p3 = compile_profile(1, [_rule(1, "r3", [{"action_type": "exclude"}],
                                   conditions=[_cond(b_mid_num)])])
    assert len(p3.invalid_rules) == 1
    assert "block_after_last_number" in p3.invalid_rules[0]["error"]

    # 4. Invalid range value
    b_broken = [{"type": "number"}, {"type": "range", "value": "не_числа"}]
    p4 = compile_profile(1, [_rule(1, "r4", [{"action_type": "exclude"}],
                                   conditions=[_cond(b_broken)])])
    assert len(p4.invalid_rules) == 1
    assert "range_item" in p4.invalid_rules[0]["error"]


# --- reversed range: not extracted, the reason goes to errors -----------------------

def _range_profile(*extra_actions):
    first = _extract("extract_range", "N", "-", "N")
    return compile_profile(1, [_rule(1, "диапазон", [first, *extra_actions])])


def test_ascending_range_is_extracted():
    result = process_title(_range_profile(), "Тайтл 5-10 серия")
    assert result["extracted"] == {"start": 5, "end": 10}
    assert not result["errors"]


def test_reversed_range_is_not_extracted_and_says_why():
    result = process_title(_range_profile(), "Тайтл 10-5 серия")
    assert result["extracted"] == {}, "bounds must not be swapped silently"
    assert result["events"] == []
    assert len(result["errors"]) == 1
    assert parse(result["errors"][0])["code"] == "rule_range_reversed"
    assert parse(result["errors"][0])["params"] == {"rule": "диапазон", "start": 10, "end": 5}


def test_equal_bounds_are_a_range_of_one_episode():
    assert process_title(_range_profile(), "Тайтл 7-7 серия")["extracted"] == {"start": 7, "end": 7}


def test_reversed_range_lets_the_next_variant_of_the_rule_win():
    # the failed variant must not count as the winner of start/end
    second = _extract("extract_range", "N", " по ", "N")
    result = process_title(_range_profile(second), "Тайтл 10-5 и 3 по 8")
    assert result["extracted"] == {"start": 3, "end": 8}
    assert len(result["errors"]) == 1


def test_reversed_range_does_not_stop_the_cascade():
    profile = compile_profile(1, [
        _rule(1, "диапазон", [_extract("extract_range", "N", "-", "N")]),
        _rule(2, "серия", [_extract("extract_single", "серия ", "N")]),
    ])
    result = process_title(profile, "10-5 серия 4")
    assert result["extracted"] == {"episode": 4}
    assert len(result["errors"]) == 1


# --- тесты блоков «+»/«−» (модификаторов числа) ---------------------------------

def test_modifier_after_number_applies():
    profile = compile_profile(1, [
        _rule(1, "серия", [_extract("extract_single", "серия ", "N", ("subtract", "180"))])
    ])
    assert not profile.invalid_rules
    assert process_title(profile, "серия 181")["extracted"] == {"episode": 1}
    assert process_title(profile, "серия 212")["extracted"] == {"episode": 32}


def test_modifier_add_applies():
    profile = compile_profile(1, [
        _rule(1, "серия", [_extract("extract_single", "N", ("add", "5"))])
    ])
    assert not profile.invalid_rules
    assert process_title(profile, "7")["extracted"] == {"episode": 12}


def test_modifier_must_follow_number():
    profile = compile_profile(1, [
        _rule(1, "сдвиг", [_extract("extract_single", "серия ", ("add", "5"), "N")])
    ])
    assert len(profile.invalid_rules) == 1
    assert "block_after_number" in profile.invalid_rules[0]["error"]


def test_modifier_cannot_stand_first():
    profile = compile_profile(1, [
        _rule(1, "сдвиг", [_extract("extract_single", ("add", "5"))])
    ])
    assert len(profile.invalid_rules) == 1
    assert "block_first" in profile.invalid_rules[0]["error"]


def test_modifier_must_follow_last_number():
    profile = compile_profile(1, [
        _rule(1, "сдвиг", [_extract("extract_single", "N", ("add", "5"), "N")])
    ])
    assert len(profile.invalid_rules) == 1
    assert "block_after_last_number" in profile.invalid_rules[0]["error"]


def test_modifier_rejected_in_condition():
    profile = compile_profile(1, [
        _rule(1, "условие", [], conditions=[_cond([{"type": "number"}, {"type": "add", "value": "5"}])])
    ])
    assert len(profile.invalid_rules) == 1
    assert "modifier_in_condition" in profile.invalid_rules[0]["error"]


def test_only_one_modifier_per_action():
    profile = compile_profile(1, [
        _rule(1, "сдвиг", [_extract("extract_single", "N", ("add", "5"), ("add", "3"))])
    ])
    assert len(profile.invalid_rules) == 1
    assert "block_repeated" in profile.invalid_rules[0]["error"]


def test_range_rejected_in_action():
    profile = compile_profile(1, [
        _rule(1, "диапазон", [_extract("extract_single", "N", ("range", "1-10"))])
    ])
    assert len(profile.invalid_rules) == 1
    assert "range_in_action" in profile.invalid_rules[0]["error"]


def test_attachment_rejected_with_several_numbers():
    # The attachment belongs to the only number of the template.
    profile = compile_profile(1, [
        _rule(1, "два числа", [], conditions=[_cond([{"type": "number"}, {"type": "number"},
                                                     {"type": "range", "value": "1-10"}])])
    ])
    assert len(profile.invalid_rules) == 1
    assert "block_one_number" in profile.invalid_rules[0]["error"]


def test_modifier_rejected_with_several_numbers():
    profile = compile_profile(1, [
        _rule(1, "сдвиг", [_extract("extract_single", "N", "N", ("add", "5"))])
    ])
    assert len(profile.invalid_rules) == 1
    assert "block_one_number" in profile.invalid_rules[0]["error"]


def test_modifier_rejected_in_extract_range_with_two_numbers():
    # extract_range with two numbers and a modifier was accepted before (the
    # second bound was shifted); now the template is rejected as in the panel.
    profile = compile_profile(1, [
        _rule(1, "диапазон серий", [_extract("extract_range", "N", "-", "N", ("add", "5"))])
    ])
    assert len(profile.invalid_rules) == 1
    assert "block_one_number" in profile.invalid_rules[0]["error"]


def test_non_numeric_modifier_value_rejected():
    profile = compile_profile(1, [
        _rule(1, "сдвиг", [_extract("extract_single", "N", ("add", "abc"))])
    ])
    assert len(profile.invalid_rules) == 1
    assert "modifier_not_number" in profile.invalid_rules[0]["error"]


def test_range_filter_compilation_separators():
    # The compilation is detected from the title: the separators the engine knows.
    for sep in ("-", "–", "—", " по ", " to "):
        cond = {
            "condition_type": "contains",
            "logical_operator": "AND",
            "pattern": json.dumps([
                {"type": "number"},
                {"type": "range", "value": "1-20"}
            ], ensure_ascii=False)
        }
        profile = compile_profile(1, [
            _rule(1, "исключить компиляции", [{"action_type": "exclude"}], conditions=[cond])
        ])
        assert not profile.invalid_rules
        assert process_title(profile, f"10{sep}30 серия")["excluded"] is True
        assert process_title(profile, f"21{sep}40 серия")["excluded"] is False


def test_attachment_with_single_number_allowed():
    profile = compile_profile(1, [
        _rule(1, "диапазон", [], conditions=[_cond([{"type": "number"},
                                                     {"type": "range", "value": "1-10"}])])
    ])
    assert not profile.invalid_rules


def test_validate_rule_rejects_misplaced_modifier():
    with pytest.raises(ValueError, match="block_after_number"):
        validate_rule({
            "name": "сдвиг",
            "action_pattern": json.dumps([_extract("extract_single", "серия ", ("add", "5"), "N")],
                                         ensure_ascii=False),
            "conditions": [],
        })


def test_validate_rule_accepts_modifier_after_number():
    validate_rule({
        "name": "сдвиг",
        "action_pattern": json.dumps([_extract("extract_single", "N", ("subtract", "180"))],
                                     ensure_ascii=False),
        "conditions": [],
    })


def test_range_resolution_marker_symmetrical_protection():
    # F1 fix verification: resolution markers like 1080p, 1080i, 1080р, 4к must not be confused with compilation ends
    cond_single = {
        "condition_type": "contains",
        "logical_operator": "AND",
        "pattern": json.dumps([
            {"type": "number"},
            {"type": "range", "value": "171-200"}
        ], ensure_ascii=False)
    }
    profile_single = compile_profile(1, [
        _rule(1, "exclude 171-200", [{"action_type": "exclude"}], conditions=[cond_single])
    ])

    # 170-1080p has episode 170 outside 171-200, and 1080 is a resolution, not an episode number
    assert process_title(profile_single, "170-1080p")["excluded"] is False
    assert process_title(profile_single, "170-1080i")["excluded"] is False
    assert process_title(profile_single, "170-1080р")["excluded"] is False
    assert process_title(profile_single, "170-4к")["excluded"] is False

    # Real compilations must still be matched and excluded
    assert process_title(profile_single, "170-190")["excluded"] is True
    assert process_title(profile_single, "175-190")["excluded"] is True

    # Явный шаблон с двумя числами и диапазоном недопустим (Р-31): блок-приложение
    # принадлежит единственному числу. Проверяем отказ, а не исключение.
    cond_explicit = {
        "condition_type": "contains",
        "logical_operator": "AND",
        "pattern": json.dumps([
            {"type": "number"},
            {"type": "text", "value": "-"},
            {"type": "number"},
            {"type": "range", "value": "1-100"}
        ], ensure_ascii=False)
    }
    profile_explicit = compile_profile(2, [
        _rule(1, "exclude 1-100", [{"action_type": "exclude"}], conditions=[cond_explicit])
    ])
    assert len(profile_explicit.invalid_rules) == 1
    assert "block_one_number" in profile_explicit.invalid_rules[0]["error"]


def test_range_disallowed_in_actions_loudly():
    # F2 fix verification: range block in actions must loudly mark rule invalid under Decision B
    bad_action_rule = _rule(1, "bad action", [
        {"action_type": "extract_single",
         "action_pattern": json.dumps([{"type": "number"}, {"type": "range", "value": "1-10"}])}
    ])
    profile = compile_profile(1, [bad_action_rule])
    assert len(profile.invalid_rules) == 1
    assert "range_in_action" in profile.invalid_rules[0]["error"]


# --- словарь конструктора: неизвестные значения не принимаются --------------------

def test_unknown_action_type_rejected_by_validation():
    with pytest.raises(ValueError, match="action_unknown"):
        validate_rule({"name": "r", "action_pattern": json.dumps(
            [{"action_type": "assign_voiceoverr", "action_pattern": "X"}])})


def test_unknown_condition_type_rejected_by_validation():
    with pytest.raises(ValueError, match="condition_type_unknown"):
        validate_rule({"name": "r",
                       "conditions": [_cond(_blocks("S01"), ctype="Contains")]})


def test_valid_rule_passes_validation():
    validate_rule({
        "name": "r",
        "action_pattern": json.dumps([_extract("extract_single", "N")]),
        "conditions": [_cond(_blocks("S01"))]})


def test_unknown_action_type_disables_rule_not_profile():
    """Опечатка в типе действия отключает правило, но не роняет профиль."""
    profile = compile_profile(1, [
        _rule(1, "r", [{"action_type": "assign_voiceoverr",
                        "action_pattern": "X"}],
              conditions=[_cond(_blocks("S01"))])])
    assert profile.rules == []
    assert len(profile.invalid_rules) == 1
    assert "action_unknown" in profile.invalid_rules[0]["error"]
    assert process_title(profile, "Some.Show.S01E02")["excluded"] is False





# --- file name from ready-made numbers: no rule is applied to it --------------------

def _series(**kw):
    base = {"name_en": "Show", "season": "1"}
    base.update(kw)
    return base


def _item(**kw):
    base = {"season": None, "episode_start": 5, "episode_end": None,
            "part": None, "resolution": "1080p", "voiceover_tag": None}
    base.update(kw)
    return base


def _name(series, item):
    from modules.rules import formatter
    metadata = formatter.build_metadata(series, item)
    return formatter.format_name(series, metadata, original_filename="x.mkv")


def test_series_card_is_default_for_quality_and_resolution():
    series = _series(quality_override="BDRip", resolution_override="2160p")
    assert _name(series, _item()) == "Show s01e05 BDRip 2160p.mkv"


def test_database_facts_used_without_card_overrides():
    assert _name(_series(), _item()) == "Show s01e05 1080p.mkv"


def test_season_of_the_item_beats_series_season():
    assert _name(_series(), _item(season=2)) == "Show s02e05 1080p.mkv"


def test_series_season_fills_an_item_without_season():
    assert _name(_series(season="Season 3"), _item()) == "Show s03e05 1080p.mkv"


def test_end_equal_to_start_or_empty_is_a_single_episode_but_a_later_end_is_a_range():
    from modules.rules import formatter
    for end in (None, 5, 4):
        metadata = formatter.build_metadata(_series(), _item(episode_end=end))
        assert (metadata["episode"], metadata["start"], metadata["end"]) == (5, None, None), end
    metadata = formatter.build_metadata(_series(), _item(episode_end=7))
    assert (metadata["episode"], metadata["start"], metadata["end"]) == (None, 5, 7)


def test_a_range_of_equal_bounds_is_printed_as_one_episode():
    from modules.rules import formatter
    series = _series()
    assert formatter.format_name(series, {"season": 1, "start": 5, "end": 5}) == "Show s01e05.mkv"
    assert formatter.format_name(series, {"season": 1, "start": 5, "end": 6}) == "Show s01e05-e06.mkv"


def test_range_and_part_and_voiceover_of_the_item_reach_the_name():
    item = _item(episode_start=3, episode_end=5, part=2, voiceover_tag="AniLibria")
    assert _name(_series(), item) == "Show s01e03-e05 Part 2 [AniLibria] 1080p.mkv"


def test_a_file_keeps_its_own_extension_whatever_its_sources():
    from modules.rules import formatter
    item = _item()
    series = _series()
    metadata = formatter.build_metadata(series, item)
    assert formatter.format_name(series, metadata, original_filename="x.mkv").endswith(".mkv")
    assert formatter.format_name(series, metadata, original_filename="x.mp4").endswith(".mp4")
    assert formatter.format_name(series, metadata, original_filename="x.avi").endswith(".avi")
    assert formatter.format_name(series, metadata).endswith(".mkv")
    # a leftover source_type must not decide anything any more
    legacy = _series(source_type="vk_video")
    assert formatter.format_name(legacy, formatter.build_metadata(legacy, item),
                                 original_filename="x.avi").endswith(".avi")


# --- the action that assigns a range of episodes: the constructor names it, the table does not ------------------

def _assign_range(value, conditions=()):
    return _rule(1, "склейка", [{"action_type": "assign_range", "action_pattern": value}], conditions=conditions)


def test_assign_range_gives_the_numbers_of_a_compilation_the_title_does_not_say():
    profile = compile_profile(1, [_assign_range("109-110", [_cond(_blocks("110 серия"))])])
    res = process_title(profile, "Название | cvh | AniMy | 1 сезон | 1080p | 110 серия")
    assert res["extracted"] == {"start": 109, "end": 110} and not res["errors"]
    assert process_title(profile, "Название | cvh | AniMy | 1 сезон | 1080p | 111 серия")["extracted"] == {}


def test_assign_range_takes_the_dashes_and_the_spaces_a_person_types():
    for text in ("109-110", "109 – 110", "109—110", " 109 - 110 "):
        res = process_title(compile_profile(1, [_assign_range(text)]), "любое")
        assert res["extracted"] == {"start": 109, "end": 110}, text


@pytest.mark.parametrize("value", ["110", "110-109", "110-110", "109-110, 112", "109-", "-110", "abc", ""])
def test_assign_range_with_anything_but_one_ascending_interval_is_refused_on_saving(value):
    with pytest.raises(ValueError):
        validate_rule({"name": "склейка", "conditions": [],
                       "action_pattern": json.dumps([{"action_type": "assign_range", "action_pattern": value}])})


def _assign_episode(value):
    return _rule(1, "серия", [{"action_type": "assign_episode", "action_pattern": value}], continue_after=True)


def test_a_later_range_replaces_an_earlier_single_episode_and_the_result_holds_one_form():
    rules = [_assign_episode("110"), _rule(2, "склейка", [{"action_type": "assign_range", "action_pattern": "109-110"}])]
    assert process_title(compile_profile(1, rules), "x")["extracted"] == {"start": 109, "end": 110}


def test_a_later_single_episode_replaces_an_earlier_range():
    rules = [_rule(1, "склейка", [{"action_type": "assign_range", "action_pattern": "109-110"}], continue_after=True),
             _rule(2, "серия", [{"action_type": "assign_episode", "action_pattern": "110"}])]
    assert process_title(compile_profile(1, rules), "x")["extracted"] == {"episode": 110}


def test_an_extracted_range_after_an_extracted_single_episode_leaves_the_range_alone():
    single = _rule(1, "серия", [_extract("extract_single", "N", "_", "серия")], continue_after=True)
    span = _rule(2, "склейка", [_extract("extract_range", "N", "-", "N")])
    res = process_title(compile_profile(1, [single, span]), "110 серия 109-110")
    assert res["extracted"] == {"start": 109, "end": 110}


def test_within_one_rule_the_first_form_of_the_number_wins_over_a_later_alternative_of_the_other_form():
    both = _rule(1, "серия или склейка", [_extract("extract_single", "N", "_", "серия"), _extract("extract_range", "N", "-", "N")])
    assert process_title(compile_profile(1, [both]), "5 серия 7-8")["extracted"] == {"episode": 5}


def test_assign_range_next_to_a_voice_over_gives_both():
    profile = compile_profile(1, [_rule(1, "склейка", [
        {"action_type": "assign_range", "action_pattern": "3-4"},
        {"action_type": "assign_voiceover", "action_pattern": "Студия"}])])
    assert process_title(profile, "x")["extracted"] == {"start": 3, "end": 4, "voiceover": "Студия"}


# --- the field of the source every line starts with ---------------------------------------------------------------

def _assign_voiceover(name):
    return {"action_type": "assign_voiceover", "action_pattern": name}


def test_an_ordinary_condition_on_the_source_field_names_the_voice_over_of_that_source_only():
    profile = compile_profile(1, [_rule(1, "тег канала", [_assign_voiceover("ПВА ШОУ")],
                                        [_cond(_blocks("PID:vkvideo.ru"))], continue_after=True)])
    vk = process_title(profile, "PID:vkvideo.ru | P:VK | Тайтл | Серия 1 | R:1080p")
    site = process_title(profile, "PID:1.ruchime.org | P:VK | Тайтл | Серия 1 | R:1080p")
    assert vk["extracted"] == {"voiceover": "ПВА ШОУ"} and site["extracted"] == {}


def test_a_condition_that_is_not_from_a_source_leaves_that_source_out():
    profile = compile_profile(1, [_rule(1, "не с рутайма", [_assign_voiceover("Чужая")],
                                        [_cond(_blocks("PID:1.ruchime.org"), ctype="not_contains")], continue_after=True)])
    assert process_title(profile, "PID:vkvideo.ru | P:VK | Серия 1")["extracted"] == {"voiceover": "Чужая"}
    assert process_title(profile, "PID:1.ruchime.org | P:VK | Серия 1")["extracted"] == {}


# --- the block or: any of the texts next to it ------------------------------------

OR = ("or", "")


def test_or_joins_texts_into_one_place_of_the_title_in_a_condition_and_in_an_extraction():
    """«вечная воля» «или» «воля вечная»: одно место названия, любой из текстов; цепочка из трёх так же."""
    profile = compile_profile(1, [_rule(1, "тайтл", [_extract("extract_single", "серия", OR, "эпизод", "_", "N")],
                                        [_cond(_blocks("вечная воля", OR, "воля вечная", OR, "Eternal Will"))])])
    assert process_title(profile, "Вечная воля, серия 5")["extracted"] == {"episode": 5}
    assert process_title(profile, "ВОЛЯ ВЕЧНАЯ эпизод 7")["extracted"] == {"episode": 7}
    assert process_title(profile, "Eternal Will серия 3")["extracted"] == {"episode": 3}
    assert process_title(profile, "Вечная жизнь серия 5")["extracted"] == {}


def test_or_captures_nothing_so_the_number_after_it_is_the_one_extracted():
    profile = compile_profile(1, [_rule(1, "сдвиг", [
        _extract("extract_single", "серия", OR, "ep", "_", "N", ("add", "1"))])])
    assert process_title(profile, "Тайтл ep 10")["extracted"] == {"episode": 11}
    assert process_title(profile, "Тайтл серия 4")["extracted"] == {"episode": 5}


@pytest.mark.parametrize("specs", [
    (OR, "текст"), ("текст", OR), ("текст", OR, "N"), ("N", OR, "текст"), ("текст", OR, OR, "текст"),
    ("", OR, "текст"),
])
def test_or_out_of_place_or_with_an_empty_text_is_refused_on_saving(specs):
    with pytest.raises(ValueError, match="or_"):
        validate_rule({"conditions": [_cond(_blocks(*specs))]})


# --- the rule «Таблица»: its rows are ordinary rules -----------------------------------------------

def _table(rule_id, name, action_type, rows, continue_after=True):
    return {"id": rule_id, "name": name, "priority": rule_id, "continue_after_match": continue_after,
            "action_pattern": "[]", "conditions": [], "rule_table": {"action_type": action_type, "rows": rows}}


def _same_rules(table):
    """What the table is by the definition of the owner: one ordinary rule per row, «если содержит → действие»."""
    spec = table["rule_table"]
    rules = []
    for number, row in enumerate(spec["rows"], 1):
        then = row["then"]
        pattern = "[]" if spec["action_type"] == "exclude" else (
            then if isinstance(then, str) else json.dumps(then, ensure_ascii=False))
        rules.append(_rule(table["id"], f"{table['name']} · {number}",
                           [{"action_type": spec["action_type"], "action_pattern": pattern}],
                           [_cond(row["if"])], table["continue_after_match"]))
    return rules


VOICES = [{"if": _blocks("V:AniStar"), "then": "AniStar"},
          {"if": [{"type": "text", "value": "V:HORIZON"}, {"type": "or"}, {"type": "text", "value": "mir.donghua"}],
           "then": "HORIZON"},
          {"if": _blocks("PID:animakima.ru", "*", "V:AniSTAR"), "then": "AniStar"}]
EPISODES = [{"if": _blocks("Серия", "_", "N"), "then": _blocks("Серия", "_", "N")},
            {"if": _blocks("N", "_", "серия"), "then": _blocks("N", "_", "серия")}]
TITLES = ["PID:1.ruchime.org | P:Kodik | Вечная воля | Серия 1 | V:AniStar | VT:voice | R:1080",
          "PID:1.ruchime.org | P:Kodik | Вечная воля | Серия 12 | V:HORIZON | VT:voice | R:720",
          "PID:mir.donghua.ru | P:VK | Вечная воля | 7 серия | V:Другая | R:2160",
          "PID:animakima.ru | P:Animakima | Вечная воля | 3 серия | V:AniSTAR | R:1080",
          "Трейлер без номера"]


@pytest.mark.parametrize("continue_after", [True, False])
@pytest.mark.parametrize("action_type,rows", [("assign_voiceover", VOICES), ("extract_single", EPISODES),
                                              ("exclude", [{"if": _blocks("Трейлер"), "then": None}])])
def test_a_table_answers_every_title_as_its_rows_written_as_ordinary_rules_do(action_type, rows, continue_after):
    episode = _rule(9, "серия", [_extract("extract_single", "Серия", "_", "N")], [_cond(_blocks("Серия", "_", "N"))], True)
    table = _table(5, "Озвучки", action_type, rows, continue_after)
    for order in ([table, episode], [episode, table]):
        as_table = compile_profile(1, order)
        as_rules = compile_profile(1, [each for rule in order
                                       for each in (_same_rules(rule) if rule.get("rule_table") else [rule])])
        assert not as_table.invalid_rules
        for title in TITLES:
            assert process_title(as_table, title) == process_title(as_rules, title), title


@pytest.mark.parametrize("table,error", [
    ({"action_type": "assign_voiceover", "rows": []}, "table_empty"),
    ({"action_type": "assign_voiceover", "rows": [{"if": _blocks("V:A"), "then": "A"}, {"if": [], "then": "B"}]},
     "table_row_if_empty"),
    ({"action_type": "assign_voiceover", "rows": [{"if": _blocks("V:A"), "then": "  "}]}, "table_row_then_empty"),
    ({"action_type": "extract_single", "rows": [{"if": _blocks("N"), "then": []}]}, "table_row_template_empty"),
    ({"action_type": "rename", "rows": [{"if": _blocks("V:A"), "then": "A"}]}, "action_unknown"),
    ({"action_type": "assign_range", "rows": [{"if": _blocks("V:A"), "then": "9-3"}]}, "table_row"),
])
def test_a_table_with_a_row_it_cannot_apply_is_refused_on_saving_with_the_number_of_the_row(table, error):
    with pytest.raises(ValueError, match=error):
        validate_rule({"name": "Озвучки", "rule_table": table})


def test_a_broken_table_is_disabled_whole_and_the_other_rules_still_work():
    broken = _table(5, "Озвучки", "assign_voiceover", [{"if": _blocks("V:A"), "then": "A"}, {"if": [], "then": "B"}])
    episode = _rule(9, "серия", [_extract("extract_single", "Серия", "_", "N")], [_cond(_blocks("Серия", "_", "N"))])
    profile = compile_profile(1, [broken, episode])
    assert profile.invalid_rules == [{"rule_id": 5, "name": "Озвучки", "error": reason("table_row_if_empty", number=2)}]
    assert process_title(profile, "Серия 4 | V:A")["extracted"] == {"episode": 4}


def test_the_rule_that_stops_the_rest_says_so_in_its_event_and_a_table_reads_its_rows_whatever_cells_are_joined():
    """The test of a profile shows why a rule placed lower did nothing; the marks of joined cells are the editor's."""
    table = {"id": 7, "name": "Серия", "continue_after_match": 0, "action_pattern": "[]", "conditions": [],
             "rule_table": {"action_type": "extract_single", "rows": [
                 {"if": [{"type": "text", "value": "PID:a"}], "then": [{"type": "text", "value": "第"}, {"type": "number"}]},
                 {"if": [{"type": "text", "value": "PID:a"}], "if_joined": True,
                  "then": [{"type": "number"}, {"type": "whitespace"}, {"type": "text", "value": "серия"}]}]}}
    profile = compile_profile(1, [table])
    assert profile.invalid_rules == []
    # The first «ТО» of the joined «ЕСЛИ» finds nothing in this line, the second does and stops the rest.
    outcome = process_title(profile, "PID:a | 12 серия")
    assert outcome["extracted"] == {"episode": 12}
    assert [(event["rule"], event.get("stopped", False)) for event in outcome["events"]] == [("Серия · 2", True)]
