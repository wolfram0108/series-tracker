from __future__ import annotations

from modules.sources.video_providers.players import PLAYER_ORDER
from modules.sources.video_providers.ruchime_address import (
    PLAYERS, Choice, address_of, choice_of, parse_vibix_link, vibix_link)

PAGE = "https://1.ruchime.org/release/test"


def test_the_players_of_the_address_are_the_players_of_the_provider():
    assert PLAYERS == tuple(adapter.name for adapter in PLAYER_ORDER)


def test_an_address_without_a_query_chooses_nothing():
    assert choice_of(PAGE) == Choice()
    assert address_of(PAGE, Choice()) == PAGE


def test_the_choice_is_read_from_the_query():
    choice = choice_of(f"{PAGE}?vibix=2&off=allvideo,vk")
    assert choice == Choice(2, frozenset({"vk", "allvideo"}))


def test_nonsense_in_the_query_is_ignored():
    assert choice_of(f"{PAGE}?vibix=0&off=nothing") == Choice()
    assert choice_of(f"{PAGE}?vibix=two") == Choice()


def test_the_address_lists_players_in_their_fixed_order():
    choice = Choice(2, frozenset({"vibix", "vk"}))
    assert address_of(PAGE, choice) == f"{PAGE}?vibix=2&off=vk,vibix"


def test_an_address_survives_the_round_trip():
    for choice in (Choice(), Choice(1), Choice(None, frozenset({"allvideo"})), Choice(3, frozenset(PLAYERS))):
        assert choice_of(address_of(PAGE, choice)) == choice


def test_toggling_a_player_twice_changes_nothing():
    choice = Choice(2)
    assert choice.toggled("vk").off == {"vk"}
    assert choice.toggled("vk").toggled("vk") == choice


def test_a_season_is_chosen_without_touching_the_disabled_players():
    assert Choice(None, frozenset({"vk"})).with_season(2) == Choice(2, frozenset({"vk"}))


def test_a_vibix_link_names_the_season_and_the_episode():
    link = vibix_link(f"{PAGE}/episode-5", 2, 5)
    assert link == f"{PAGE}/episode-5#vibix=2/5"
    assert parse_vibix_link(link) == (2, 5)


def test_a_link_of_another_kind_is_not_a_vibix_link():
    assert parse_vibix_link(f"{PAGE}/episode-5") is None
    assert parse_vibix_link(f"{PAGE}/episode-5#cvh=1") is None
