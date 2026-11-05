"""Synthetic answers of the sites and the players, built in the shape of the real ones.

Each answer keeps only the frame a reader takes (the elements, attributes and fields it looks for) and the facts the tests
check; nothing in it was copied from a real page. They stand in for the snapshots of real answers that were kept out of git,
so the tests of the sites run anywhere. `SAMPLES` names every answer; `sources_support.text_of` serves them by that name.
"""
from __future__ import annotations

import hashlib
import json
from functools import cache
from typing import Callable

# --- shared ------------------------------------------------------------------------------------------------------------------

TITLE_RU = "Практикуя ци сто тысяч лет"


def hex_of(*parts: object, size: int = 32) -> str:
    """A stable hex key standing for a hash a player gives (a Kodik item, a voice-over)."""
    return hashlib.md5("/".join(map(str, parts)).encode()).hexdigest()[:size]


# --- Kodik: the iframe of a voice-over and the answer of `/ftor` ---------------------------------------------------------------

KODIK_SIGNATURES = {"domain": "dunhuatv.ru", "d_sign": hex_of("d_sign", size=20), "pd": "kodikplayer.com",
                    "pd_sign": hex_of("pd_sign", size=20), "ref": "https://dunhuatv.ru/", "ref_sign": hex_of("ref_sign", size=20)}
# The three voice-overs of the title: translation id, studio, serial id, serial hash, first item id.
KODIK_VOICES = (("1043", "Animy", "50228", "7bbb4c6b2524cdda200216964479c303", 1144202),
                ("910", "AniStar", "49002", "b1b36deb82f4052aa1b25ef20c617288", 1110039),
                ("3845", "Студия Константина Исаева", "67748", "13d6614e6cd14385fe7f0e8debac3091", 1468548))


def kodik_animy_items() -> list[tuple[str, str]]:
    """362 items of Animy: single episodes, and 26 compilations of two from 109-110 (the value is the last number)."""
    items = [(f"{number} серия", str(number)) for number in range(1, 109)]
    items += [(f"{first}-{first + 1} серия", str(first + 1)) for first in range(109, 161, 2)]
    items += [(f"{number} серия", str(number)) for number in range(161, 389)]
    return items


def kodik_anistar_items() -> list[tuple[str, str]]:
    return [(f"{number} серия", str(number)) for number in range(1, 288)]


def kodik_isaev_items() -> list[tuple[str, str]]:
    """17 compilations of three episodes each: 1-3, 4-6 ... 49-51."""
    return [(f"{first}-{first + 2} серия", str(first + 2)) for first in range(1, 52, 3)]


def kodik_iframe(voice: int, items: list[tuple[str, str]]) -> str:
    """The iframe of one voice-over: the list of the voice-overs of the title (this one selected), its one season, its items
    and the signed parameters of the player script."""
    translation_id, studio, media_id, media_hash, first_id = KODIK_VOICES[voice]
    voices = "\n".join(
        f'<option value="{tid}" data-id="{tid}" data-translation-type="voice" data-media-id="{mid}" '
        f'data-media-hash="{mhash}" data-media-type="serial" data-title="{name}"'
        f'{" selected=\"selected\"" if tid == translation_id else ""}>{name}</option>'
        for tid, name, mid, mhash, _first in KODIK_VOICES)
    series = "\n".join(
        f'<option value="{value}" data-id="{first_id + index}" data-hash="{hex_of(media_id, index)}" data-title="{text}" '
        f'data-other-translation="false">{text}</option>'
        for index, (text, value) in enumerate(items))
    signatures = "\n".join(f'var {name} = "{value}";' for name, value in KODIK_SIGNATURES.items())
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Kodik</title></head><body>
<div class="serial-translations-box"><select>
{voices}
</select></div>
<div class="serial-panel hidden-seasons">
<div class="serial-seasons-box"><select>
<option value="1" data-serial-id="{media_id}" data-serial-hash="{media_hash}" data-title="1 сезон" selected="selected" data-translation-title="{studio}">1 сезон</option>
</select></div>
<div class="serial-series-box"><select>
{series}
</select></div>
</div>
<script>
{signatures}
</script>
</body></html>
"""


def kodik_ftor() -> str:
    links = {height: [{"src": f"https://cloud.kodik.test/s/{height}.mp4:hls:manifest.m3u8", "type": "application/x-mpegURL"}]
             for height in ("360", "480", "720")}
    return json.dumps({"advert_script": "", "domain": "kodikplayer.com", "default": 360, "links": links})


# --- 1.ruchime.org: the release, its list of episodes, the page of an episode ---------------------------------------------------

RUCHIME_SLUG = "praktikuia-ci-sto-tysiac-let"
# The players of the first and of the last episode page: VK id and key, AllVideo embed.
RUCHIME_PLAYERS = {1: ("456246539", "8edfdb802d04778c", "688273"), 390: ("456256559", "844d706ebb47dffa", "1081867")}
KODIK_SWITCH = "https://kodikplayer.com/serial/50228/7bbb4c6b2524cdda200216964479c303/720p?only_translations=1043"


@cache
def ruchime_entries() -> tuple[dict, ...]:
    """The 390 entries of the list of the release (`ALL_EPS`): the first published on 9 May, the last on 31 October."""
    return tuple({"id": 93449 + number, "number": number, "compilation_from": None, "compilation_to": None,
                  "title": f"Серия {number}", "is_vip": False, "is_trailer": False,
                  "date": "31 Oct 2026" if number == 390 else "09 May 2026"} for number in range(1, 391))


def ruchime_release() -> str:
    head = json.dumps({"@context": "https://schema.org", "@type": "TVSeries", "name": TITLE_RU, "alternateName": "炼气十万年"},
                      ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><title>{TITLE_RU} — Ruchime</title>
<script type="application/ld+json">{head}</script></head>
<body>
<div class="info-meta">
<span class="info-meta-key">Серии</span><span class="info-meta-val">Вышло 390 из 420</span>
<span class="info-meta-key">Озвучка</span>
<span class="info-meta-val" style="color:var(--accent)">Ruchime</span>
</div>
<script>
const ALL_EPS = {json.dumps(list(ruchime_entries()), ensure_ascii=False)};
</script>
</body></html>
"""


def ruchime_episode(number: int) -> str:
    """The page of an episode: the iframe of VK and the buttons of the players it switches to."""
    vk_id, vk_hash, allvideo_id = RUCHIME_PLAYERS[number]
    vk = f"https://vkvideo.ru/video_ext.php?oid=-170515668&id={vk_id}&hash={vk_hash}"
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><title>{TITLE_RU} {number} серия — Ruchime</title></head>
<body>
<iframe data-src="{vk}" src="" allowfullscreen id="vplayer-iframe"></iframe>
<div class="player-source-row">
<button class="source-btn" data-player="vk" onclick="switchSource('{vk}',this)">VK Video</button>
<button class="source-btn" data-player="allvideo" onclick="switchSource('https://fsst.online/embed/{allvideo_id}/',this)">AllVideo</button>
<button class="source-btn" data-player="vibix" onclick="switchSource('vibix-embed',this)">Vibix</button>
<button class="source-btn" data-player="kodik" onclick="switchSource('{KODIK_SWITCH}', this)">Kodik</button>
</div>
</body></html>
"""


def vk_embed_closed() -> str:
    """The player page of VK for a video the owner closed: the page says so and lists no file."""
    return """<!DOCTYPE html>
<html><head><title>Video embed</title></head><body>
<div id="video_ext_msg">Это видео скрыто настройками приватности и недоступно для просмотра</div>
</body></html>
"""


def vk_embed_shell() -> str:
    """The shell of the VK player page that issues a web token and names the host of its API."""
    token = json.dumps({"access_token": "anonym.synthetic-token", "expired_at": 9_000_000_000})
    config = json.dumps({"apiDomain": "api.vkvideo.ru", "domain": "vkvideo.ru", "loginDomain": "login.vk.ru"})
    return f"""<!DOCTYPE html>
<html><head><title>VK</title></head><body>
<script>
window.vk = {{
  apiConfigDomains: {config},
  webToken: {token},
}};
</script>
</body></html>
"""


def vk_video_get_restricted() -> str:
    """The answer of `video.get` for the last episode: the video is for signed-in users only and lists no file."""
    item = {"id": 456256559, "owner_id": -170515668, "title": f"{TITLE_RU} 390 серия 4K", "duration": 448,
            "date": 1793433607, "restriction": {"can_play": 0, "can_preview": 1, "title": "Signed-in users only"}}
    return json.dumps({"response": {"count": 1, "items": [item]}}, ensure_ascii=False)


def allvideo_embed(video_id: str, file: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><title>{TITLE_RU}</title></head><body>
<script>
var player = new Playerjs({{ id: 'Video{video_id}', file:"{file}" }});
</script>
</body></html>
"""


def vibix_catalog(seasons: list[tuple[str, int]], quality: str) -> str:
    """The decoded answer of the Vibix API: seasons of numbered episodes, each with one stream named by its voice-over."""
    playlist = [{"title": title, "folder": [
        {"title": f"Серия {number}",
         "file": f"[{quality}]{{RuChiMe}}https://river-1.kinescopecdn.net/hls/{index}/{number}.mp4/index.m3u8"}
        for number in range(1, count + 1)]} for index, (title, count) in enumerate(seasons, 1)]
    return json.dumps({"data": {"playlist": playlist}, "success": True, "message": "", "status": 200}, ensure_ascii=False)


# --- dunhuatv.ru: the title page and CDN VideoHub ----------------------------------------------------------------------------

CVH_FULL_HD = "14244459338480"


def dunhuatv_title() -> str:
    """The title page: the name, the original name, the widget of CDN VideoHub and the iframe of Kodik."""
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>{TITLE_RU} смотреть онлайн</title></head><body>
<h1>{TITLE_RU}</h1>
<details><summary>Как называется в оригинале?</summary>
<div class="faq-a">Lian Qi Shi Wan Nian (англ. One Hundred Thousand Years of Qi Refining).</div></details>
<video-player id="myplayer" data-title-id="54437" data-publisher-id="1110" ident="player_1" data-aggregator="mali" is-show-voice-only="false"></video-player>
<iframe data-src="//kodikplayer.com/serial/50228/7bbb4c6b2524cdda200216964479c303/720p" loading="lazy" allowfullscreen></iframe>
</body></html>
"""


@cache
def cvh_items() -> tuple[dict, ...]:
    """607 items of the playlist: AniMy / RuChiMe 330 (no item 109, the file of 110 holds both), AniStar 238 of one type and
    27 of another, and 12 with no studio at all (the key is absent, as the player leaves it out)."""
    plan = ([("AniMy / RuChiMe", "Многоголосый", number) for number in [*range(1, 109), *range(110, 332)]]
            + [("AniStar", "Многоголосый", number) for number in range(1, 239)]
            + [("AniStar", "Неизвестный", number) for number in range(239, 266)]
            + [(None, "Многоголосый", number) for number in range(325, 337)])
    special = {0: "9902733417042", 1: "8026395597487", 2: CVH_FULL_HD}
    items = []
    for index, (studio, kind, episode) in enumerate(plan):
        item = {"cvhId": f"0194ebd9-cf18-74a9-a921-{index:012d}", "vkId": special.get(index, str(9_000_000_000_000 + index))}
        if studio is not None:
            item["voiceStudio"] = studio
        items.append({**item, "voiceType": kind, "season": 1, "episode": episode})
    return tuple(items)


def cvh_playlist() -> str:
    return json.dumps({"titleName": TITLE_RU, "isSerial": True, "items": list(cvh_items()), "trailers": []},
                      ensure_ascii=False)


def cvh_video(video_id: str, full_hd: bool) -> str:
    """The answer of the player about one video: direct links up to 720p (up to 1080p for one), none above it."""
    rungs = ["mpegTinyUrl", "mpegLowestUrl", "mpegLowUrl", "mpegMediumUrl", "mpegHighUrl"] + (["mpegFullHdUrl"] if full_hd else [])
    sources = {"hlsUrl": f"https://vd1.okcdn.ru/video.m3u8?id={video_id}", "dashUrl": f"https://vd1.okcdn.ru/dash?id={video_id}",
               "mpegQhdUrl": "", "mpeg2kUrl": "", "mpeg4kUrl": "", "mpegFullHdUrl": ""}
    sources.update({key: f"https://vd1.okcdn.ru/?rung={key}&id={video_id}" for key in rungs})
    return json.dumps({"unitedVideoId": int(video_id), "duration": 733, "failoverHost": "vd1.okcdn.ru", "sources": sources})


# --- the names the tests ask for ---------------------------------------------------------------------------------------------

SAMPLES: dict[str, Callable[[], str]] = {
    "ruchime__release.html": ruchime_release,
    "ruchime__ALL_EPS.json": lambda: json.dumps(list(ruchime_entries()), ensure_ascii=False),
    "ruchime__episode-1.html": lambda: ruchime_episode(1),
    "ruchime__episode-390.html": lambda: ruchime_episode(390),
    "ruchime__episode-1__vk_embed.html": vk_embed_closed,
    "ruchime__episode-390__vk_embed.html": vk_embed_closed,
    "ruchime__episode-1__allvideo_embed.html": lambda: allvideo_embed("688273", ",".join(
        f"[{height}p]https://www.incvideo1.online/get_file/12/688273_{height}p.mp4/" for height in (360, 720, 1080))),
    "ruchime__episode-390__allvideo_embed.html": lambda: allvideo_embed(
        "1081867", "https://www.incvideo1.online/get_file/11/1081867_360p.mp4/"),
    "ruchime__vk_embed_agent0.html": vk_embed_shell,
    "ruchime__vk_video_get_agent0.json": vk_video_get_restricted,
    "vibix__praktikuia-ci-sto-tysiac-let__api_decoded.json": lambda: vibix_catalog([("Сезон 1", 390)], "1080p"),
    "vibix__asura-bezumnyi-korol-2__api_decoded.json": lambda: vibix_catalog([("Сезон 1", 9), ("Сезон 2", 14)], "480p"),
    "dunhuatv__title.html": dunhuatv_title,
    "dunhuatv__cvh_playlist.json": cvh_playlist,
    f"dunhuatv__cvh_video__{CVH_FULL_HD}.json": lambda: cvh_video(CVH_FULL_HD, True),
    "dunhuatv__cvh_video__8026395597487.json": lambda: cvh_video("8026395597487", False),
    "dunhuatv__cvh_video__9902733417042.json": lambda: cvh_video("9902733417042", False),
    "dunhuatv__kodik_voice0.html": lambda: kodik_iframe(0, kodik_animy_items()),
    "dunhuatv__kodik_voice1.html": lambda: kodik_iframe(1, kodik_anistar_items()),
    "dunhuatv__kodik_voice2.html": lambda: kodik_iframe(2, kodik_isaev_items()),
    "dunhuatv__kodik_ftor.json": kodik_ftor,
}
