"""The memory of switched-off episodes and the rule that reads it, for every kind of source."""

from core.ignored import parse_memory


def test_the_memory_keeps_seasons_with_numbers_and_drops_the_rest():
    assert parse_memory({"4": [1, 2], "bad": [3], "5": [], "6": [None, 7]}) == {4: {1, 2}, 6: {7}}
    assert parse_memory(None) == {} and parse_memory([1, 2]) == {}
