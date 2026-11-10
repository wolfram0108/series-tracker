"""The site yummyani.me: a title, and every video its API lists for it.

The API of the site gives the title (`/api/anime/<address>`: its number and names) and every video of it
(`/api/anime/<number>/videos`): one element for each episode of each voice-over of each player, with the iframe that
plays it. A player that lists a whole title (Kodik, CDN VideoHub) is read through its own list, as on any page that
embeds it; a player of one video an iframe (VK, Alloha) gives a row for each element. The rows of a list of Kodik are the
episodes the site lists for that list: the list of a voice-over holds more than the site shows of it (an episode Kodik
borrowed from another voice-over), and the table is what the site shows. The site wraps two players in pages
of its own: CDN VideoHub is its widget by the number of the title at MyAnimeList (with the publisher of the site), VK is a
video by its number. A player no player here knows is not read, and the log says so. Nothing here numbers an episode.
"""
from __future__ import annotations

import html
import json
import logging
import re
from collections import Counter
from urllib.parse import parse_qs, urlparse

from core.contracts import RowVoice
from core.reasons import reason

from ...players.catalog import Entry
from ...players.cdnvideohub import CdnVideoHubPlayer
from ...players.kodik import KodikPlayer
from ...players.contract import PlayerRef
from ...players.registry import PlayerRegistry
from ...players.vk import VkPlayer
from .. import PHASE_RELEASE, ReadContext, SiteError, TableDraft
from ..catalogs import Listed, catalog_part, read_page, table_rows
from ..recent import last_of_each

logger = logging.getLogger("sources")

SITE = "https://old.yummyani.me"
SITE_NAME = "yummyani.me"
# The publisher the site's own page of CDN VideoHub embeds the widget with.
CVH_PUBLISHER = 745
CVH_AGGREGATOR = "mali"

_TITLE_PATH = re.compile(r"^/catalog/item/([^/]+)/?$")
_OWN_CVH = "/iframeCVH.html"
_OWN_VK = "/iframeVK.html"
_VK_VIDEO = re.compile(r"^(?:video)?(-?\d+)_(\d+)$")
# The kinds of a voice-over the site puts before its name: a voice-over or subtitles.
_VOICE = re.compile(r"^(Озвучка|Субтитры)\s+(.+)$")


def voice_of(dubbing: str) -> RowVoice:
    """The voice-over as the site words it: its kind when the site names one, and the name."""
    text = " ".join(str(dubbing or "").split())
    found = _VOICE.match(text)
    return RowVoice(name=found.group(2), kind=found.group(1)) if found else RowVoice(name=text)


def title_names(title: dict) -> list[str]:
    others = title.get("other_titles") if isinstance(title.get("other_titles"), list) else []
    names = [title.get("title"), *others]
    return list(dict.fromkeys(str(name).strip() for name in names if isinstance(name, str) and name.strip()))


def videos(answer: str) -> list[dict]:
    try:
        listed = json.loads(answer).get("response")
    except (ValueError, AttributeError):
        return []
    return [item for item in listed if isinstance(item, dict)] if isinstance(listed, list) else []


def _absolute(iframe: str) -> str:
    return "https:" + iframe if iframe.startswith("//") else iframe


def _query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}


class YummyAniSite:
    name = "yummyani"
    domains = ("yummyani.me",)
    source_type = "web_video"

    def __init__(self, players: PlayerRegistry | None = None) -> None:
        self.players = players or PlayerRegistry.standard()

    def configure(self, *, players: PlayerRegistry | None = None, **_wiring) -> None:
        if players is not None:
            self.players = players

    def match_url(self, url: str) -> bool:
        try:
            return _TITLE_PATH.match(urlparse(url).path) is not None
        except ValueError:
            return False

    def canonical_url(self, url: str) -> str:
        """The title page alone, at the one host of the site: the mirrors show one title at one address."""
        found = _TITLE_PATH.match(urlparse(url).path)
        if not found:
            raise ValueError(reason("not_a_title_page", site=SITE_NAME, url=url))
        return f"{SITE}/catalog/item/{found.group(1)}"

    async def read(self, url: str, ctx: ReadContext) -> TableDraft:
        address = _TITLE_PATH.match(urlparse(url).path).group(1)
        headers = {"Referer": url, "X-Requested-With": "XMLHttpRequest"}
        ctx.progress(PHASE_RELEASE, 0, 2)
        try:
            title = json.loads((await read_page(f"{SITE}/api/anime/{address}", ctx, SITE_NAME, headers=headers)).text)
            title = title["response"]
            number = int(title["anime_id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise SiteError(reason("no_players_empty", site=SITE_NAME)) from exc
        ctx.progress(PHASE_RELEASE, 1, 2)
        listed = videos((await read_page(f"{SITE}/api/anime/{number}/videos", ctx, SITE_NAME, headers=headers)).text)
        ctx.progress(PHASE_RELEASE, 2, 2)

        catalogs: list[PlayerRef] = []
        voices: dict[PlayerRef, list[str]] = {}
        # The episodes the site lists of each list of Kodik (the part of the catalog), by the number the site gives.
        episodes: dict[str, set[str]] = {}
        entries: dict[str, list[Entry]] = {}
        unknown: Counter = Counter()
        for item in listed:
            iframe = _absolute(str(item.get("iframe_url") or ""))
            ref = self._ref(url, iframe)
            if ref is None:
                unknown[str((item.get("data") or {}).get("player") or urlparse(iframe).hostname)] += 1
                continue
            player = self.players.get(ref.name)
            data = item.get("data") or {}
            if player.series_scope:
                catalogs.append(ref)
                voices.setdefault(ref, []).append(voice_of(data.get("dubbing", "")).name)
                if ref.name == KodikPlayer.name:
                    episodes.setdefault(catalog_part(player, ref), set()).add(str(item.get("number") or "").strip())
                continue
            number_text = str(item.get("number") or "").strip()
            entries.setdefault(ref.name, []).append(Entry(
                key=player.video_of(ref.url) or str(item.get("video_id")), ref=ref,
                text=f"{number_text} серия" if number_text else "", voice=voice_of(data.get("dubbing", "")),
                site_fields={"video_id": item.get("video_id"), "number": number_text}))
        for player_label, count in unknown.items():
            logger.info("%s: %d videos of %r on %s are not read: no player knows them", SITE_NAME, count, player_label, url)
        if not catalogs and not entries:
            raise SiteError(reason("no_players_empty", site=SITE_NAME))
        own = [Listed(self.players.get(name), url, last_of_each(_once(found), lambda each: (each.voice.name, each.voice.kind), ctx.recent))
               for name, found in entries.items()]
        def shown(part: str, entry: Entry) -> bool:
            return part not in episodes or str(entry.site_fields.get("value", "")) in episodes[part]

        body = await table_rows(self.players, [(url, {ref.name: ref}) for ref in catalogs], ctx, own, names=voices,
                                shown=shown)
        return TableDraft(names=title_names(title), rows=body.rows, unread=body.unread)

    def _ref(self, url: str, iframe: str) -> PlayerRef | None:
        """The player behind an iframe: the site's own page of CDN VideoHub or of VK, or the player that knows the iframe."""
        path = urlparse(iframe).path
        if path == _OWN_CVH:
            anime = _query(iframe).get("anime_id", "")
            return CdnVideoHubPlayer.listing(url, int(anime), CVH_PUBLISHER, CVH_AGGREGATOR) if anime.isdigit() else None
        if path == _OWN_VK:
            found = _VK_VIDEO.match(_query(iframe).get("id", ""))
            if found is None or VkPlayer.name not in self.players:
                return None
            return VkPlayer.ref_of(f"https://vk.com/video_ext.php?oid={found.group(1)}&id={found.group(2)}")
        frame = f'<iframe src="{html.escape(iframe)}"></iframe>'
        return next((ref for player in self.players if (ref := player.find(frame, url)) is not None), None)


def _once(entries: list[Entry]) -> list[Entry]:
    """One video listed twice by the site is one row: the first."""
    first: dict[str, Entry] = {}
    for entry in entries:
        first.setdefault(entry.key, entry)
    return list(first.values())
