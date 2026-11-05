"""The projection of a series: rows of all its sources with the numbers the constructor gave, voice-over groups and
unknown voice-overs, the summaries a window shows before any number exists."""
import re

import pytest

from core.contracts import (CompositionRow, Exclusion, PlayerSummary, ReleaseTable, RowState, RowVoice, RuleLine, RuleResult,
                            RulesApplyReply, SeriesSource, SourceStatus, SourceType, TableRow, line_of)
from core.voice_names import fold_name
from sources_support import json_of, text_of
from modules.composition.project import (UNKNOWN_VOICE_LABEL, SourceInput, group_renames, layout_episodes, lines_for,
                                         project, selected_rows, touched_episodes, voice_name_of, voice_names)

def source(id=1, position=0, profile=7, **fields) -> SeriesSource:
    return SeriesSource(id=id, series_id=5, position=position, url=f"https://example.org/{id}",
                        source_type=SourceType.WEB_VIDEO, parser_profile_id=profile, **fields)


def row(key, text="", player="vk", voice="", kind="", group="", heights=(720,), state=RowState.PLAYABLE, label="",
        site_fields=None) -> TableRow:
    return TableRow(key=key, player=player, player_label=label, text=text, group=group,
                    voice=RowVoice(name=voice, kind=kind), heights=list(heights), state=state,
                    site_fields=site_fields or {})


def table(*rows, names=("Название",), site="ruchime") -> ReleaseTable:
    return ReleaseTable(url="https://example.org/page", site=site, names=list(names), read_at="2026-11-01T10:00:00Z",
                        complete=True, rows=list(rows))


def reply(results: dict) -> RulesApplyReply:
    return RulesApplyReply(results={key: RuleResult(**fields) for key, fields in results.items()})


def item(src: SeriesSource, tbl: ReleaseTable | None = None, results: dict | None = None,
         failure: str | None = None) -> SourceInput:
    return SourceInput(src, tbl, reply(results) if results is not None else None, failure)


def one(inp: SourceInput, groups=None, unknown=None) -> list[CompositionRow]:
    return project([inp], groups or {}, unknown or {}).rows


# ---- numbers: only from the constructor, rows kept as given ------------------------------------------------------


def test_a_single_episode_is_a_range_of_one_and_numbers_are_copied_from_the_answer():
    out = one(item(source(), table(row("a", "Серия 1", voice="Ruchime")), {"a": {"season": 2, "episode": 1}}))
    only = out[0]
    assert (only.season, only.episode_start, only.episode_end, only.part) == (2, 1, 1, None)
    assert not only.excluded and only.exclusion is None and not only.is_compilation
    assert (only.series_id, only.source_id, only.row_key) == (5, 1, "a")


def test_the_row_and_its_site_fields_never_give_a_number():
    marked = row("a", "Серия 7", group="Сезон 3", site_fields={"number": 99, "season": 9, "compilation_to": 12})
    only = one(item(source(), table(marked), {"a": {"episode": 7}}))[0]
    # Neither the season in the site fields (9) nor the one in the text of the group (3): the answer names none.
    assert (only.season, only.episode_start, only.episode_end) == (None, 7, 7)


def test_the_season_is_copied_from_the_answer_and_never_made_up():
    out = one(item(source(), table(row("a"), row("b"), row("c"), row("d")), {
        "a": {"episode": 1}, "b": {"episode": 2, "season": 3}, "c": {"episode": 3, "season": 0},
        "d": {"start": 4, "end": 5}}))
    assert [r.season for r in out] == [None, 3, 0, None]


def test_a_row_the_constructor_excluded_has_no_season():
    only = one(item(source(), table(row("t", "Трейлер")), {"t": {"excluded": True}}))[0]
    assert only.season is None


def test_a_compilation_stays_one_row_with_the_range_the_constructor_gave():
    tbl = table(row("c", "1-3 серия", player="kodik"), row("s", "4 серия", player="kodik"))
    out = one(item(source(), tbl, {"c": {"season": 1, "start": 1, "end": 3}, "s": {"season": 1, "episode": 4}}))
    assert [(r.row_key, r.episode_start, r.episode_end, r.is_compilation) for r in out] == [
        ("c", 1, 3, True), ("s", 4, 4, False)]


def test_a_single_row_named_by_a_compilation_text_stays_a_single_episode():
    # CVH writes "110 episode" on a file of episodes 109-110: the text is all the constructor can read.
    only = one(item(source(), table(row("x", "110 серия", player="cvh")), {"x": {"season": 1, "episode": 110}}))[0]
    assert (only.episode_start, only.episode_end, only.is_compilation) == (110, 110, False)


def test_a_single_episode_wins_over_a_range_and_a_range_without_an_end_ends_where_it_starts():
    out = one(item(source(), table(row("a"), row("b")),
                   {"a": {"episode": 5, "start": 1, "end": 3}, "b": {"start": 4}}))
    assert [(r.episode_start, r.episode_end) for r in out] == [(5, 5), (4, 4)]


def test_a_row_the_constructor_excluded_is_kept_with_the_reason_and_without_numbers():
    only = one(item(source(), table(row("t", "Трейлер")), {"t": {"excluded": True, "episode": 5, "season": 1}}))[0]
    assert only.excluded and only.exclusion == "rule"
    assert (only.season, only.episode_start, only.episode_end, only.part) == (None, None, None, None)


@pytest.mark.parametrize("answer", [{}, {"season": 2}, {"part": 1}])
def test_a_row_without_an_episode_number_is_kept_as_excluded_for_the_lack_of_it(answer):
    only = one(item(source(), table(row("a", "Спецвыпуск")), {"a": answer}))[0]
    assert only.excluded and only.exclusion == "no_number"
    assert (only.episode_start, only.episode_end) == (None, None)
    assert (only.season, only.part) == (answer.get("season"), answer.get("part"))


def test_a_row_the_constructor_gave_no_answer_for_has_no_number():
    out = one(item(source(), table(row("a"), row("b")), {"a": {"episode": 1}}))
    assert [(r.row_key, r.excluded, r.exclusion) for r in out] == [("a", False, None), ("b", True, "no_number")]


def test_a_row_carries_what_the_table_gave_about_itself():
    given = TableRow(key="k", player="kodik", player_label="Kodik", group="1 сезон", text="1-3 серия",
                     voice=RowVoice(name="Animy", kind="voice"), heights=[360, 720], state=RowState.NOT_OBTAINED,
                     published="2026-10-01", link="https://example.org/stream")
    only = one(item(source(), table(given), {"k": {"start": 1, "end": 3}}))[0]
    assert (only.player, only.player_label, only.group, only.text, only.heights, only.state, only.published,
            only.link) == ("kodik", "Kodik", "1 сезон", "1-3 серия", [360, 720], RowState.NOT_OBTAINED, "2026-10-01",
                           "https://example.org/stream")
    assert only.height == 720


# ---- a source without a profile has no rows ----------------------------------------------------------------------


def test_a_source_without_a_profile_is_only_in_the_summary():
    bare = item(source(id=1, position=0, profile=None), table(row("a", "Серия 1"), row("b", "Серия 2", player="allvideo")))
    ready = item(source(id=2, position=1), table(row("c", "Серия 1")), {"c": {"episode": 1}})
    out = project([bare, ready], {}, {})
    assert [r.source_id for r in out.rows] == [2]
    assert [(s.source_id, s.status) for s in out.sources] == [(1, SourceStatus.NO_PROFILE), (2, SourceStatus.READY)]
    assert [(p.player, p.rows) for p in out.sources[0].players] == [("vk", 1), ("allvideo", 1)]


def test_a_stale_answer_does_not_give_numbers_to_a_source_whose_profile_was_taken_off():
    stale = item(source(profile=None), table(row("a", "Серия 1")), {"a": {"episode": 1}})
    out = project([stale], {}, {})
    assert out.rows == [] and out.sources[0].status == SourceStatus.NO_PROFILE


def test_a_source_the_page_of_which_is_not_read_yet_waits():
    out = project([item(source())], {}, {})
    summary = out.sources[0]
    assert out.rows == []
    assert (summary.source_id, summary.url, summary.status) == (1, "https://example.org/1", SourceStatus.WAITING)
    assert (summary.players, summary.read_at, summary.reason) == ([], None, None)


def test_a_source_with_a_profile_waits_for_the_answer_of_the_constructor():
    out = project([item(source(), table(row("a")))], {}, {})
    assert out.rows == [] and out.sources[0].status == SourceStatus.WAITING
    assert out.sources[0].players[0].rows == 1


def test_a_failed_read_keeps_the_last_complete_table_in_force_and_names_the_reason():
    why = '{"code":"read_failed"}'
    out = project([item(source(), table(row("a", "Серия 1")), {"a": {"episode": 1}}, failure=why)], {}, {})
    assert [r.row_key for r in out.rows] == ["a"]
    assert (out.sources[0].status, out.sources[0].reason) == (SourceStatus.FAILED, why)
    assert out.sources[0].read_at == "2026-11-01T10:00:00Z"


def test_a_failed_read_without_any_table_gives_the_reason_and_nothing_else():
    out = project([item(source(), failure='{"code":"read_failed"}')], {}, {})
    assert out.rows == [] and out.sources[0].players == []
    assert (out.sources[0].status, out.sources[0].reason) == (SourceStatus.FAILED, '{"code":"read_failed"}')


def test_a_ready_source_has_no_reason():
    out = project([item(source(), table(row("a")), {"a": {"episode": 1}})], {}, {})
    assert (out.sources[0].status, out.sources[0].reason) == (SourceStatus.READY, None)


def test_the_summary_counts_players_rows_and_seasons_without_a_number():
    tbl = table(
        row("1", "Серия 1", player="vibix", label="Vibix", group="Сезон 1", heights=(480, 1080)),
        row("2", "Серия 2", player="vibix", label="Vibix", group="Сезон 2", heights=(720,)),
        row("3", "Серия 3", player="vibix", label="", group="Сезон 2", heights=(720,)),
        row("4", "Серия 1", player="vk", label="VK", heights=(2160, 1080)),
        row("5", "Серия 2", player="vk", label="VK", heights=(4320,), state=RowState.ABSENT),
        row("6", "Серия 3", player="vk", label="VK", heights=(), state=RowState.NOT_OBTAINED))
    players = project([item(source(profile=None), tbl)], {}, {}).sources[0].players
    assert players == [PlayerSummary(player="vibix", player_label="Vibix", rows=3, groups=2,
                                     group_names=["Сезон 1", "Сезон 2"]),
                       PlayerSummary(player="vk", player_label="VK", rows=3, groups=0)]


def test_the_summary_of_a_player_holds_no_voice_and_no_resolution_that_a_window_could_draw_beside_the_layout():
    assert set(PlayerSummary.model_fields) == {"player", "player_label", "rows", "groups", "group_names"}


# ---- order -------------------------------------------------------------------------------------------------------


def test_rows_follow_the_sources_by_position_and_each_source_follows_its_table():
    late = item(source(id=1, position=2), table(row("l2", "Серия 2"), row("l1", "Серия 1")),
                {"l1": {"episode": 1}, "l2": {"episode": 2}})
    early = item(source(id=3, position=1), table(row("e1", "Серия 1")), {"e1": {"episode": 1}})
    tie = item(source(id=2, position=1), table(row("t1", "Серия 1")), {"t1": {"episode": 1}})
    out = project([late, early, tie], {}, {})
    assert [(r.source_id, r.row_key) for r in out.rows] == [(2, "t1"), (3, "e1"), (1, "l2"), (1, "l1")]
    assert [s.source_id for s in out.sources] == [2, 3, 1]


# ---- the choice of the source ------------------------------------------------------------------------------------


def catalog_table():
    return table(*[row(f"v{season}{number}", f"Серия {number}", player="vibix", group=f"Сезон {season}")
                   for season in (1, 2) for number in (1, 2)], row("k1", "1 серия", player="kodik", group="1 сезон"))


@pytest.mark.parametrize("chosen, keys", [
    (None, ["v11", "v12", "v21", "v22", "k1"]),
    (1, ["v11", "v12", "k1"]),
    (2, ["v21", "v22", "k1"]),
    (3, ["k1"]),
    (0, ["v11", "v12", "v21", "v22", "k1"]),
])
def test_only_the_chosen_season_of_the_catalog_player_is_in_play(chosen, keys):
    assert [r.key for r in selected_rows(source(vibix_season=chosen), catalog_table())] == keys


def test_a_switched_off_player_is_not_asked_about_and_is_in_the_layout_as_left_out_by_the_source():
    src = source(players_off=["kodik"])
    tbl = catalog_table()
    assert [line.key for line in lines_for(src, tbl)] == ["v11", "v12", "v21", "v22"]
    results = {key: {"episode": 1} for key in ("v11", "v12", "v21", "v22", "k1")}
    out = project([item(src, tbl, results)], {}, {})
    assert [(r.row_key, r.excluded, r.exclusion) for r in out.rows] == [
        ("v11", False, None), ("v12", False, None), ("v21", False, None), ("v22", False, None),
        ("k1", True, Exclusion.SOURCE)]
    [left] = [r for r in out.rows if r.excluded]
    assert left.left_out and left.decided and (left.season, left.episode_start, left.voice_label) == (None, None, "")
    # A row the source leaves out names no voice-over: it takes no number of an unknown one.
    assert (left.voice_group, left.voice_unknown) == (None, None)
    alone = project([item(source(), table(*[r for r in tbl.rows if r.player == "kodik"]), results)], {}, {})
    assert project([item(src, table(*[r for r in tbl.rows if r.player == "kodik"]), results)], {}, {}).unknown_voices == {}
    assert alone.unknown_voices != {}  # the same row in play does take one
    assert [p.player for p in out.sources[0].players] == ["vibix", "kodik"]


@pytest.mark.parametrize("chosen", [None, 0, 1, 2, 3])
@pytest.mark.parametrize("off", [(), ("kodik",), ("vibix",), ("vibix", "kodik")])
def test_every_row_of_the_table_is_in_the_layout_once_and_only_the_choice_of_the_source_leaves_it_out(chosen, off):
    src = source(vibix_season=chosen, players_off=list(off))
    tbl = catalog_table()
    out = project([item(src, tbl, {key: {"episode": 1} for key in ("v11", "v12", "v21", "v22", "k1")})], {}, {})
    in_play = {r.key for r in selected_rows(src, tbl)}
    assert sorted(r.row_key for r in out.rows) == sorted(r.key for r in tbl.rows)
    assert {r.row_key for r in out.rows if r.exclusion == Exclusion.SOURCE} == {r.key for r in tbl.rows} - in_play
    assert {r.row_key for r in out.rows if not r.excluded} == in_play


def test_a_source_without_a_profile_or_an_answer_still_has_no_rows():
    tbl = catalog_table()
    assert project([item(source(profile=None, players_off=["kodik"]), tbl, {})], {}, {}).rows == []
    assert project([item(source(players_off=["kodik"]), tbl)], {}, {}).rows == []


# ---- the lines the constructor is asked about --------------------------------------------------------------------


def test_the_lines_are_those_of_line_of_with_the_key_of_the_row():
    tbl = table(row("a", "Серия 1", label="VK", heights=(1080,)), row("b", "Серия 2", state=RowState.ABSENT, label="VK"),
                names=("Асура: Безумный король 2",))
    assert lines_for(source(), tbl) == [
        RuleLine(key=r.key, text=line_of(tbl, r), player=r.player, voice=r.voice.name) for r in tbl.rows]
    assert [line.text for line in lines_for(source(), tbl)] == [
        "PID:example.org | P:VK | Асура: Безумный король 2 | Серия 1 | R:1080p", "PID:example.org | P:VK | Асура: Безумный король 2 | Серия 2"]


def test_the_lines_and_the_layout_of_real_values_of_ruchime_dunhuatv_kodik_and_cvh():
    # Values as the sites give them: a ruchime row of a Vibix season, a Kodik compilation text of three episodes, a CVH row
    # of AniStar with its multi-voice type.
    ruchime = table(
        row("2:1", "Серия 1", player="vibix", label="Vibix", voice="Ruchime", group="Сезон 2", heights=(480, 720, 1080)),
        row("2:2", "Серия 2", player="vibix", label="Vibix", voice="Ruchime", group="Сезон 2", heights=(480, 720, 1080)),
        names=("Асура: Безумный король 2",))
    dunhuatv = table(
        row("1468548", "1-3 серия", player="kodik", label="Kodik", voice="Студия Константина Исаева", kind="voice",
            group="1 сезон", heights=(360, 480, 720)),
        row("0191878f-7db0-7639-98c5-775f13d7dcce", "5 серия", player="cvh", label="CVH", voice="AniStar",
            kind="Многоголосый", group="1 сезон", heights=(480, 1080)),
        names=("Практикуя ци сто тысяч лет",), site="dunhuatv")
    first, second = source(id=1, position=0), source(id=2, position=1, profile=8)
    assert [line.text for line in lines_for(first, ruchime)] == [
        "PID:example.org | P:Vibix | Асура: Безумный король 2 | Серия 1 | G:Сезон 2 | V:Ruchime | R:1080p",
        "PID:example.org | P:Vibix | Асура: Безумный король 2 | Серия 2 | G:Сезон 2 | V:Ruchime | R:1080p"]
    assert [line.text for line in lines_for(second, dunhuatv)] == [
        "PID:example.org | P:Kodik | Практикуя ци сто тысяч лет | 1-3 серия | G:1 сезон | V:Студия Константина Исаева | VT:voice | R:720p",
        "PID:example.org | P:CVH | Практикуя ци сто тысяч лет | 5 серия | G:1 сезон | V:AniStar | VT:Многоголосый | R:1080p"]
    out = project(
        [item(first, ruchime, {"2:1": {"season": 2, "episode": 1}, "2:2": {"season": 2, "episode": 2}}),
         item(second, dunhuatv, {"1468548": {"season": 1, "start": 1, "end": 3},
                                 "0191878f-7db0-7639-98c5-775f13d7dcce": {"season": 1, "episode": 5}})],
        {"Ruchime": "Ruchime", "AniStar": "AniStar"}, {})
    assert [(r.source_id, r.season, r.episode_start, r.episode_end, r.voice_label) for r in out.rows] == [
        (1, 2, 1, 1, "Ruchime"), (1, 2, 2, 2, "Ruchime"),
        (2, 1, 1, 3, "Неизвестная озвучка 1"), (2, 1, 5, 5, "AniStar")]


# ---- voice-over groups and unknown voice-overs -------------------------------------------------------------------


def test_a_voice_over_in_a_group_is_worded_by_the_group_and_takes_no_number():
    out = project([item(source(), table(row("a", voice="AniStar")), {"a": {"episode": 1}})], {"AniStar": "Стар"}, {})
    only = out.rows[0]
    assert (only.voice_group, only.voice_label, only.voice_unknown) == ("Стар", "Стар", None)
    assert out.unknown_voices == {}


def test_unknown_voice_overs_are_numbered_in_order_of_appearance_and_spellings_are_one_name():
    names = ["AniMy / RuChiMe", "AniStar", " ani star ", "ANIMY/RUCHIME", "", "  ", "Ani-Star"]
    tbl = table(*[row(str(index), voice=name) for index, name in enumerate(names)])
    out = project([item(source(), tbl, {str(index): {"episode": index} for index in range(len(names))})], {}, {})
    assert [r.voice_unknown for r in out.rows] == [1, 2, 2, 1, 3, 3, 2]
    assert [r.voice_label for r in out.rows][:3] == ["Неизвестная озвучка 1", "Неизвестная озвучка 2",
                                                    "Неизвестная озвучка 2"]
    assert all(r.voice_group is None for r in out.rows)
    assert out.unknown_voices == {"animyruchime": 1, "anistar": 2, "": 3}


def test_the_label_of_an_unknown_voice_comes_from_one_template():
    only = one(item(source(), table(row("a", voice="X")), {"a": {"episode": 1}}))[0]
    assert only.voice_label == UNKNOWN_VOICE_LABEL.format(number=1)


def test_the_same_name_has_the_same_number_in_every_source_and_a_new_name_takes_the_next_one():
    one_src = item(source(id=1, position=0), table(row("a", voice="Animy"), row("b", voice="AniStar")),
                   {"a": {"episode": 1}, "b": {"episode": 2}})
    other = item(source(id=2, position=1), table(row("c", voice="anistar"), row("d", voice="Студия")),
                 {"c": {"episode": 1}, "d": {"episode": 2}})
    out = project([other, one_src], {}, {})
    assert [(r.row_key, r.voice_unknown) for r in out.rows] == [("a", 1), ("b", 2), ("c", 2), ("d", 3)]


def test_numbers_given_earlier_hold_whatever_the_order_of_the_rows_is_now():
    tbl = table(row("a", voice="Новая"), row("b", voice="AniStar"), row("c", voice="Animy"))
    inp = item(source(), tbl, {"a": {"episode": 1}, "b": {"episode": 2}, "c": {"episode": 3}})
    out = project([inp], {}, {"animy": 1, "anistar": 2})
    assert [(r.row_key, r.voice_unknown) for r in out.rows] == [("a", 3), ("b", 2), ("c", 1)]
    assert out.unknown_voices == {"animy": 1, "anistar": 2, "новая": 3}


def test_a_number_is_never_given_to_another_name_even_when_the_old_one_has_left():
    out = project([item(source(), table(row("a", voice="Новая")), {"a": {"episode": 1}})], {}, {"ушла": 4})
    assert out.rows[0].voice_unknown == 5
    assert out.unknown_voices == {"ушла": 4, "новая": 5}


def test_a_name_keeps_its_number_through_a_group_that_comes_and_goes():
    inp = item(source(), table(row("a", voice="AniStar")), {"a": {"episode": 1}})
    before = project([inp], {}, {})
    grouped = project([inp], {"AniStar": "Стар"}, before.unknown_voices)
    after = project([inp], {}, grouped.unknown_voices)
    assert before.rows[0].voice_unknown == 1
    assert (grouped.rows[0].voice_group, grouped.rows[0].voice_unknown) == ("Стар", None)
    assert after.rows[0].voice_unknown == 1 and after.rows == before.rows


def test_the_name_the_constructor_assigned_is_the_one_that_is_looked_up_and_numbered():
    inp = item(source(), table(row("a", voice="AniStar", kind="Многоголосый"), row("b", voice="")),
               {"a": {"episode": 1, "voiceover": "Мой дубляж"}, "b": {"episode": 2, "voiceover": "Мой дубляж"}})
    named = project([inp], {"AniStar": "Стар", "Мой дубляж": "Моя"}, {})
    assert [(r.voice_group, r.voice_label) for r in named.rows] == [("Моя", "Моя"), ("Моя", "Моя")]
    unnamed = project([inp], {"AniStar": "Стар"}, {})
    assert [(r.voice_group, r.voice_unknown) for r in unnamed.rows] == [(None, 1), (None, 1)]
    assert unnamed.unknown_voices == {"мойдубляж": 1}


@pytest.mark.parametrize("assigned", [None, "", "   "])
def test_a_name_that_says_nothing_does_not_replace_the_one_of_the_player(assigned):
    only = one(item(source(), table(row("a", voice="AniStar")), {"a": {"episode": 1, "voiceover": assigned}}),
               groups={"AniStar": "Стар"})[0]
    assert only.voice_group == "Стар"


def test_a_row_with_no_voice_at_all_is_the_voice_with_the_empty_name():
    out = project([item(source(), table(row("a"), row("b", voice="  ")), {"a": {"episode": 1}, "b": {"episode": 2}})],
                  {}, {})
    assert [r.voice_unknown for r in out.rows] == [1, 1] and out.unknown_voices == {"": 1}


def test_an_excluded_row_names_its_voice_like_any_other():
    out = project([item(source(), table(row("t", "Трейлер", voice="Чужая")), {"t": {"excluded": True}})], {}, {})
    assert (out.rows[0].voice_unknown, out.unknown_voices) == (1, {"чужая": 1})


def test_the_names_to_ask_the_groups_about_are_those_of_the_rows_the_constructors_answered():
    first = item(source(id=1, position=0, players_off=["allvideo"]),
                 table(row("a", voice="AniStar"), row("b", voice="Animy", player="allvideo"), row("c", voice=""),
                       row("d", voice="AniStar", kind="x"), row("e", voice="Table name")),
                 {"a": {"episode": 1}, "b": {"episode": 1}, "c": {"episode": 2}, "d": {"episode": 3},
                  "e": {"episode": 4, "voiceover": "Assigned"}})
    bare = item(source(id=2, position=1, profile=None), table(row("f", voice="Никто")),
                {"f": {"episode": 1, "voiceover": "Stale"}})
    waiting = item(source(id=3, position=2))
    assert voice_names([waiting, bare, first]) == ["AniStar", "Assigned"]


def test_a_row_carries_the_name_of_its_voice_over_as_it_came_and_the_name_the_constructor_assigned_wins():
    tbl = table(row("a", voice="AniStar"), row("b", voice="AniStar"), row("c"), row("d", voice="  "))
    out = project([item(source(), tbl, {
        "a": {"episode": 1}, "b": {"episode": 2, "voiceover": "Мой дубляж"}, "c": {"episode": 3, "voiceover": "VK-озвучка"},
        "d": {"episode": 4, "voiceover": "  "}})], {"AniStar": "Стар"}, {}).rows
    assert [(r.voice_name, r.voice_group, r.voice_label) for r in out] == [
        ("AniStar", "Стар", "Стар"), ("Мой дубляж", None, "Неизвестная озвучка 1"),
        ("VK-озвучка", None, "Неизвестная озвучка 2"), ("  ", None, "Неизвестная озвучка 3")]


def test_the_name_of_a_voice_is_the_assigned_one_or_the_one_of_the_row():
    plain = row("a", voice="AniStar")
    assert voice_name_of(plain, None) == "AniStar"
    assert voice_name_of(plain, RuleResult()) == "AniStar"
    assert voice_name_of(plain, RuleResult(voiceover="Мой")) == "Мой"
    assert voice_name_of(row("b"), RuleResult(voiceover="Мой")) == "Мой"


def test_the_name_the_user_gave_a_source_is_the_name_of_the_rows_that_have_none_and_of_no_other():
    nameless, named = row("a"), row("b", voice="AniStar")
    assert voice_name_of(nameless, None, "Мой дубляж") == "Мой дубляж"
    assert voice_name_of(nameless, RuleResult(voiceover="Ассистент"), "Мой дубляж") == "Ассистент"
    assert voice_name_of(named, None, "Мой дубляж") == "AniStar"
    assert voice_name_of(nameless, None, "  ") == "" and voice_name_of(nameless, None) == ""


def test_a_source_with_a_name_for_its_voice_has_the_name_in_its_rows_its_lines_and_its_groups():
    tbl = table(row("a", "Серия 1"), row("b", "Серия 2", voice="Animy"))
    one = item(source(voice_name="AniStar"), tbl, {"a": {"episode": 1}, "b": {"episode": 2}})
    out = project([one], {"AniStar": "Стар"}, {})
    assert [(r.voice_name, r.voice_label) for r in out.rows] == [("AniStar", "Стар"), ("Animy", "Неизвестная озвучка 1")]
    assert [line.voice for line in lines_for(one.source, tbl)] == ["AniStar", "Animy"]
    assert voice_names([one]) == ["AniStar", "Animy"]


def test_names_are_the_same_name_without_case_spaces_and_punctuation():
    assert fold_name(" Red Head-Sound ") == fold_name("redheadsound") == "redheadsound"
    assert fold_name("") == fold_name("  ") == ""


# ---- parts: copied from the answer, never made ---------------------------------------------------------------------


def test_a_part_is_copied_from_the_answer_and_a_single_without_one_is_left_without():
    tbl = table(row("a", "1 серия часть 2", voice="V"), row("b", "1 серия", voice="V"))
    out = one(item(source(), tbl, {"a": {"season": 1, "episode": 1, "part": 2}, "b": {"season": 1, "episode": 1}}))
    assert [(r.row_key, r.part) for r in out] == [("a", 2), ("b", None)]


def test_the_line_asked_names_the_player_and_the_voice_of_its_row():
    tbl = table(row("a", "1 серия", voice="V", player="kodik"))
    assert [(line.player, line.voice) for line in lines_for(source(), tbl)] == [("kodik", "V")]


# ---- purity ------------------------------------------------------------------------------------------------------


def busy_inputs():
    tbl = table(row("a", "1 серия часть 1", voice="Animy"), row("b", "1 серия", voice="Animy"),
                row("c", "2-3 серия", voice="Новая", player="kodik"), row("t", "Трейлер", voice=""),
                row("n", "Спец", voice="AniStar"))
    results = {"a": {"season": 1, "episode": 1, "part": 1}, "b": {"season": 1, "episode": 1},
               "c": {"season": 1, "start": 2, "end": 3}, "t": {"excluded": True}, "n": {}}
    return [item(source(id=2, position=1), tbl, results), item(source(id=1, position=0, profile=None), tbl)]


def test_equal_inputs_give_equal_projections_and_the_inputs_are_left_as_they_were():
    inputs, groups, unknown = busy_inputs(), {"Animy": "Ани"}, {"anistar": 4}
    snapshot = ([(i.source.model_dump(), i.table.model_dump() if i.table else None,
                  i.reply.model_dump() if i.reply else None) for i in inputs], dict(groups), dict(unknown))
    first = project(inputs, groups, unknown)
    second = project(list(reversed(inputs)), dict(groups), dict(unknown))
    assert first == second
    assert snapshot == ([(i.source.model_dump(), i.table.model_dump() if i.table else None,
                          i.reply.model_dump() if i.reply else None) for i in inputs], groups, unknown)
    assert first.unknown_voices == {"anistar": 4, "новая": 5, "": 6}
    again = project(inputs, groups, first.unknown_voices)
    assert (again.rows, again.unknown_voices) == (first.rows, first.unknown_voices)


# ---- the episodes of a layout and the episodes two layouts differ in --------------------------------------------------


def crow(key, season, start, end=None, *, source_id=1, excluded=False, **fields) -> CompositionRow:
    return CompositionRow(series_id=5, source_id=source_id, row_key=key, season=season, episode_start=start,
                          episode_end=end if end is not None else start, excluded=excluded, player="vk",
                          state=RowState.PLAYABLE, **fields)


def test_a_layout_covers_the_episodes_of_its_rows_and_a_compilation_covers_its_whole_range():
    rows = [crow("a", 1, 1), crow("b", 1, 2, 4), crow("c", 2, 1), crow("x", 1, 9, excluded=True),
            crow("n", None, 7), crow("r", 1, 6, 5)]
    assert layout_episodes(rows) == {(1, 1), (1, 2), (1, 3), (1, 4), (2, 1), (1, 6)}
    assert layout_episodes([]) == set()


def test_two_layouts_differ_in_the_episodes_of_the_rows_that_are_not_the_same_in_both():
    base = [crow("a", 1, 1), crow("b", 1, 2), crow("c", 1, 3, 5)]
    assert touched_episodes(base, [row.model_copy() for row in base]) == set()
    assert touched_episodes(base, [*base[:2], crow("c", 1, 3, 5, link="https://cdn.test/new")]) == {
        (1, 3), (1, 4), (1, 5)}
    assert touched_episodes(base, [crow("a", 1, 7), *base[1:]]) == {(1, 1), (1, 7)}
    assert touched_episodes(base, base[1:]) == {(1, 1)}
    assert touched_episodes(base[:2], base) == {(1, 3), (1, 4), (1, 5)}
    assert touched_episodes([], base) == {(1, 1), (1, 2), (1, 3), (1, 4), (1, 5)}


def test_rows_with_the_same_key_in_two_sources_are_two_rows():
    assert touched_episodes([crow("a", 1, 1, source_id=1), crow("a", 1, 2, source_id=2)],
                            [crow("a", 1, 1, source_id=1)]) == {(1, 2)}


def test_a_difference_that_names_no_episode_is_an_empty_set_and_one_that_cannot_be_named_is_none():
    base = [crow("a", 1, 1), crow("b", 1, 2)]
    assert touched_episodes(base, list(reversed(base))) == set()
    assert touched_episodes(base, [*base, crow("t", 1, 1, excluded=True, exclusion="rule")]) == set()
    assert touched_episodes(base, [*base, crow("n", None, 3)]) is None
    assert touched_episodes([crow("n", None, 3)], []) is None


# ---- the answers of the sites (synthetic, in the shape of the real ones: site_samples) -----------------------------------------------------------------------------------


def test_a_line_is_the_coded_fields_of_the_registry_in_their_order_and_a_field_with_no_value_is_left_out():
    def line(url: str, **fields) -> str:
        tbl = table(row("a", "Серия 1", player="vk", label="VK", **fields), names=("Тайтл ",)).model_copy(update={"url": url})
        return line_of(tbl, tbl.rows[0])

    assert line("https://vkvideo.ru/@pvashow|Тайтл|search") == "PID:vkvideo.ru | P:VK | Тайтл | Серия 1 | R:720p"
    assert line("https://dunhuatv.ru/anime/x", voice="ПВА ШОУ", kind="Многоголосый", group="3 сезон", heights=(480, 1080)) == (
        "PID:dunhuatv.ru | P:VK | Тайтл | Серия 1 | G:3 сезон | V:ПВА ШОУ | VT:Многоголосый | R:1080p")
    assert line("", state=RowState.ABSENT) == "P:VK | Тайтл | Серия 1"


def test_the_390_episodes_of_the_ruchime_list_give_390_lines_with_the_text_of_the_site():
    listing = json_of("ruchime__ALL_EPS.json")
    rows = [row(str(each["id"]), each["title"], player="vk", label="VK", voice="Ruchime", heights=(1080,))
            for each in listing]
    tbl = table(*rows, names=("Практикуя ци сто тысяч лет",))
    lines = lines_for(source(), tbl)
    assert len(lines) == len(listing) == 390 and len({line.key for line in lines}) == 390
    assert lines[1].key == "93451"
    assert lines[1].text == "PID:example.org | P:VK | Практикуя ци сто тысяч лет | Серия 2 | V:Ruchime | R:1080p"


def test_the_607_rows_of_cvh_become_a_layout_whose_unknown_voices_are_numbered_by_name_not_by_type():
    playlist = json_of("dunhuatv__cvh_playlist.json")["items"]
    rows = [row(each["cvhId"], f"{each['episode']} серия", player="cvh", label="CVH", voice=each.get("voiceStudio", ""),
                kind=each["voiceType"], group=f"{each['season']} сезон", heights=(1080,)) for each in playlist]
    answers = {each["cvhId"]: {"season": each["season"], "episode": each["episode"]} for each in playlist}
    out = project([item(source(), table(*rows, names=("Практикуя ци сто тысяч лет",), site="dunhuatv"), answers)], {}, {})
    assert len(out.rows) == len(playlist) == 607 and not [r for r in out.rows if r.excluded]
    by_studio = {}
    for each, projected in zip(playlist, out.rows):
        by_studio.setdefault(each.get("voiceStudio", ""), set()).add(projected.voice_unknown)
    assert all(len(numbers) == 1 for numbers in by_studio.values())
    assert sorted(next(iter(numbers)) for numbers in by_studio.values()) == [1, 2, 3]
    assert set(by_studio) == {"AniMy / RuChiMe", "AniStar", ""}
    assert out.unknown_voices[""] == next(iter(by_studio[""]))


def test_the_kodik_list_of_a_voice_over_keeps_its_compilations_as_ranges():
    html = text_of("dunhuatv__kodik_voice2.html")
    box = html.split('class="serial-series-box"', 1)[1].split("</select>", 1)[0]
    items = re.findall(r'data-id="(\d+)"[^>]*?data-title="([^"]*)"', box, re.DOTALL)
    rows = [row(key, title, player="kodik", label="Kodik", voice="Студия Константина Исаева", kind="voice",
                group="1 сезон", heights=(360, 480, 720)) for key, title in items]
    compilations = [(key, title) for key, title in items if re.fullmatch(r"\d+-\d+ серия", title)]
    assert compilations and compilations[0] == ("1468548", "1-3 серия")
    tbl = table(*rows, names=("Практикуя ци сто тысяч лет",), site="dunhuatv")
    texts = {line.key: line.text for line in lines_for(source(), tbl)}
    assert texts["1468548"] == ("PID:example.org | P:Kodik | Практикуя ци сто тысяч лет | 1-3 серия | G:1 сезон | "
                                "V:Студия Константина Исаева | VT:voice | R:720p")
    answers = {}
    for key, title in items:
        numbers = [int(number) for number in re.findall(r"\d+", title)]
        answers[key] = {"season": 1, "start": numbers[0], "end": numbers[-1]} if len(numbers) > 1 else {
            "season": 1, "episode": numbers[0]}
    out = project([item(source(), tbl, answers)], {}, {}).rows
    assert len(out) == len(items) and sum(r.is_compilation for r in out) == len(compilations)


def test_the_two_seasons_of_the_asura_vibix_catalog_are_told_apart_by_the_choice_of_the_source():
    playlist = json_of("vibix__asura-bezumnyi-korol-2__api_decoded.json")["data"]["playlist"]
    rows = [row(f"{position}:{number}", episode["title"], player="vibix", label="Vibix", voice="Ruchime",
                group=season["title"], heights=(480, 720, 1080))
            for position, season in enumerate(playlist, 1) for number, episode in enumerate(season["folder"], 1)]
    tbl = table(*rows, names=("Асура: Безумный король 2",))
    assert [len(selected_rows(source(vibix_season=chosen), tbl)) for chosen in (None, 1, 2, 3)] == [23, 9, 14, 0]
    second = lines_for(source(vibix_season=2), tbl)
    assert second[0].text == "PID:example.org | P:Vibix | Асура: Безумный король 2 | Серия 1 | G:Сезон 2 | V:Ruchime | R:1080p"


def test_the_summary_of_a_player_names_its_groups_in_the_order_of_the_table_once_each():
    rows = [row("a", "1", player="vibix", group="Сезон 2"), row("b", "2", player="vibix", group="Сезон 1"),
            row("c", "3", player="vibix", group="Сезон 2"), row("d", "4", player="vk")]
    summary = project([item(source(), table(*rows))], {}, {}).sources[0]
    by_player = {each.player: each for each in summary.players}
    assert by_player["vibix"].group_names == ["Сезон 2", "Сезон 1"] and by_player["vibix"].groups == 2
    assert by_player["vk"].group_names == []


def test_the_summary_carries_the_canonical_address_of_the_page_before_and_after_the_table_arrives():
    waiting = project([SourceInput(source(), None, None, None, "https://example.org/canonical")], {}, {}).sources[0]
    assert waiting.status == SourceStatus.WAITING and waiting.canonical_url == "https://example.org/canonical"
    read = project([item(source(), table(row("a", "1")))], {}, {}).sources[0]
    assert read.canonical_url == "https://example.org/page"



# ---- the groups of voice-overs that changed ------------------------------------------------------------------------------


def labelled(*pairs) -> list[CompositionRow]:
    """Rows a, b, c… with the voice-over name and the label given."""
    return [CompositionRow(series_id=5, source_id=1, row_key=key, voice_name=name, voice_label=label, player="vk",
                           state=RowState.PLAYABLE) for key, name, label in pairs]


def test_a_voice_over_that_kept_its_name_and_changed_its_group_is_a_rename_and_one_that_changed_its_name_is_not():
    before = labelled(("a", "AniStar", "Неизвестная озвучка 1"), ("b", "Animy", "Неизвестная озвучка 2"),
                      ("c", "Old", "Неизвестная озвучка 3"))
    after = labelled(("a", "AniStar", "Стар"), ("b", "Animy", "Неизвестная озвучка 2"), ("c", "New", "Неизвестная озвучка 4"))
    assert group_renames(before, after) == {"Неизвестная озвучка 1": "Стар"}


def test_a_label_that_still_stands_or_became_two_is_not_a_rename():
    before = labelled(("a", "AniStar", "Неизвестная озвучка 1"), ("b", "Animy", "Неизвестная озвучка 1"))
    split = labelled(("a", "AniStar", "Стар"), ("b", "Animy", "Ани"))
    assert group_renames(before, split) == {}
    partly = labelled(("a", "AniStar", "Стар"), ("b", "Animy", "Неизвестная озвучка 1"))
    assert group_renames(before, partly) == {}
    assert group_renames([], split) == {} and group_renames(before, []) == {}


def test_two_groups_dissolved_into_the_unknown_ones_are_renames_back():
    before = labelled(("a", "AniStar", "Стар"))
    after = labelled(("a", "AniStar", "Неизвестная озвучка 1"))
    assert group_renames(before, after) == {"Стар": "Неизвестная озвучка 1"}


def test_a_player_switched_off_or_on_renames_no_group():
    named = labelled(("a", "AniStar", "Стар"))
    left = [row.model_copy(update={"excluded": True, "exclusion": Exclusion.SOURCE, "voice_label": ""}) for row in named]
    assert group_renames(named, left) == {} and group_renames(left, named) == {}



# ---- the order of the voice-overs of a season (stage 11) ------------------------------------------------------------


def voiced(*pairs, season=1) -> list[CompositionRow]:
    """Rows of one season: (voice label, episode) in the order of the layout."""
    return [CompositionRow(series_id=5, source_id=1, row_key=f"r{i}", season=season, episode_start=episode,
                           episode_end=episode, voice_label=label, voice_name=label, player="vk", state=RowState.PLAYABLE)
            for i, (label, episode) in enumerate(pairs)]


def test_a_new_voice_takes_the_end_and_the_voices_there_keep_their_places_whatever_the_layout_brings():
    import itertools
    from modules.composition.project import season_orders
    for previous in itertools.permutations(["A", "B", "C"]):
        for present in itertools.permutations(["A", "B", "C", "D"], 3):
            order = season_orders({1: list(previous)}, voiced(*[(label, n) for n, label in enumerate(present, 1)]), {})[1]
            assert order[:3] == list(previous)  # nothing that was there moved
            assert order[3:] == [label for label in present if label not in previous]  # what is new takes the end


def test_the_default_order_is_the_order_of_the_layout_and_a_group_absent_now_keeps_its_place():
    from modules.composition.project import season_orders, season_voices
    rows = voiced(("Ruchime", 1), ("ПВА", 1), ("Ruchime", 2), ("Anistar", 3))
    assert season_orders({}, rows, {}) == {1: ["Ruchime", "ПВА", "Anistar"]}
    kept = season_orders({1: ["X", "ПВА", "Ruchime"]}, rows, {})
    assert kept == {1: ["X", "ПВА", "Ruchime", "Anistar"]}
    shown = season_voices(kept, rows)[0]
    assert [(voice.label, voice.episodes) for voice in shown.voices] == [("ПВА", "1"), ("Ruchime", "1–2"), ("Anistar", "3")]


def test_a_renamed_group_keeps_its_place_and_two_groups_made_one_keep_the_higher_place():
    from modules.composition.project import season_orders
    rows = voiced(("Стар", 1), ("B", 1), ("C", 1))
    assert season_orders({1: ["A", "B", "C"]}, rows, {"A": "Стар"}) == {1: ["Стар", "B", "C"]}
    merged = voiced(("G", 1), ("C", 1))
    assert season_orders({1: ["C", "A", "B"]}, merged, {"A": "G", "B": "G"}) == {1: ["C", "G"]}


def test_the_seasons_have_orders_of_their_own():
    from modules.composition.project import season_orders
    rows = voiced(("A", 1), ("B", 1)) + voiced(("B", 1), ("C", 1), season=2)
    assert season_orders({}, rows, {}) == {1: ["A", "B"], 2: ["B", "C"]}


@pytest.mark.parametrize("numbers, text", [([1], "1"), ([1, 2, 3], "1–3"), ([5, 1, 2, 9, 10], "1–2, 5, 9–10"), ([], "")])
def test_the_episodes_of_a_voice_are_worded_as_runs(numbers, text):
    from modules.composition.project import spans
    assert spans(numbers) == text


def test_the_user_orders_the_voices_shown_and_the_hidden_keep_their_places():
    from modules.composition.project import reorder
    assert reorder(["A", "X", "B", "C"], ["A", "B", "C"], ["C", "A", "B"]) == ["C", "X", "A", "B"]
    assert reorder(["A", "B"], ["A", "B"], ["A"]) is None
    assert reorder(["A", "B"], ["A", "B"], ["A", "A"]) is None
