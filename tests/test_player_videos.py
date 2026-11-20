"""Which video a link of a player is, as its host numbers it: one video shown by two pages is known as one; a link that
names a place on its page names no video."""
from modules.sources.players.allvideo import AllVideoPlayer
from modules.sources.players.animakima import AnimakimaPlayer
from modules.sources.players.cdnvideohub import CdnVideoHubPlayer
from modules.sources.players.kodik import KodikPlayer
from modules.sources.players.maccms import MaccmsPlayer
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
        (MaccmsPlayer, "https://jszyapi.com/api.php/provide/vod/?ac=detail&ids=1#maccms=https%3A%2F%2Fvv.test%2Fp%2Fi.m3u8",
         "https://api.xinlangapi.com/xinlangapi.php/provide/vod/?ac=detail&ids=9#maccms=https%3A%2F%2Fvv.test%2Fp%2Fi.m3u8",
         "https://vv.test/p/i.m3u8"),
    ]:
        assert player.video_of(one) == player.video_of(other) == video, player
