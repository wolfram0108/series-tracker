"""The numbers the constructor settles beyond one line: the default season and the first part."""
import pytest

from core.contracts import RuleLine, RuleResult
from modules.rules.numbering import DEFAULT_SEASON, season_of, settle_parts


def line(key, player="vk", voice="V") -> RuleLine:
    return RuleLine(key=key, text=key, player=player, voice=voice)


def answer(**fields) -> RuleResult:
    return RuleResult(**fields)


def parts(lines, results):
    settled = settle_parts(lines, results)
    return [(each.key, settled[each.key].part) for each in lines]


@pytest.mark.parametrize("series, extracted, expected", [
    (None, None, DEFAULT_SEASON), ("", None, DEFAULT_SEASON), ("Сезон 4", None, 4), (2, None, 2),
    (None, 3, 3), ("Сезон 4", 0, 0), (None, 0, 0)])
def test_the_season_is_the_extracted_one_then_the_card_s_then_the_default(series, extracted, expected):
    assert season_of(series, extracted) == expected
    assert DEFAULT_SEASON == 1


def test_a_single_without_a_part_takes_part_one_where_the_slot_has_parts():
    lines = [line("a"), line("b"), line("c")]
    results = {"a": answer(season=1, episode=1, part=2), "b": answer(season=1, episode=1),
               "c": answer(season=1, episode=2)}
    assert parts(lines, results) == [("a", 2), ("b", 1), ("c", None)]


def test_a_slot_is_a_player_a_voice_a_season_and_an_episode():
    base = {"season": 1, "episode": 1}
    lines = [line("p"), line("same", voice="v "), line("voice", voice="W"), line("player", player="allvideo"),
             line("season"), line("episode")]
    results = {"p": answer(**base, part=3), "same": answer(**base), "voice": answer(**base), "player": answer(**base),
               "season": answer(season=2, episode=1), "episode": answer(season=1, episode=2)}
    assert parts(lines, results) == [("p", 3), ("same", 1), ("voice", None), ("player", None), ("season", None),
                                     ("episode", None)]


def test_the_voice_the_constructor_assigned_beats_the_voice_of_the_table():
    lines = [line("a", voice="X"), line("b", voice="Y")]
    results = {"a": answer(season=1, episode=1, part=2, voiceover="AniStar"), "b": answer(season=1, episode=1, voiceover="anistar")}
    assert parts(lines, results) == [("a", 2), ("b", 1)]


def test_a_compilation_neither_makes_a_slot_nor_takes_part_one():
    lines = [line("c"), line("c2"), line("s")]
    results = {"c": answer(season=1, start=1, end=3), "c2": answer(season=1, start=1, end=3, part=2),
               "s": answer(season=1, episode=1)}
    assert parts(lines, results) == [("c", None), ("c2", 2), ("s", None)]


def test_a_line_without_a_number_or_excluded_takes_no_part():
    lines = [line("a"), line("x"), line("t")]
    results = {"a": answer(season=1, episode=1, part=2), "x": answer(season=1), "t": answer(excluded=True)}
    assert parts(lines, results) == [("a", 2), ("x", None), ("t", None)]


def test_the_answers_given_are_left_as_they_were():
    lines = [line("a"), line("b")]
    results = {"a": answer(season=1, episode=1, part=2), "b": answer(season=1, episode=1)}
    settle_parts(lines, results)
    assert results["b"].part is None
