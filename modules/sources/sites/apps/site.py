"""A card of a video application (modules/sources/apps) as a page: its table is every episode of every line of the card.

The card lists its lines as `play_from` (codes of the application's players, apart by `$$$`) and their episodes as
`play_url` (`<label>$<token>#...` for each line, apart by `$$$`), the lines in the order the application gives. Every item
of every line is a row of the player of its line (`<application>:<line>`, labelled with the name of the line), its text the
label as the application gives it ("1", "第01集", "《万界独尊》2026新春贺岁PV"): nothing here numbers an episode, leaves an
item out or tells one line from another. The line's name, type and parsers are visible fields of its rows.

Every episode is asked of the application's player in the read, its frame measured (players/app_lines.py): the lines of
a card are asked all at once, each episode by the gates of its parser and of the host of its files. An episode the last
table measured is not asked again: the token names its video for good. The field `stream` of a row says how its stream
comes: the playlist the line lists itself, or a parser of the application.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import asyncio

from core.contracts import RowState, TableRow
from core.reasons import reason

from ...players.asking import catalog_rows
from ...players.catalog import Entry
from ...players.contract import Kind, PlayerRef, Reading
from ...apps import SCHEME, card_address
from ...apps.app99 import AppError
from ...players.app_lines import AppPlayer
from .. import PHASE_PLAYERS, PHASE_RELEASE, ReadContext, SiteError, TableDraft
from ..catalogs import AskCount
from ..recent import last_of_each

LIST_SEPARATOR = "$$$"
ITEM_SEPARATOR = "#"


def lines_of(card: dict) -> list[tuple[str, list[tuple[int, str, str]]]]:
    """(line, [(place, label, token)]) of a card, in the order the application gives; an item without a token is none."""
    codes = str(card.get("play_from") or "").split(LIST_SEPARATOR)
    lists = str(card.get("play_url") or "").split(LIST_SEPARATOR)
    found = []
    for code, listed in zip(codes, lists):
        items = []
        for place, item in enumerate(listed.split(ITEM_SEPARATOR), start=1):
            label, _, token = item.partition("$")
            if token.strip():
                items.append((place, label.strip(), token.strip()))
        found.append((code.strip(), items))
    return found


class AppSite:
    """The cards of one application, read through its API."""

    source_type = "web_video"

    def __init__(self, player: AppPlayer) -> None:
        self.player = player
        self.client = player.client
        self.name, self.label = player.name, player.label
        self.domains = (player.name,)

    def configure(self, **_wiring) -> None:
        """The card is read through the application's API alone: the wiring of the module gives it nothing."""

    def _card(self, url: str) -> str | None:
        split = urlsplit(url)
        number = split.path.strip("/")
        if split.scheme != SCHEME or (split.hostname or "") != self.name or not number.isdigit():
            return None
        return number

    def match_url(self, url: str) -> bool:
        return self._card(url) is not None

    def canonical_url(self, url: str) -> str:
        card = self._card(url)
        if card is None:
            raise ValueError(reason("not_a_title_page", site=self.player.label, url=url))
        return card_address(self.name, card)

    async def read(self, url: str, ctx: ReadContext) -> TableDraft:
        card_url = self.canonical_url(url)
        ctx.progress(PHASE_RELEASE, 0, 1)
        try:
            data = await self.client.detail(self._card(card_url))
            system = await self.client.system()
        except AppError as exc:
            raise SiteError(exc.reason) from exc
        ctx.progress(PHASE_RELEASE, 1, 1)
        players = {str(each.get("code") or key): each for key, each in (system.get("player") or {}).items()
                   if isinstance(each, dict)}
        # What the last table measured of each episode: the token names its video for good.
        known = {PlayerRef(row.player, row.link): Reading(Kind.PLAYABLE, 200, tuple(row.heights))
                 for row in ctx.last_rows if self.player.owns(row.link) and row.state is RowState.PLAYABLE and row.heights}
        asked = []
        for code, items in lines_of(data):
            shown = players.get(code, {})
            line = self.player.line(code, str(shown.get("name") or code))
            fields = {"line": code, "line_name": line.label, "line_type": shown.get("type"),
                      "parsers": str(shown.get("parseUrl") or ""),
                      "stream": "playlist" if shown.get("type") == 0 else "parser"}
            entries = [Entry(key=str(place), ref=PlayerRef(line.name, self.player.link_of(card_url, code, token)),
                             text=label, site_fields={**fields, "place": place})
                       for place, label, token in last_of_each(items, lambda _item: code, ctx.recent)]
            if entries:
                asked.append((line, entries))
        if not asked:
            raise SiteError(reason("no_players_empty", site=self.player.label))
        count = AskCount(ctx, sum(len(entries) for _line, entries in asked))
        ctx.progress(PHASE_PLAYERS, 0, count.total)
        # Every line at once: the gates of the parsers and of the hosts of the files bound what is asked together.
        bodies = await asyncio.gather(*(catalog_rows(line, entries, ctx.http, card_url, None, on_asked=count, known=known)
                                        for line, entries in asked))
        rows = [row for body in bodies for row in body]
        names = [str(data.get("name") or "").strip(), *(part.strip() for part in str(data.get("sub") or "").split(","))]
        year = str(data.get("year") or "").strip()
        title = " · ".join(part for part in (self.label, names[0] + (f" ({year})" if year and year != "0" else "")) if part)
        return TableDraft(names=list(dict.fromkeys(name for name in names if name)), title=title, rows=rows)
