"""The MacCMS collection sites (Jisu, Xinlang, Lzi, Subo): a title of their API, and every episode of its HLS lists.

The four sites run one engine and answer one API (`provide/vod/?ac=detail&ids=<id>`): a title is one JSON record whose
lists of episodes are `vod_play_from` (names) and `vod_play_url` (`<label>$<address>#...`), lists apart by `$$$`. A list
named `…m3u8` holds the HLS playlists; the others (`…yun`) hold the pages of the site's web player, which are no stream.
The table is every item of every HLS list, the text of an item as the site gives it ("第12集"): nothing here numbers an
episode. Each item is asked of the player MacCMS, which measures its frame; an item the last table measured is not asked.

A title is the same on the API host and on the site's own pages (`/index.php/vod/detail/id/<id>.html`): both give one
canonical address, the API request of the title, which is what `read` asks.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from core.contracts import RowState, TableRow
from core.reasons import reason

from ...players.catalog import Entry
from ...players.contract import Kind, PlayerRef, Reading
from ...players.maccms import MaccmsPlayer
from ...players.registry import PlayerRegistry
from .. import PHASE_RELEASE, ReadContext, SiteError, TableDraft, on_domain
from ..catalogs import Listed, read_page, table_rows
from ..recent import last_of_each


@dataclass(frozen=True)
class Provider:
    name: str
    domain: str
    api: str


PROVIDERS = (
    Provider("Jisu", "jszyapi.com", "https://jszyapi.com/api.php/provide/vod/"),
    Provider("Xinlang", "xinlangapi.com", "https://api.xinlangapi.com/xinlangapi.php/provide/vod/"),
    Provider("Lzi", "lziapi.com", "https://cj.lziapi.com/api.php/provide/vod/"),
    Provider("Subo", "subocaiji.com", "https://subocaiji.com/api.php/provide/vod/"),
)
LIST_SEPARATOR = "$$$"
ITEM_SEPARATOR = "#"

_DETAIL_PATH = re.compile(r"/id/(\d+)")


def provider_of(url: str) -> Provider | None:
    return next((each for each in PROVIDERS if on_domain(url, (each.domain,))), None)


def title_id(url: str) -> str | None:
    """The number of the title: the `ids` of an API request, or the `/id/<n>` of a page of the site."""
    parts = urlsplit(url)
    found = _DETAIL_PATH.search(parts.path)
    value = found.group(1) if found else (parse_qs(parts.query).get("ids") or [""])[0]
    return value if value.isdigit() else None


def hls_items(record: dict) -> list[tuple[str, int, str, str]]:
    """(list, place in it, label, playlist) of every item of the HLS lists of a title, in the order the site gives them."""
    items = []
    names = str(record.get("vod_play_from") or "").split(LIST_SEPARATOR)
    lists = str(record.get("vod_play_url") or "").split(LIST_SEPARATOR)
    for name, listed in zip(names, lists):
        if "m3u8" not in name.lower():
            continue
        for place, item in enumerate(listed.split(ITEM_SEPARATOR), start=1):
            label, _, address = item.partition("$")
            if address.strip().startswith(("https://", "http://")):
                items.append((name, place, label.strip(), address.strip()))
    return items


def title_names(record: dict) -> list[str]:
    """The name of the title, then the other names the site gives it (`vod_sub`, comma-separated)."""
    names = [str(record.get("vod_name") or "").strip()]
    names += [each.strip() for each in re.split(r"[,，/]", str(record.get("vod_sub") or ""))]
    return list(dict.fromkeys(name for name in names if name))


def known_readings(rows: tuple[TableRow, ...]) -> dict[PlayerRef, Reading]:
    """What the last table measured of each playlist: the address of a playlist names its video for good."""
    return {PlayerRef(MaccmsPlayer.name, row.link): Reading(Kind.PLAYABLE, 200, tuple(row.heights),
                                                            files=tuple((height, MaccmsPlayer.video_of(row.link))
                                                                        for height in row.heights))
            for row in rows if row.player == MaccmsPlayer.name and row.state is RowState.PLAYABLE and row.heights}


class MaccmsSite:
    name = "maccms"
    domains = tuple(each.domain for each in PROVIDERS)
    source_type = "web_video"

    def __init__(self, players: PlayerRegistry | None = None) -> None:
        self.players = players or PlayerRegistry.standard()

    def configure(self, *, players: PlayerRegistry | None = None, **_wiring) -> None:
        if players is not None:
            self.players = players

    def match_url(self, url: str) -> bool:
        try:
            return provider_of(url) is not None and title_id(url) is not None
        except ValueError:
            return False

    def canonical_url(self, url: str) -> str:
        """The API request of the title: one address whichever page of the site or form of the request was given."""
        provider, number = provider_of(url), title_id(url)
        if provider is None or number is None:
            raise ValueError(reason("not_a_title_page", site=provider.name if provider else self.name, url=url))
        return f"{provider.api}?ac=detail&ids={number}"

    async def read(self, url: str, ctx: ReadContext) -> TableDraft:
        provider = provider_of(url)
        site = provider.name if provider is not None else self.name
        ctx.progress(PHASE_RELEASE, 0, 1)
        answer = await read_page(url, ctx, site)
        ctx.progress(PHASE_RELEASE, 1, 1)
        try:
            records = json.loads(answer.text.lstrip("﻿")).get("list") or []
        except (ValueError, AttributeError) as exc:
            raise SiteError(reason("player_not_json", player=site)) from exc
        if not records or not isinstance(records[0], dict):
            raise SiteError(reason("title_absent", site=site, url=url))
        record = records[0]
        player = self.players.get(MaccmsPlayer.name)
        entries = [Entry(key=f"{name}:{place}", ref=PlayerRef(MaccmsPlayer.name, MaccmsPlayer.link_of(url, playlist)),
                         text=label, site_fields={"site": site, "list": name, "place": place})
                   for name, place, label, playlist in hls_items(record)]
        if not entries:
            raise SiteError(reason("no_players_empty", site=site))
        entries = last_of_each(entries, lambda entry: entry.site_fields["list"], ctx.recent)
        listed = Listed(player, url, entries, known_readings(ctx.last_rows))
        body = await table_rows(self.players, [], ctx, [listed])
        return TableDraft(names=title_names(record), rows=body.rows, unread=body.unread)
