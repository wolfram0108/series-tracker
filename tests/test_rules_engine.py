"""Юнит-тесты семантики движка правил (Р-8).

Дифф со старым движком на 1088 реальных названиях — tests/verify_rules_diff.py
(0 расхождений). Здесь — синтетика для случаев, которых нет в живых
данных: исправления А/Б/В, OR-группы, модификаторы, first-wins.
"""
import json

import pytest

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
    series = {"name_en": "Show", "source_type": "vk_video"}
    # Без части -> Show s01e08.mp4
    assert format_name(series, {"season": 1, "episode": 8}) == "Show s01e08.mp4"
    # С частью 1 -> Show s01e08 Part 1.mp4
    assert format_name(series, {"season": 1, "episode": 8, "part": 1}) == "Show s01e08 Part 1.mp4"
    # С частью 2 -> Show s01e08 Part 2.mp4
    assert format_name(series, {"season": 1, "episode": 8, "part": 2}) == "Show s01e08 Part 2.mp4"


# --- тесты блока диапазона («Диапазон») ------------------------------------------

def test_parse_range_spec_valid():
    from modules.rules.engine import parse_range_spec
    assert parse_range_spec("1-180") == [(1, 180)]
    assert parse_range_spec("1, 3-10") == [(1, 1), (3, 10)]
    assert parse_range_spec(" 1 - 5 , 6 - 10 ") == [(1, 10)]  # объединение смежных
    assert parse_range_spec("10-1") == [(1, 10)]  # нормализация инвертированных
    assert parse_range_spec("1–5, 8—12") == [(1, 5), (8, 12)]  # разные дефисы


def test_parse_range_spec_invalid():
    from modules.rules.engine import parse_range_spec
    with pytest.raises(ValueError, match="не может быть пустым"):
        parse_range_spec("")
    with pytest.raises(ValueError, match="не может быть пустым"):
        parse_range_spec("   ")
    with pytest.raises(ValueError, match="некорректный элемент"):
        parse_range_spec("abc")
    with pytest.raises(ValueError, match="некорректный элемент"):
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
    # Явный шаблон компиляции из двух чисел с диапазоном недопустим: блок-приложение
    # принадлежит единственному числу шаблона. Тот же смысл («исключить
    # компиляции, задевающие 1-20») даёт форма с одним числом — компиляция
    # определяется по самому названию.
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
    assert "должно быть ровно одно число" in profile.invalid_rules[0]["error"]


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
    assert "не может стоять первым" in p1.invalid_rules[0]["error"]

    # 2. Range block follows text rather than number
    b_after_text = [{"type": "number"}, {"type": "text", "value": " "}, {"type": "range", "value": "1-10"}]
    p2 = compile_profile(1, [_rule(1, "r2", [{"action_type": "exclude"}],
                                   conditions=[_cond(b_after_text)])])
    assert len(p2.invalid_rules) == 1
    assert "должен следовать сразу за блоком «Число»" in p2.invalid_rules[0]["error"]

    # 3. Range block is not after the last number (another number follows)
    b_mid_num = [{"type": "number"}, {"type": "range", "value": "1-10"}, {"type": "number"}]
    p3 = compile_profile(1, [_rule(1, "r3", [{"action_type": "exclude"}],
                                   conditions=[_cond(b_mid_num)])])
    assert len(p3.invalid_rules) == 1
    assert "после последнего числа" in p3.invalid_rules[0]["error"]

    # 4. Invalid range value
    b_broken = [{"type": "number"}, {"type": "range", "value": "не_числа"}]
    p4 = compile_profile(1, [_rule(1, "r4", [{"action_type": "exclude"}],
                                   conditions=[_cond(b_broken)])])
    assert len(p4.invalid_rules) == 1
    assert "некорректный элемент диапазона" in p4.invalid_rules[0]["error"]


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
    assert "должен следовать сразу за блоком «Число»" in profile.invalid_rules[0]["error"]


def test_modifier_cannot_stand_first():
    profile = compile_profile(1, [
        _rule(1, "сдвиг", [_extract("extract_single", ("add", "5"))])
    ])
    assert len(profile.invalid_rules) == 1
    assert "не может стоять первым" in profile.invalid_rules[0]["error"]


def test_modifier_must_follow_last_number():
    profile = compile_profile(1, [
        _rule(1, "сдвиг", [_extract("extract_single", "N", ("add", "5"), "N")])
    ])
    assert len(profile.invalid_rules) == 1
    assert "после последнего числа" in profile.invalid_rules[0]["error"]


def test_modifier_rejected_in_condition():
    profile = compile_profile(1, [
        _rule(1, "условие", [], conditions=[_cond([{"type": "number"}, {"type": "add", "value": "5"}])])
    ])
    assert len(profile.invalid_rules) == 1
    assert "допустим только в действиях «ТО»" in profile.invalid_rules[0]["error"]


def test_only_one_modifier_per_action():
    profile = compile_profile(1, [
        _rule(1, "сдвиг", [_extract("extract_single", "N", ("add", "5"), ("add", "3"))])
    ])
    assert len(profile.invalid_rules) == 1
    assert "может быть только один блок «±»" in profile.invalid_rules[0]["error"]


def test_range_rejected_in_action():
    profile = compile_profile(1, [
        _rule(1, "диапазон", [_extract("extract_single", "N", ("range", "1-10"))])
    ])
    assert len(profile.invalid_rules) == 1
    assert "недопустим в действиях «ТО»" in profile.invalid_rules[0]["error"]


def test_attachment_rejected_with_several_numbers():
    # Блок-приложение принадлежит единственному числу шаблона.
    profile = compile_profile(1, [
        _rule(1, "два числа", [], conditions=[_cond([{"type": "number"}, {"type": "number"},
                                                     {"type": "range", "value": "1-10"}])])
    ])
    assert len(profile.invalid_rules) == 1
    assert "должно быть ровно одно число" in profile.invalid_rules[0]["error"]


def test_modifier_rejected_with_several_numbers():
    profile = compile_profile(1, [
        _rule(1, "сдвиг", [_extract("extract_single", "N", "N", ("add", "5"))])
    ])
    assert len(profile.invalid_rules) == 1
    assert "должно быть ровно одно число" in profile.invalid_rules[0]["error"]


def test_attachment_with_single_number_allowed():
    profile = compile_profile(1, [
        _rule(1, "диапазон", [], conditions=[_cond([{"type": "number"},
                                                     {"type": "range", "value": "1-10"}])])
    ])
    assert not profile.invalid_rules


def test_validate_rule_rejects_misplaced_modifier():
    with pytest.raises(ValueError, match="должен следовать сразу за блоком «Число»"):
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

    # Explicit two-number pattern with resolution attached to second number
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
    assert process_title(profile_explicit, "10-1080p")["excluded"] is False


def test_range_disallowed_in_actions_loudly():
    # F2 fix verification: range block in actions must loudly mark rule invalid under Decision B
    bad_action_rule = _rule(1, "bad action", [
        {"action_type": "extract_single",
         "action_pattern": json.dumps([{"type": "number"}, {"type": "range", "value": "1-10"}])}
    ])
    profile = compile_profile(1, [bad_action_rule])
    assert len(profile.invalid_rules) == 1
    assert "недопустим в действиях «ТО»" in profile.invalid_rules[0]["error"]


# --- словарь конструктора: неизвестные значения не принимаются --------------------

def test_unknown_action_type_rejected_by_validation():
    with pytest.raises(ValueError, match="неизвестный тип действия"):
        validate_rule({"name": "r", "action_pattern": json.dumps(
            [{"action_type": "assign_voiceoverr", "action_pattern": "X"}])})


def test_unknown_condition_type_rejected_by_validation():
    with pytest.raises(ValueError, match="неизвестный тип условия"):
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
    assert "неизвестный тип действия" in profile.invalid_rules[0]["error"]
    assert process_title(profile, "Some.Show.S01E02")["excluded"] is False





# --- приоритет: правила важнее карточки сериала -----------------------------------

def _series(**kw):
    base = {"name_en": "Show", "source_type": "torrent", "season": "1"}
    base.update(kw)
    return base


def _item(**kw):
    base = {"season": None, "episode_start": 5, "episode_end": None,
            "part": None, "resolution": "1080p", "voiceover_tag": None}
    base.update(kw)
    return base


def _name(series, item, extracted):
    from modules.rules import formatter
    metadata = formatter.build_metadata(series, item, extracted)
    return formatter.format_name(series, metadata, original_filename="x.mkv")


def test_rules_beat_series_card_overrides():
    series = _series(quality_override="BDRip", resolution_override="2160p")
    name = _name(series, _item(), {"quality": "WEB-DL", "resolution": "1080p"})
    assert name == "Show s01e05 WEB-DL 1080p.mkv"


def test_series_card_is_default_when_rules_silent():
    series = _series(quality_override="BDRip", resolution_override="2160p")
    assert _name(series, _item(), {}) == "Show s01e05 BDRip 2160p.mkv"


def test_database_facts_used_without_rules_and_card():
    assert _name(_series(), _item(), {}) == "Show s01e05 1080p.mkv"


def test_extracted_season_beats_series_season():
    assert _name(_series(), _item(), {"season": 2}) == "Show s02e05 1080p.mkv"
