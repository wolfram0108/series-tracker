"""A card of a video application (modules/sources/apps) as a page: its table is every episode of every admitted line.

The card lists its lines as `play_from` (codes of the application's players, apart by `$$$`) and their episodes as
`play_url` (`<label>$<token>#...` for each line, apart by `$$$`), the lines in the order the application gives. A line is
read only when the registry of the application admits it (apps/lines.py): a line it does not admit is no player, and the
log of the read names it with the reason. Every item of an admitted line is a row of the player of its line
(`<application>:<code>`, named by its code), its text the label as the application gives it ("1", "第01集", "PV"): nothing
here numbers an episode or leaves an item of an admitted line out. The name the application gives the line, its type and
its parsers are visible fields of its rows.

The table is the card as the application lists it, read by two requests, and the frame of every row measured on its own
video: its stream asked (a parse of the application for a line of a parser), its playlist or file read, the head of the
picture given to ffprobe (players/app_lines.py). A frame is of the row it was measured on and of no other row. Each row
is asked once, at the pace the read of a card keeps (`ROWS_PER_SECOND`); a row the last table measured on itself
(`frame: measured`) keeps its frame and is not asked again, so a read after the first one asks only the episodes that are
new. A row whose episode gave no stream is `not_obtained` with the cause the host or the application gave, and is asked
again by the next read. The field `stream` of a row says how its stream comes: the playlist the line lists itself, or a
parser of the application.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

from core.contracts import RowState, TableRow
from core.reasons import reason

from ...players.contract import PlayerRef
from ...policy import Pacer
from ...apps import SCHEME, card_address
from ...apps import AppError
from ...players.app_lines import AppLines, AppPlayer
from .. import PHASE_PLAYERS, PHASE_RELEASE, ReadContext, SiteError, TableDraft
from ..recent import last_of_each

logger = logging.getLogger("sources")

# The pace the owner asked of the read of a card: a card of thousands of episodes is measured in minutes.
ROWS_PER_SECOND = 25
# A row is asked, read and measured in about three seconds: four seconds of rows at that pace in flight at most, so that a
# host that slows down holds back the asks instead of piling them up.
ROWS_IN_FLIGHT = ROWS_PER_SECOND * 4


class AppSite:
    """The cards of one application, read through its API."""

    source_type = "web_video"

    def __init__(self, player: AppPlayer, lines: AppLines) -> None:
        self.player, self.lines = player, lines
        self.client = player.client
        self.name, self.label = player.name, player.label
        self.domains = (player.name,)
        self.pacer = Pacer(1 / ROWS_PER_SECOND)
        self.in_flight = asyncio.Semaphore(ROWS_IN_FLIGHT)

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
            card = await self.client.card(self._card(card_url))
        except AppError as exc:
            raise SiteError(exc.reason) from exc
        ctx.progress(PHASE_RELEASE, 1, 1)
        lines, passed = [], []
        for each in card.lines:
            if not self.player.registry.admits(each.code):
                passed.append(f"{each.code} ({self.player.registry.why_not(each.code)})")
                continue
            fields = {"app": self.name, "line": each.code, "line_name": each.name,
                      "parsers": each.parsers, "stream": "playlist" if each.listed else "parser"}
            listed = last_of_each([(item.place, item.label, item.token) for item in each.items],
                                  lambda _item: each.code, ctx.recent)
            if listed:
                lines.append((self.lines.line(each.code), fields, listed))
        if passed:
            logger.info("приложение %s, карточка %s: линии не признаны и не читаются — %s", self.name, self._card(card_url),
                        "; ".join(passed))
        frames = await self._frames(card_url, lines, ctx)
        rows: list[TableRow] = []
        for line, fields, listed in lines:
            for place, label, token in listed:
                link = self.player.link_of(card_url, line.code, token)
                own = {**fields, "place": place}
                heights, frame = frames[link]
                # The key is of the application and its line: one source lists an episode of a line once.
                key = f"{self.name}:{line.code}:{place}"
                if heights:
                    rows.append(TableRow(key=key, player=line.name, player_label=line.label, text=label,
                                         state=RowState.PLAYABLE, heights=heights, link=link,
                                         site_fields={**own, **frame}))
                    continue
                rows.append(TableRow(key=key, player=line.name, player_label=line.label, text=label,
                                     state=RowState.NOT_OBTAINED, link=link, site_fields={**own, **frame},
                                     reason=frame["frame_failed"]))
        if not rows:
            raise SiteError(reason("no_players_empty", site=self.player.label))
        title = " · ".join(part for part in (self.label, card.names[0] + (f" ({card.year})" if card.year else "")
                                             if card.names else "") if part)
        return TableDraft(names=card.names, title=title, rows=rows)

    async def _frames(self, card_url: str, lines, ctx: ReadContext) -> dict[str, tuple[list[int], dict]]:
        """The frame of each row measured on its own video, by its link ([height] or [] with why): the last table's of
        every row it measured on itself, the others measured now — each once, not repeated, at the pace of the players."""
        kept = {row.link: (list(row.heights), {"frame": "measured"}) for row in ctx.last_rows
                if self.player.owns(row.link) and row.heights and row.site_fields.get("frame") == "measured"}
        asked = [(line, link) for line, _fields, listed in lines
                 for link in (self.player.link_of(card_url, line.code, token) for _place, _label, token in listed)
                 if link not in kept]
        done = 0
        ctx.progress(PHASE_PLAYERS, 0, len(asked))

        async def measure(line, link) -> tuple[str, tuple[list[int], dict]]:
            nonlocal done
            try:
                async with self.in_flight:
                    await self.pacer.wait()
                    reading = line.read(await line.fetch(PlayerRef(line.name, link), ctx.http, card_url))
                said = reading.message or reading.kind.value
                heights = sorted(reading.resolutions)[-1:]
                failed = said if said.startswith('{"code"') else reason("player_failed", player=line.label, cause=said)
            except Exception as exc:  # noqa: BLE001 - one row cannot take the others with it
                heights, failed = [], reason("player_failed", player=line.label, cause=type(exc).__name__)
            done += 1
            ctx.progress(PHASE_PLAYERS, done, len(asked))
            if heights:
                return link, (heights, {"frame": "measured"})
            return link, ([], {"frame_failed": failed})

        measured = dict(await asyncio.gather(*(measure(line, link) for line, link in asked)))
        return {**kept, **measured}
