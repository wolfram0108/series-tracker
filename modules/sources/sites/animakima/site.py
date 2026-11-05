"""The site animakima.ru: a title page, and every item of every player the page offers.

The page holds no player: it holds the buttons of its players and the list of its episodes, and the API of the site gives
what a button shows (`get_player_view`). The view of a player that lists the whole title (the widget of CDN VideoHub, the
iframe of Kodik) is read by that player as it is read on any page that embeds it; a view no player knows is not read, and
the log says so. The own player of the site shows one episode at a time: its rows are the episodes of the page, each asked
of the player. The table is the whole of it, with no choice of a player or a voice-over, and nothing here numbers an episode.
"""
from __future__ import annotations

import html
import logging
import re
from typing import Mapping
from urllib.parse import urlparse

from core.contracts import RowVoice, TableRow
from core.reasons import reason

from ...players.animakima import VIEW, AnimakimaError, AnimakimaPlayer, read_view, view_request
from ...players.asking import catalog_rows
from ...players.catalog import Entry
from ...players.contract import PlayerRef
from ...players.http import USER_AGENT, get_answer
from ...players.registry import PlayerRegistry
from ...policy import ask
from .. import PHASE_PLAYERS, PHASE_RELEASE, ReadContext, SiteError, TableDraft
from ..catalogs import AskCount, read_catalogs, rows_of_catalogs
from ..recent import last_of_each

logger = logging.getLogger("sources")

SITE = "https://animakima.ru"
SITE_NAME = "animakima.ru"
PAGE_TIMEOUT = 20.0

_TITLE_PATH = re.compile(r"^/pages/([^/]+)/?$")
_PLAYER = re.compile(r"<title-player\b([^>]*)>", re.IGNORECASE)
_ATTR = re.compile(r"""([\w-]+)\s*=\s*["']([^"']*)["']""")
_BUTTON = re.compile(r"""<button\b[^>]*\bbtn--player-select\b[^>]*\bdata-player=["']([\w-]+)["']""", re.IGNORECASE)
_EPISODE = re.compile(r"""<button\b[^>]*\bbtn--select-video\b[^>]*\bdata-sid=["'](\d+)["'][^>]*>(.*?)</button>""",
                      re.IGNORECASE | re.DOTALL)
_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_OTHER_NAMES = re.compile(r"""<div class=["']expand-list-content["']>(.*?)</div>""", re.IGNORECASE | re.DOTALL)
_SPAN = re.compile(r"<span\b[^>]*>(.*?)</span>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAGS.sub("", fragment)).split())


def title_names(text: str) -> list[str]:
    """The names of the title as the page writes them: the heading, then the other names it lists."""
    heading = _H1.search(text)
    others = _OTHER_NAMES.search(text)
    names = [_text(heading.group(1))] if heading else []
    names += [_text(span) for span in _SPAN.findall(others.group(1))] if others else []
    return list(dict.fromkeys(name for name in names if name))


def title_id(text: str) -> str | None:
    """The number of the title the API knows it by."""
    found = _PLAYER.search(text)
    aid = dict(_ATTR.findall(found.group(1))).get("aid", "") if found else ""
    return aid if aid.isdigit() else None


def player_views(text: str) -> list[str]:
    """The players the page offers, by the name its API gives each view, in the order of the buttons."""
    return list(dict.fromkeys(_BUTTON.findall(text)))


def episodes(text: str) -> list[tuple[str, str]]:
    """(the number of the episode at the site, the text of its button) in the order of the list."""
    return list(dict.fromkeys((sid, _text(label)) for sid, label in _EPISODE.findall(text)))


class AnimakimaSite:
    name = "animakima"
    domains = ("animakima.ru",)
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

    def canonical_url(self, url: str, options: Mapping[str, str] = {}) -> str:
        """The title page alone: what a series takes from it is chosen on the table, not in the address."""
        found = _TITLE_PATH.match(urlparse(url).path)
        if not found:
            raise ValueError(reason("not_a_title_page", site=SITE_NAME, url=url))
        return f"{SITE}/pages/{found.group(1)}/"

    async def read(self, url: str, ctx: ReadContext) -> TableDraft:
        page = await self._page(url, ctx)
        aid = title_id(page)
        views = player_views(page)
        if aid is None or not views:
            raise SiteError(reason("no_players_empty", site=SITE_NAME))
        ctx.progress(PHASE_RELEASE, 1, 1 + len(views))

        refs: dict[str, PlayerRef] = {}
        for done, view in enumerate(views, start=2):
            if view != VIEW:
                found = await self._listed(url, aid, view, ctx)
                if found is not None:
                    refs[found.name] = found
            ctx.progress(PHASE_RELEASE, done, 1 + len(views))

        own = self._own_entries(url, aid, page, ctx) if VIEW in views else []
        if not refs and not own:
            raise SiteError(reason("no_players_empty", site=SITE_NAME))
        catalogs = await read_catalogs(self.players, [(url, refs)], ctx) if refs else []
        asked = AskCount(ctx, sum(len(catalog.entries) for catalog in catalogs) + len(own))
        ctx.progress(PHASE_PLAYERS, 0, asked.total)
        rows: list[TableRow] = await rows_of_catalogs(catalogs, ctx, self.players.pacer, asked)
        if own:
            rows += await catalog_rows(self.players.get(AnimakimaPlayer.name), own, ctx.http, url, self.players.pacer,
                                       on_asked=asked)
        return TableDraft(names=title_names(page), rows=rows)

    async def _page(self, url: str, ctx: ReadContext) -> str:
        ctx.progress(PHASE_RELEASE, 0, 1)
        answer = await ask(lambda: get_answer(ctx.http, url, "", headers={"User-Agent": USER_AGENT}, timeout=PAGE_TIMEOUT))
        if answer.status is None:
            raise SiteError(reason("site_unreachable", site=SITE_NAME, error=answer.error))
        if answer.status != 200:
            raise SiteError(reason("site_status", site=SITE_NAME, status=answer.status, url=url))
        return answer.text

    async def _listed(self, url: str, aid: str, view: str, ctx: ReadContext) -> PlayerRef | None:
        """The player that lists the whole title behind a button; None, and a line in the log, for a view no player knows."""
        try:
            shown = read_view(await ask(lambda: view_request(ctx.http, aid, view, referer=url)), SITE_NAME)
        except AnimakimaError as exc:
            raise SiteError(exc.reason) from exc
        found = next((ref for player in self.players if (ref := player.find(shown.data, url)) is not None), None)
        if found is None:
            logger.info("%s: player %r of %s is not read: no player knows its view", SITE_NAME, view, url)
        return found

    def _own_entries(self, url: str, aid: str, page: str, ctx: ReadContext) -> list[Entry]:
        """An entry for each episode of the page: the own player gives no voice-over and no season of its own."""
        if AnimakimaPlayer.name not in self.players:
            logger.info("%s: the own player of %s is not read: it is not registered", SITE_NAME, url)
            return []
        found = [Entry(key=sid, ref=AnimakimaPlayer.ref_of(url, aid, sid), text=label, voice=RowVoice(),
                       site_fields={"sid": sid}) for sid, label in episodes(page)]
        return last_of_each(found, lambda _entry: "", ctx.recent)
