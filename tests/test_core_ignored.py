"""The memory of switched-off episodes and the rule that reads it, for every kind of source."""
import pytest

from core.ignored import covered, episodes_of, parse_memory


def test_the_memory_keeps_seasons_with_numbers_and_drops_the_rest():
    assert parse_memory({"4": [1, 2], "bad": [3], "5": [], "6": [None, 7]}) == {4: {1, 2}, 6: {7}}
    assert parse_memory(None) == {} and parse_memory([1, 2]) == {}


@pytest.mark.parametrize("meta, expected", [
    ({"episode": 3}, {3}),
    ({"start": 2, "end": 4}, {2, 3, 4}),
    ({"start": 5}, {5}),
    ({"start": 5, "end": 4}, {5}),
    ({}, set()),
    (None, set()),
])
def test_the_episodes_a_file_covers(meta, expected):
    assert episodes_of(meta) == expected


def test_an_episode_is_covered_when_its_season_remembers_it():
    memory = {1: {2, 3}}
    assert covered(1, {2}, memory) and covered(1, {2, 3}, memory)
    assert not covered(1, {1}, memory) and not covered(2, {2}, memory)


def test_a_compilation_is_covered_only_when_none_of_its_episodes_is_wanted():
    assert not covered(1, {2, 3, 4}, {1: {2, 3}})
    assert covered(1, {2, 3}, {1: {2, 3, 4}})


def test_nothing_is_covered_without_a_season_or_without_episodes():
    assert not covered(None, {1}, {1: {1}}) and not covered(1, set(), {1: {1}})
