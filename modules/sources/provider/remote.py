"""A site and a player of a provider, as the registries of the module see them (contracts/provider_socket.md, the manifest).

`RemoteSite` is a `Site`: it tells an address of the provider from any other and writes its canonical form by the rules the
manifest gives, with no network, and reads the page by asking the provider, which answers with frames; the table it returns
is the draft the provider made. `RemoteFamily` is the player of the rows of the provider: it owns the links the manifest
describes, names the video of a link, and has the provider download the file of one.

Nothing here knows what the provider reads: every fact about an address or a link is a regular expression of the manifest."""
from __future__ import annotations

import re
from contextlib import aclosing
from typing import AsyncIterator, Protocol
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError

from core.contracts.provider import (FRAME_ALIVE, FRAME_DONE, FRAME_DRAFT, FRAME_ERROR, FRAME_PROGRESS,
                                     PROVIDER_BAD_ANSWER, PROVIDER_STREAM_BROKEN, DownloadFrame, DownloadRequest,
                                     ManifestFamily, ManifestSite, ReadFrame, ReadRequest)
from core.reasons import reason

from ..players.contract import TransferRefused
from ..sites import ReadContext, SiteError, TableDraft
from .client import ProviderError


class Wire(Protocol):
    """What a site and a player of the provider ask of the connector that holds the socket."""

    def read(self, request: ReadRequest) -> AsyncIterator[ReadFrame]: ...

    def download(self, request: DownloadRequest) -> AsyncIterator[DownloadFrame]: ...

    def known_player(self, name: str) -> bool: ...

    def term_hours(self, url: str) -> int: ...


class RemoteSite:
    """A reader of pages that the provider does: its addresses by the manifest, its tables by the socket."""

    def __init__(self, entry: ManifestSite, wire: Wire) -> None:
        self.entry, self._wire = entry, wire
        self.name, self.label = entry.name, entry.label or entry.name
        self.domains = tuple(entry.domains)
        self.source_type = entry.source_type
        self.first_read_by_user = entry.first_read_by_user
        self._path = re.compile(entry.address.path)
        self._schemes = frozenset(each.lower() for each in entry.address.schemes)

    def configure(self, **_wiring) -> None:
        """The page is read by the provider alone: the wiring of the module gives it nothing."""

    def _groups(self, url: str) -> dict[str, str] | None:
        split = urlsplit(url)
        found = self._path.fullmatch(split.path) if split.scheme.lower() in self._schemes else None
        return found.groupdict() if found is not None else None

    def match_url(self, url: str) -> bool:
        return self._groups(url) is not None

    def canonical_url(self, url: str) -> str:
        groups = self._groups(url)
        if groups is None:
            raise ValueError(reason("not_a_title_page", site=self.label, url=url))
        return self.entry.address.canonical.format(**groups)

    async def read(self, url: str, ctx: ReadContext) -> TableDraft:
        request = ReadRequest(url=url, rows=ctx.rows, recent=ctx.recent, fresh=ctx.fresh, off=sorted(ctx.off),
                              last_rows=[row.model_dump(mode="json") for row in ctx.last_rows],
                              term_hours=self._wire.term_hours(url))
        draft: TableDraft | None = None
        try:
            # Closed on every way out: the closed connection is how the provider learns the read is over (or was stopped).
            async with aclosing(self._wire.read(request)) as frames:
                async for frame in frames:
                    if frame.type == FRAME_PROGRESS:
                        ctx.progress(frame.phase, frame.current, frame.total)
                    elif frame.type == FRAME_ALIVE:
                        continue
                    elif frame.type == FRAME_ERROR:
                        raise SiteError(frame.reason)
                    elif frame.type == FRAME_DRAFT:
                        draft = self._draft(frame)
                        break
        except ProviderError as exc:
            raise SiteError(exc.reason) from exc
        if draft is None:
            raise SiteError(reason(PROVIDER_STREAM_BROKEN))
        return draft

    def _draft(self, frame: ReadFrame) -> TableDraft:
        """The table the provider made, checked against what the tracker holds a table to: closed fields, and every row of a
        player the manifest names (the player of a row is the one that tells which video it is)."""
        try:
            draft = TableDraft.model_validate(frame.draft)
        except ValidationError as exc:
            raise SiteError(reason(PROVIDER_BAD_ANSWER, cause=f"draft: {exc.error_count()} errors, first: "
                                   f"{exc.errors()[0]['loc']} {exc.errors()[0]['type']}")) from exc
        for row in draft.rows:
            if not self._wire.known_player(row.player):
                raise SiteError(reason(PROVIDER_BAD_ANSWER, cause=f"the player {row.player!r} of a row is no family of the manifest"))
        return draft


class RemoteFamily:
    """The player of the rows the provider makes (a family of the manifest): links by pattern, streams by the socket."""

    series_scope = False
    # The registry finds `<family>:<line>` as its family when the player says it has lines (modules/sources/players/registry.py).
    lines = True
    stream_headers: dict[str, str] = {}

    def __init__(self, entry: ManifestFamily, wire: Wire) -> None:
        self.entry, self._wire = entry, wire
        self.name, self.label = entry.name, entry.label or entry.name
        self._owns = re.compile(entry.owns)

    def find(self, text: str, page_url: str):
        """No page embeds the player: the provider makes the links of its rows."""
        return None

    def owns(self, url: str) -> bool:
        return self._owns.fullmatch(url) is not None

    def video_of(self, url: str) -> str:
        """The video the link names, as the host numbers it (the group the manifest says, decoded); empty for a link that
        is none of the family's."""
        found = self._owns.fullmatch(url)
        return unquote(found.group(self.entry.video) or "") if found is not None else ""

    async def stream(self, url: str, http, max_height: int | None, pacer) -> dict:
        """What the downloads need to know of a link of the family before they ask for it: that the provider takes the whole
        file itself (`fetch_by`), so no address of a stream is asked, none is held and nothing is read from the network."""
        return {"stream_url": url, "headers": {}, "player_fetch": True, "fetch_by": "provider"}

    async def transfer(self, url: str, target: str, max_height: int | None, connections: int, told) -> dict:
        """The file of the link into `target`, taken by the provider. The telemetry of its stages goes on as it comes; the file
        is at `target` whole when this returns. The number of connections is the provider's own business: `connections` is
        not sent. A refusal has its kind (final, broken, unasked) as the provider says it; a provider that cannot be reached, or
        whose answer ends with no outcome, says nothing of the source."""
        request = DownloadRequest(url=url, path=target, max_height=max_height, term_hours=self._wire.term_hours(url))
        try:
            # Closed on every way out: the closed connection is how the provider learns the file is given up.
            async with aclosing(self._wire.download(request)) as frames:
                async for frame in frames:
                    if frame.type == FRAME_PROGRESS:
                        await told(frame.figures)
                    elif frame.type == FRAME_DONE:
                        return {"seconds": 0.0, "longest": 0.0, "height": frame.height}
                    elif frame.type == FRAME_ERROR:
                        raise TransferRefused(frame.reason, kind=frame.kind)
        except ProviderError as exc:
            raise TransferRefused(exc.reason, kind="unasked") from exc
        raise TransferRefused(reason(PROVIDER_STREAM_BROKEN), kind="unasked")
