"""The site dunhuatv.ru: a title page, and every item of every voice-over of every player it embeds.

The page embeds two players, CDN VideoHub and Kodik, and each lists the whole title: the voice-overs, and for each of them
its items. The table is the whole of it, with no choice of a player or a voice-over: a row for each item, with the voice-over
as the player names it, the season as the player groups it, and the text of the item as it is (CDN VideoHub has no text of
its own, and its player makes it of the numbers it gives). Nothing here numbers an episode: a compilation is a row whose
text says so (a range), and what its numbers are is the business of the constructor.
"""
from __future__ import annotations

import html
import re
from urllib.parse import urlparse

from core.reasons import reason

from ...players.registry import PlayerRegistry
from .. import PHASE_RELEASE, ReadContext, SiteError, TableDraft
from ..catalogs import read_page, table_rows

SITE = "https://dunhuatv.ru"
SITE_NAME = "dunhuatv.ru"

_TITLE_PATH = re.compile(r"^/(\d+)-[^/]+\.html$")
_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_ORIGINAL_NAME = re.compile(r"Как называется в оригинале\?</summary>\s*<div[^>]*>([^<]*)", re.IGNORECASE)


def title_names(text: str) -> list[str]:
    """The names of the title as the page writes them: the main one, then the original when the page has it."""
    heading = _H1.search(text)
    name = html.unescape(_TAGS.sub("", heading.group(1))).strip() if heading else ""
    original = _ORIGINAL_NAME.search(text)
    english = (re.split(r"\s*\(", html.unescape(original.group(1)), maxsplit=1)[0].strip(" .") or None) if original else None
    return [each for each in (name, english) if each]


class DunhuaTvSite:
    name = "dunhuatv"
    domains = ("dunhuatv.ru",)
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
        ctx.progress(PHASE_RELEASE, 0, 1)
        answer = await read_page(url, ctx, SITE_NAME)
        ctx.progress(PHASE_RELEASE, 1, 1)

        refs = {player.name: ref for player in self.players if (ref := player.find(answer.text, url)) is not None}
        if not refs:
            raise SiteError(reason("no_players_empty", site=SITE_NAME))
        body = await table_rows(self.players, [(url, refs)], ctx)
        return TableDraft(names=title_names(answer.text), rows=body.rows, unread=body.unread)
