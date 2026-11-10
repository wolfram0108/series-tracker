"""A card of a video application (modules/sources/apps) as a page: its table is every episode of every line of the card.

The card lists its lines as `play_from` (codes of the application's players, apart by `$$$`) and their episodes as
`play_url` (`<label>$<token>#...` for each line, apart by `$$$`), the lines in the order the application gives. Every item
of every line is a row of the player of its line (`<application>:<line>`, labelled with the name of the line), its text the
label as the application gives it ("1", "第01集", "《万界独尊》2026新春贺岁PV"): nothing here numbers an episode, leaves an
item out or tells one line from another. The line's name, type and parsers are visible fields of its rows.

The table is the card as the application lists it, read by two requests. A row's frame is the frame of its own video,
measured on it, and of no other row: the stream of an episode of most lines costs a parse of the application, and a read
measures one episode of each line — its newest — so that row alone has a frame (`frame: measured`). Every other row is `not_obtained`: its player was not asked, which the row says (`frame_not_measured`); its
frame is never carried over from another episode. A row measured by the last table keeps its frame and is not asked again;
a row whose episode gave no stream is `not_obtained` with why (`frame_failed`) and is asked once more by the next read. The ask is single:
the refusal of an application (no parser of the line, an empty answer) does not pass in seconds.
The field `stream` of a row says how its stream comes: the playlist the line lists itself, or a parser of the application.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from core.contracts import RowState, TableRow
from core.reasons import reason

from ...players.contract import PlayerRef
from ...apps import SCHEME, card_address
from ...apps.app99 import AppError
from ...players.app_lines import AppPlayer
from .. import PHASE_PLAYERS, PHASE_RELEASE, ReadContext, SiteError, TableDraft
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
        lines = []
        for code, items in lines_of(data):
            shown = players.get(code, {})
            line = self.player.line(code, str(shown.get("name") or code))
            fields = {"line": code, "line_name": line.label, "line_type": shown.get("type"),
                      "parsers": str(shown.get("parseUrl") or ""),
                      "stream": "playlist" if shown.get("type") == 0 else "parser"}
            listed = last_of_each(items, lambda _item: code, ctx.recent)
            if listed:
                lines.append((line, fields, listed))
        frames = await self._frames(card_url, lines, ctx)
        rows: list[TableRow] = []
        for line, fields, listed in lines:
            for place, label, token in listed:
                link = self.player.link_of(card_url, line.code, token)
                own = {**fields, "place": place}
                heights, frame = frames.get(link, ([], {}))
                if heights:
                    rows.append(TableRow(key=f"{line.name}:{place}", player=line.name, player_label=line.label,
                                         text=label, state=RowState.PLAYABLE, heights=heights, link=link,
                                         site_fields={**own, **frame}))
                    continue
                cause = frame.get("frame_failed") or reason("frame_not_measured", player=line.label)
                rows.append(TableRow(key=f"{line.name}:{place}", player=line.name, player_label=line.label, text=label,
                                     state=RowState.NOT_OBTAINED, link=link, site_fields={**own, **frame}, reason=cause))
        if not rows:
            raise SiteError(reason("no_players_empty", site=self.player.label))
        names = [str(data.get("name") or "").strip(), *(part.strip() for part in str(data.get("sub") or "").split(","))]
        year = str(data.get("year") or "").strip()
        title = " · ".join(part for part in (self.label, names[0] + (f" ({year})" if year and year != "0" else "")) if part)
        return TableDraft(names=list(dict.fromkeys(name for name in names if name)), title=title, rows=rows)

    async def _frames(self, card_url: str, lines, ctx: ReadContext) -> dict[str, tuple[list[int], dict]]:
        """The frame of each row measured on its own video, by its link ([height] or [] with why): the last table's of
        every row it measured itself, and the newest episode of each line measured now when the last table has not
        measured it — one ask a line, not repeated, all lines at once."""
        kept = {row.link: (list(row.heights), {"frame": "measured"}) for row in ctx.last_rows
                if self.player.owns(row.link) and row.heights and row.site_fields.get("frame") == "measured"}
        asked = []
        for line, _fields, listed in lines:
            _place, _label, token = listed[-1]
            link = self.player.link_of(card_url, line.code, token)
            if link not in kept:
                asked.append((line, link))
        done = 0
        ctx.progress(PHASE_PLAYERS, 0, len(asked))

        async def measure(line, link) -> tuple[str, tuple[list[int], dict]]:
            nonlocal done
            ref = PlayerRef(line.name, link)
            try:
                reading = line.read(await line.fetch(ref, ctx.http, card_url))
                heights, failed = sorted(reading.resolutions)[-1:], reading.message or reading.kind.value
            except Exception as exc:  # noqa: BLE001 - one line cannot take the others with it
                heights, failed = [], reason("player_failed", player=line.label, cause=type(exc).__name__)
            done += 1
            ctx.progress(PHASE_PLAYERS, done, len(asked))
            if heights:
                return link, (heights, {"frame": "measured"})
            return link, ([], {"frame_failed": failed})

        measured = dict(await asyncio.gather(*(measure(line, link) for line, link in asked)))
        return {**kept, **measured}
