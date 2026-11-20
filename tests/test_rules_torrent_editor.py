"""The rules of the torrent editor (stage 15): the line of each tab as the editor writes it, and the actions that set a
folder of a voice-over or of subtitles, a language and a type of subtitles; the season and the episode of a file are the
constructor's own extractions over the line of the tab «Файлы». The layouts are those of the corpus of the owner's
releases (DESIGN.md): every file in one folder with suffixes, folders by studio, numbers only in the names of the sounds,
seasons and OVA in folders of their own."""
import json


from modules.rules import compile_profile, process_title


def rule(rule_id, name, actions, conditions=()):
    return {"id": rule_id, "name": name, "priority": rule_id, "continue_after_match": True,
            "action_pattern": json.dumps(actions, ensure_ascii=False), "conditions": list(conditions)}


def contains(*blocks, ctype="contains"):
    return {"condition_type": ctype, "logical_operator": "AND", "pattern": json.dumps(list(blocks), ensure_ascii=False)}


def text(value):
    return {"type": "text", "value": value}


NUMBER, ANY = {"type": "number"}, {"type": "any_text"}


def assign(action, value):
    return {"action_type": action, "action_pattern": value}  # the value of an assignment is stored as its text


def extract(action, *blocks):
    return {"action_type": action, "action_pattern": json.dumps(list(blocks), ensure_ascii=False)}


FILES = compile_profile(1, [
    rule(1, "не видео и не звук", [{"action_type": "exclude"}], [contains(text("K:другое"))]),
    rule(2, "сезон из каталога", [extract("extract_season", text("Season "), NUMBER)], [contains(text("D:"))]),
    rule(3, "серия после тире", [extract("extract_single", text(" - "), NUMBER)]),
    rule(4, "серия SxxEyy", [extract("extract_season", text("S"), NUMBER, text("E")),
                              extract("extract_single", text("E"), NUMBER)]),
])


VOICES = compile_profile(2, [
    rule(1, "студии", [assign("assign_voice_folder", "JAM"), assign("assign_language", "rus")],
         [contains(text("D:Show/Rus Audio/JAM"))]),
    rule(2, "надписи", [assign("assign_subtitle_folder", "Надписи"), assign("assign_subtitle_type", "signs")],
         [contains(text("D:"), ANY, text("Signs"))]),
])


def test_a_folder_is_named_with_its_language_in_one_action_a_name_alone_names_the_folder_only():
    """«ТО» of two fields: the folder and its language, in a rule and in a row of a table; a value of a name alone (a rule
    written before) gives the folder; a value with no name is an error."""
    both = json.dumps({"name": "AnimeSpace Team", "language": "rus"}, ensure_ascii=False)
    profile = compile_profile(4, [
        rule(1, "звук", [assign("assign_voice_folder", both)], [contains(text("AnimeSpace"))]),
        rule(2, "субтитры", [assign("assign_subtitle_folder", "Надписи")], [contains(text("Signs"))]),
        rule(3, "пусто", [assign("assign_voice_folder", json.dumps({"name": " ", "language": "rus"}))], [contains(text("Пусто"))])])
    assert process_title(profile, "D:Sound/[AnimeSpace Team] | 01.mka")["extracted"] == {"voice_folder": "AnimeSpace Team",
                                                                                        "language": "rus"}
    assert process_title(profile, "D:Subs/Signs | 01.ass")["extracted"] == {"subtitle_folder": "Надписи"}
    empty = process_title(profile, "D:Пусто | 01.mka")
    assert empty["extracted"] == {} and '"rule_value_bad"' in empty["errors"][0]
    table = compile_profile(5, [{"id": 1, "name": "таблица", "priority": 1, "continue_after_match": False,
                                 "action_pattern": "[]", "conditions": [],
                                 "rule_table": {"action_type": "assign_voice_folder",
                                                "rows": [{"if": [text("AnimeSpace")], "then": both}]}}])
    assert process_title(table, "D:Sound/[AnimeSpace Team] | 01.mka")["extracted"] == {"voice_folder": "AnimeSpace Team",
                                                                                      "language": "rus"}
    # The language of the table is every row's with none of its own; a row's own stays.
    defaulted = compile_profile(6, [{"id": 1, "name": "таблица", "priority": 1, "continue_after_match": False,
                                     "action_pattern": "[]", "conditions": [],
                                     "rule_table": {"action_type": "assign_voice_folder", "language": "jpn",
                                                    "rows": [{"if": [text("Original")], "then": "Original"},
                                                             {"if": [text("AnimeSpace")], "then": both}]}}])
    assert process_title(defaulted, "D:Sound/Original | 01.mka")["extracted"] == {"voice_folder": "Original", "language": "jpn"}
    assert process_title(defaulted, "D:Sound/[AnimeSpace Team] | 01.mka")["extracted"] == {"voice_folder": "AnimeSpace Team",
                                                                                          "language": "rus"}
