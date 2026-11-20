"""Юнит-тесты семантики движка правил (Р-8).

Дифф со старым движком на 1088 реальных названиях дал 0 расхождений (коммит
54ec516; старой системы больше нет, сверка не повторяется). Здесь — синтетика
для случаев, которых нет в живых данных: исправления А/Б/В, OR-группы,
модификаторы, first-wins.
"""
import json


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

# --- фикс Б: битое условие — громко -----------------------------------------------

# --- фикс В: каскад продолжается без результата -----------------------------------

# --- остальная семантика -----------------------------------------------------------

# --- тесты блока диапазона («Диапазон») ------------------------------------------

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

# --- тесты блоков «+»/«−» (модификаторов числа) ---------------------------------

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


# --- словарь конструктора: неизвестные значения не принимаются --------------------

# --- file name from ready-made numbers: no rule is applied to it --------------------

# --- the action that assigns a range of episodes: the constructor names it, the table does not ------------------

def _assign_episode(value):
    return _rule(1, "серия", [{"action_type": "assign_episode", "action_pattern": value}], continue_after=True)


def test_a_later_range_replaces_an_earlier_single_episode_and_the_result_holds_one_form():
    rules = [_assign_episode("110"), _rule(2, "склейка", [{"action_type": "assign_range", "action_pattern": "109-110"}])]
    assert process_title(compile_profile(1, rules), "x")["extracted"] == {"start": 109, "end": 110}


# --- the field of the source every line starts with ---------------------------------------------------------------

# --- the block or: any of the texts next to it ------------------------------------

OR = ("or", "")


def test_or_captures_nothing_so_the_number_after_it_is_the_one_extracted():
    profile = compile_profile(1, [_rule(1, "сдвиг", [
        _extract("extract_single", "серия", OR, "ep", "_", "N", ("add", "1"))])])
    assert process_title(profile, "Тайтл ep 10")["extracted"] == {"episode": 11}
    assert process_title(profile, "Тайтл серия 4")["extracted"] == {"episode": 5}


# --- the rule «Таблица»: its rows are ordinary rules -----------------------------------------------

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


def test_the_rows_of_one_joined_condition_with_different_actions_each_act_the_episode_and_the_season():
    """A torrent's files: «ЕСЛИ» the name of the release, «ТО» the number of the episode and, in a row joined to it, the
    season 6. Both act, as the two actions of one rule; a row that is not joined is a rule of its own after it."""
    table = {"id": 9, "name": "JoJo", "continue_after_match": 0, "action_pattern": "[]", "conditions": [],
             "rule_table": {"action_type": "extract_single", "rows": [
                 {"if": [{"type": "text", "value": "Steel_Ball_Run"}],
                  "then": [{"type": "text", "value": "["}, {"type": "number"}, {"type": "text", "value": "]"}]},
                 {"if": [{"type": "text", "value": "Steel_Ball_Run"}], "if_joined": True, "then": "6",
                  "action_type": "assign_season"},
                 {"if": [{"type": "text", "value": "Other"}], "then": "2", "action_type": "assign_season"}]}}
    profile = compile_profile(1, [table])
    assert profile.invalid_rules == []
    outcome = process_title(profile, "D:JoJo | JoJo_Steel_Ball_Run_[01].mkv | K:видео | X:mkv")
    assert outcome["extracted"] == {"episode": 1, "season": 6}
    assert [event["rule"] for event in outcome["events"]] == ["JoJo · 1–2"]
    assert process_title(profile, "Other_[01].mkv")["extracted"] == {"season": 2}
