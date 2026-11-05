"""Which video a link of a player is, as its host numbers it: one video shown by two pages is known as one; a link that
names a place on its page names no video."""
from modules.sources.players.allvideo import AllVideoPlayer
from modules.sources.players.animakima import AnimakimaPlayer
from modules.sources.players.cdnvideohub import CdnVideoHubPlayer
from modules.sources.players.kodik import KodikPlayer
from modules.sources.players.registry import PlayerRegistry
from modules.sources.players.vibix import VibixPlayer
from modules.sources.players.vk import VkPlayer


def test_a_video_is_the_same_whatever_page_shows_it():
    for player, one, other, video in [
        (CdnVideoHubPlayer, "https://dunhuatv.ru/761-x.html#cvh=17152725179103",
         "https://animakima.ru/pages/x/#cvh=17152725179103", "17152725179103"),
        (KodikPlayer, "https://dunhuatv.ru/761-x.html#kodik=77335/5b3fda38/1672436",
         "https://animakima.ru/pages/x/#kodik=77335/5b3fda38/1672436", "77335/5b3fda38/1672436"),
        (AnimakimaPlayer, "https://animakima.ru/pages/x/#animakima=8167/182980",
         "https://animakima.ru/pages/y/#animakima=8167/182980", "8167/182980"),
        (VkPlayer, "https://vk.com/video-170515668_456255738",
         "https://vkvideo.ru/video_ext.php?oid=-170515668&id=456255738&hash=abc", "-170515668_456255738"),
        (AllVideoPlayer, "https://fsst.online/embed/1025515/", "https://incvideo1.online/embed/1025515", "1025515"),
    ]:
        assert player.video_of(one) == player.video_of(other) == video, player


def test_a_link_that_names_a_place_on_its_page_or_no_video_names_none():
    assert VibixPlayer.video_of("https://1.ruchime.org/release/x/episode-1#vibix=1/1") == ""  # a place in its page's catalog
    assert CdnVideoHubPlayer.video_of("https://dunhuatv.ru/761-x.html") == ""
    assert VkPlayer.video_of("https://vk.com/club1") == ""


def test_every_player_of_the_registry_says_which_video_its_link_is():
    registry = PlayerRegistry.standard()
    assert all(callable(getattr(player, "video_of", None)) for player in registry)
    assert registry.video_of("cvh", "https://p/#cvh=5") == "5"
