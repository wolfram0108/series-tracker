"""A card of a video application (modules/sources/apps) as a page: its table is every episode of every line of the card.

The card lists its lines as `play_from` (codes of the application's players, apart by `$$$`) and their episodes as
`play_url` (`<label>$<token>#...` for each line, apart by `$$$`), the lines in the order the application gives. Every item
of every line is a row of the player of its line (`<application>:<line>`, labelled with the name of the line), its text the
label as the application gives it ("1", "第01集", "《万界独尊》2026新春贺岁PV"): nothing here numbers an episode, leaves an
item out or tells one line from another. The line's name, type and parsers are visible fields of its rows.

A line of type 0 lists the playlists themselves: its episodes are asked of the application's player, which measures their
frame, and an item the last table measured is not asked again. Any other line gives tokens that a parser of the
application turns into streams, and a parser allows a limited number of parses a day (it answers "今日解析次数已达上限"
when they are spent): its episodes are rows as the card lists them, not asked; the stream and its frame are asked right
before the download. The field `stream` of a row says which way it goes.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from core.contracts import RowState, TableRow
from core.reasons import reason

from ...apps import SCHEME, card_address
from ...apps.app99 import AppError
from ...players.catalog import Entry
from ...players.contract import Kind, PlayerRef, Reading
from ...players.app_lines import AppPlayer
from ...players.registry import PlayerRegistry
from .. import PHASE_RELEASE, ReadContext, SiteError, TableDraft
from ..catalogs import Listed, table_rows
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


def known_readings(rows: tuple[TableRow, ...], player: AppPlayer) -> dict[PlayerRef, Reading]:
    """What the last table measured of each episode: the token names its video for good. The ref is the one the line
    asks with (its own name), so that the read takes it instead of asking."""
    return {PlayerRef(row.player, row.link): Reading(Kind.PLAYABLE, 200, tuple(row.heights))
            for row in rows if player.owns(row.link) and row.state is RowState.PLAYABLE and row.heights}


class AppSite:
    """The cards of one application, read through its API."""

    source_type = "web_video"

    def __init__(self, player: AppPlayer, players: PlayerRegistry | None = None) -> None:
        self.player = player
        self.client = player.client
        self.name, self.label = player.name, player.label
        self.domains = (player.name,)
        self.players = players or PlayerRegistry([player])

    def configure(self, *, players: PlayerRegistry | None = None, **_wiring) -> None:
        """The registry of the module: its pacer spreads the asks of the lines with every other ask of a player."""
        if players is not None:
            self.players = players

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
        card = self._card(card_url)
        ctx.progress(PHASE_RELEASE, 0, 1)
        try:
            data = await self.client.detail(card)
            system = await self.client.system()
        except AppError as exc:
            raise SiteError(exc.reason) from exc
        ctx.progress(PHASE_RELEASE, 1, 1)
        players = {str(each.get("code") or key): each for key, each in (system.get("player") or {}).items()
                   if isinstance(each, dict)}
        listed, unasked = [], []
        known = known_readings(ctx.last_rows, self.player)
        for code, items in lines_of(data):
            shown = players.get(code, {})
            line = self.player.line(code, str(shown.get("name") or code))
            direct = shown.get("type") == 0
            fields = {"line": code, "line_name": line.label, "line_type": shown.get("type"),
                      "parsers": str(shown.get("parseUrl") or ""), "stream": "playlist" if direct else "parser"}
            entries = [Entry(key=str(place), ref=PlayerRef(line.name, self.player.link_of(card_url, code, token)),
                             text=label, site_fields={**fields, "place": place})
                       for place, label, token in items]
            entries = last_of_each(entries, lambda entry: entry.site_fields["line"], ctx.recent)
            if not entries:
                continue
            if direct:
                listed.append(Listed(line, card_url, entries, known))
            else:
                unasked += [TableRow(key=f"{line.name}:{entry.key}", player=line.name, player_label=line.label,
                                     text=entry.text, state=RowState.PLAYABLE, link=entry.ref.url,
                                     site_fields=entry.site_fields) for entry in entries]
        if not listed and not unasked:
            raise SiteError(reason("no_players_empty", site=self.player.label))
        body = await table_rows(self.players, [], ctx, listed)
        names = [str(data.get("name") or "").strip(), *(part.strip() for part in str(data.get("sub") or "").split(","))]
        # The rows in the order of the card: its lines as it gives them, the items of each in its order.
        order = {code: index for index, (code, _items) in enumerate(lines_of(data))}
        rows = sorted([*body.rows, *unasked], key=lambda row: (order.get(row.site_fields.get("line"), len(order)),
                                                               row.site_fields.get("place", 0)))
        return TableDraft(names=list(dict.fromkeys(name for name in names if name)), rows=rows, unread=body.unread)

