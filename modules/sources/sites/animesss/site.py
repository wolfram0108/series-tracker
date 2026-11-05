"""The site animesss.com: a title page, and every item of every player its playlist offers.

The page holds no player: it holds the number of the title, and the site gives its playlist on a request of its own
(`mod=animesss_playlist`). The playlist names its sources ("donors"): the widget of CDN VideoHub by the numbers of the
title, and an iframe of Kodik for each voice-over. Each is read by its player as on any page that embeds it; a source no
player knows is not read, and the log says so. The table is the whole of it, and nothing here numbers an episode.
"""
from __future__ import annotations

import html
import json
import logging
import re
from urllib.parse import parse_qs, urlparse

from core.reasons import reason

from ...players.cdnvideohub import CdnVideoHubPlayer
from ...players.contract import PlayerRef
from ...players.registry import PlayerRegistry
from .. import PHASE_RELEASE, ReadContext, SiteError, TableDraft
from ..catalogs import read_page, table_rows

logger = logging.getLogger("sources")

SITE = "https://animesss.com"
SITE_NAME = "animesss.com"
PLAYLIST = f"{SITE}/index.php"

_TITLE_PATH = re.compile(r"^/(?:[^/]+/)*(\d+)-[^/]+\.html$")
_NEWS_ID = re.compile(r"""id=["']kodik_player_ajax["'][^>]*\bdata-news_id=["'](\d+)["']""", re.IGNORECASE)
_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_LD = re.compile(r"""<script type=["']application/ld\+json["']>(.*?)</script>""", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_DONOR = re.compile(r"""<ul\b[^>]*\bb-translators__list\b[^>]*\bdata-donor=["']([\w-]+)["'][^>]*>(.*?)</ul>""",
                    re.IGNORECASE | re.DOTALL)
_ITEM = re.compile(r"<li\b([^>]*\bb-translator__item\b[^>]*)>", re.IGNORECASE)
_ATTR = re.compile(r"""([\w-]+)\s*=\s*["']([^"']*)["']""")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAGS.sub("", fragment)).split())


def title_names(text: str) -> list[str]:
    """The name of the title as the site's own trail of pages ends with it, then the heading of the page."""
    names = []
    for block in _LD.findall(text):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        nodes = (data.get("@graph") or [data]) if isinstance(data, dict) else []
        for node in nodes if isinstance(nodes, list) else []:
            trail = node.get("itemListElement") if isinstance(node, dict) else None
            last = trail[-1] if isinstance(trail, list) and trail else {}
            item = last.get("item") if isinstance(last, dict) else None
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append(item["name"].strip())
    heading = _H1.search(text)
    names += [_text(heading.group(1))] if heading else []
    return list(dict.fromkeys(name for name in names if name))


def news_id(text: str) -> str | None:
    """The number of the title the playlist is asked by."""
    found = _NEWS_ID.search(text)
    return found.group(1) if found else None


def donors(playlist: str) -> list[tuple[str, dict[str, str]]]:
    """(the source, the attributes of an item) for each item of the playlist, in its order."""
    return [(donor, dict((key, html.unescape(value)) for key, value in _ATTR.findall(attrs)))
            for donor, block in _DONOR.findall(playlist) for attrs in _ITEM.findall(block)]


class AnimesssSite:
    name = "animesss"
    domains = ("animesss.com",)
    source_type = "web_video"

    def __init__(self, players: PlayerRegistry | None = None) -> None:
        self.players = players or PlayerRegistry.standard()

    def configure(self, *, players: PlayerRegistry | None = None, **_wiring) -> None:
        """The players of the module: the one Kodik and the one CDN VideoHub serve every site that embeds them."""
        if players is not None:
            self.players = players

    def match_url(self, url: str) -> bool:
        try:
            return _TITLE_PATH.match(urlparse(url).path) is not None
        except ValueError:
            return False

    def canonical_url(self, url: str) -> str:
        """The title page alone: what a series takes from it is chosen on the table, not in the address."""
        path = urlparse(url).path
        if not _TITLE_PATH.match(path):
            raise ValueError(reason("not_a_title_page", site=SITE_NAME, url=url))
        return f"{SITE}{path}"

    async def read(self, url: str, ctx: ReadContext) -> TableDraft:
        ctx.progress(PHASE_RELEASE, 0, 2)
        page = (await read_page(url, ctx, SITE_NAME)).text
        number = news_id(page)
        if number is None:
            raise SiteError(reason("no_players_empty", site=SITE_NAME))
        ctx.progress(PHASE_RELEASE, 1, 2)
        playlist = await read_page(PLAYLIST, ctx, SITE_NAME, headers={"Referer": url, "X-Requested-With": "XMLHttpRequest"},
                                   params={"controller": "ajax", "mod": "animesss_playlist", "news_id": number,
                                           "action": "load_player"})
        ctx.progress(PHASE_RELEASE, 2, 2)
        refs = [ref for donor, item in donors(playlist.text) if (ref := self._ref(url, donor, item)) is not None]
        if not refs:
            raise SiteError(reason("no_players_empty", site=SITE_NAME))
        return TableDraft(names=title_names(page),
                          rows=await table_rows(self.players, [(url, {ref.name: ref}) for ref in refs], ctx))

    def _ref(self, url: str, donor: str, item: dict[str, str]) -> PlayerRef | None:
        """The player behind an item: the widget of CDN VideoHub by its numbers, or the player that knows the iframe."""
        query = {key: values[0] for key, values in parse_qs(item.get("data-player-query", "")).items()}
        if query.get("provider") == "videohub" and query.get("title_id", "").isdigit():
            publisher = query.get("publisher_id", "")
            return CdnVideoHubPlayer.listing(url, int(query["title_id"]),
                                             *([int(publisher)] if publisher.isdigit() else []),
                                             *([query["aggregator"]] if query.get("aggregator") else []))
        link = item.get("data-this-link", "")
        frame = f'<iframe src="{html.escape(link)}"></iframe>'
        found = next((ref for player in self.players if link and (ref := player.find(frame, url)) is not None), None)
        if found is None:
            logger.info("%s: source %r of %s is not read: no player knows it", SITE_NAME, donor, url)
        return found
