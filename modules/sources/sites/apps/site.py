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
picture given to ffprobe (players/app_lines.py). A frame is of the row it was measured on and of no other row. A row the
last table measured on itself (`frame: measured`) keeps its frame and is not asked again, so a read after the first one
asks only the episodes that are new and the ones that were absent.

A row of the table is a video with its frame, or the absence of the episode on its line: there is no row left "not
measured". Every row is asked at the pace its protocol supports under the policy of every player
(policy.py: an unsure answer is asked again, the application parsing the episode anew, the pause a host names is kept).
A row whose answer stayed unsure while the whole card was being asked (no answer, a host too busy, a refusal a parser
words at one request and not at the next) is looked at again when the rest is done: an answer under the load of the
read is not yet the answer about the episode. The rows of one line left with one cause are one question, answered by
asking one of them again: the same answer says the way is down (a host that takes no connection), and the others keep
it unasked; another answer says it was the load, and every one of them is asked again, a few at a time
(players/asking.py, `look_again`, the rule of every source). The read shows this as a phase of its own (`recheck`) with
its count. A final answer (the host has no such file, a
store serves a stub for the start of the film) is asked once. An episode that gave no picture after all of it is
`absent` with what the host or the application said last,
and the log of the read names every such episode of every line. The field `stream` of a row says how its stream comes:
the playlist the line lists itself, or a parser of the application.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

from core.contracts import RowState, TableRow
from core.reasons import reason
from core.retry import Budget

from ...players.contract import Kind, PlayerRef
from ...policy import AdaptivePacer, Verdict, settle
from ...apps import SCHEME, card_address
from ...apps import AppError
from ...players.app_lines import AppLines, AppPlayer
from ...players.asking import look_again
from .. import PHASE_PLAYERS, PHASE_RECHECK, PHASE_RELEASE, ReadContext, SiteError, TableDraft
from core.row_limit import limit_of, limited
from ..recent import last_of_each

logger = logging.getLogger("sources")

# Every application resolves and measures each row remotely, whatever its protocol. Bound the whole operation across
# simultaneous cards and rechecks; a fast catalogue response is no evidence that its resolver or CDN tolerates a burst.
APP_IN_FLIGHT = 8
APP_INTERVAL_S = 0.25
APP_BUDGET = Budget(limit_s=180.0)


class AppSite:
    """The cards of one application, read through its API."""

    source_type = "web_video"
    # A card is read by the user (the button to read it) or by a synchronization: a window, a preview or the recount of a
    # layout does not begin its read, whatever the age of its table (modules/sources/table.py, `by_sync`).
    first_read_by_user = True

    def __init__(self, player: AppPlayer, lines: AppLines) -> None:
        self.player, self.lines = player, lines
        self.client = player.client
        self.name, self.label = player.name, player.label
        self.domains = (player.name,)
        self.pacer = AdaptivePacer(APP_INTERVAL_S, ceiling_s=2.0)
        self.budget = APP_BUDGET
        self.in_flight = asyncio.Semaphore(APP_IN_FLIGHT)

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
        lines, passed, off = [], [], []
        for each in card.lines:
            if not self.player.registry.admits(each.code):
                passed.append(f"{each.code} ({self.player.registry.why_not(each.code)})")
                continue
            if self.lines.line(each.code).name in ctx.off:
                off.append(each.code)
                continue
            fields = {"app": self.name, "line": each.code, "line_name": each.name,
                      "parsers": each.parsers, "stream": "playlist" if each.listed else "parser"}
            items = [(item.place, item.label, item.token) for item in each.items]
            limit = limit_of(ctx.rows)
            listed = limited(items, limit) if limit is not None else last_of_each(items, lambda _item: each.code, ctx.recent)
            if listed:
                lines.append((self.lines.line(each.code), fields, listed))
        if passed:
            logger.info("приложение %s, карточка %s: линии не признаны и не читаются — %s", self.name, self._card(card_url),
                        "; ".join(passed))
        if off:
            logger.info("приложение %s, карточка %s: линии отключены в порядке источников и не читаются — %s", self.name,
                        self._card(card_url), ", ".join(off))
        frames = await self._frames(card_url, lines, ctx)
        rows: list[TableRow] = []
        absent: dict[str, list[str]] = {}
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
                absent.setdefault(line.code, []).append(label)
                rows.append(TableRow(key=key, player=line.name, player_label=line.label, text=label,
                                     state=RowState.ABSENT, link=link, site_fields={**own, **frame},
                                     reason=frame["frame_failed"]))
        if absent:
            logger.warning("приложение %s, карточка %s: серий без видео — %s", self.name, self._card(card_url),
                           "; ".join(f"{code}: {len(labels)} ({', '.join(labels[:12])}{'…' if len(labels) > 12 else ''})"
                                     for code, labels in absent.items()))
        # A card whose every line was switched off has no row to read: that is no failure of the card.
        if not rows and not off:
            raise SiteError(reason("no_players_empty", site=self.player.label))
        title = " · ".join(part for part in (self.label, card.names[0] + (f" ({card.year})" if card.year else "")
                                             if card.names else "") if part)
        return TableDraft(names=card.names, title=title, rows=rows)

    async def _frames(self, card_url: str, lines, ctx: ReadContext) -> dict[str, tuple[list[int], dict]]:
        """The frame of each row measured on its own video, by its link ([height] or [] with why): the last table's of
        every row it measured on itself, the others asked now; the rows whose answer stayed unsure looked at again
        after the rest, cause by cause."""
        kept = {row.link: (list(row.heights), {"frame": "measured"}) for row in ctx.last_rows
                if self.player.owns(row.link) and row.heights and row.site_fields.get("frame") == "measured"}
        asked = [(line, link) for line, _fields, listed in lines
                 for link in (self.player.link_of(card_url, line.code, token) for _place, _label, token in listed)
                 if link not in kept]
        done = 0
        ctx.progress(PHASE_PLAYERS, 0, len(asked))

        async def ask(line, link) -> tuple[str, Verdict | Exception]:
            try:
                async with self.in_flight:
                    refused = self.player.refusal_for(link)
                    if refused is not None:
                        return link, Verdict(refused, 0, False)
                    return link, await settle(line, PlayerRef(line.name, link), ctx.http, card_url,
                                              budget=self.budget, pacer=self.pacer)
            except Exception as exc:  # noqa: BLE001 - one row cannot take the others with it
                return link, exc

        async def first(line, link) -> tuple[str, Verdict | Exception]:
            nonlocal done
            answer = await ask(line, link)
            done += 1
            ctx.progress(PHASE_PLAYERS, done, len(asked))
            return answer

        said = dict(await asyncio.gather(*(first(line, link) for line, link in asked)))
        # The rows left unsure are looked at again when the rest is done, line by line and cause by cause.
        line_of = {link: line for line, link in asked}

        async def again(link: str) -> Verdict | Exception:
            return (await ask(line_of[link], link))[1]

        await look_again(said, again, lambda link: line_of[link].code,
                         told=lambda current, total: ctx.progress(PHASE_RECHECK, current, total),
                         what=f"приложение {self.name}, карточка {self._card(card_url)}")
        labels = {link: line.label for line, link in asked}
        return {**kept, **{link: _frame_of(each, labels[link]) for link, each in said.items()}}


def _frame_of(said: Verdict | Exception, label: str) -> tuple[list[int], dict]:
    """What the asks of a row came to: its frame, or why its episode has none."""
    if isinstance(said, Exception):
        return [], {"frame_failed": reason("player_failed", player=label, cause=f"{type(said).__name__}: {said}"[:200])}
    reading = said.reading
    heights = sorted(reading.resolutions)[-1:] if reading.kind is Kind.PLAYABLE else []
    if heights:
        return heights, {"frame": "measured"}
    cause = reading.message or reading.kind.value
    return [], {"frame_failed": cause if cause.startswith('{"code"') else reason("player_failed", player=label, cause=cause),
                "asked": said.attempts}
