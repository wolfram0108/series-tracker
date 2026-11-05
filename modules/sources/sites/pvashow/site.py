"""The site anime.pvashow.com: a title page, and every episode of the playlist it embeds.

The page embeds a playlist of AllVideo (`playlist_iframe/<n>`): the whole title, an element for each episode with the text
the site gives it and the files of its video by quality. Each element is a video of AllVideo (its number is in the
address of its files) and is a row; what the playlist says of its files is the answer of the player, so a video is not
asked again. The voice-over is the site's own, as its page names it. Nothing here numbers an episode.
"""
from __future__ import annotations

import html
import json
import re
from urllib.parse import urlparse

from core.contracts import RowVoice
from core.reasons import reason

from ...players.allvideo import AllVideoPlayer, embed_url, reading_of_files
from ...players.catalog import Entry
from ...players.contract import PlayerRef, Reading
from ...players.registry import PlayerRegistry
from .. import PHASE_RELEASE, ReadContext, SiteError, TableDraft
from ..catalogs import Listed, read_page, table_rows
from ..recent import last_of_each

SITE = "https://anime.pvashow.com"
SITE_NAME = "anime.pvashow.com"

_TITLE_PATH = re.compile(r"^/(\d+)-[^/]+\.html$")
_PLAYLIST = re.compile(r"""<iframe\b[^>]*\bsrc=["']([^"']*/playlist_iframe/\d+/?)["']""", re.IGNORECASE)
_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_VOICED = re.compile(r"<li>\s*<span>\s*Озвучено:\s*</span>(.*?)</li>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_PLAYERJS = re.compile(r"new\s+Playerjs\s*\(", re.IGNORECASE)
_FILE_KEY = re.compile(r"\bfile\s*:\s*")
# The files of a video lie under its number: `.../<number>/<number>.mp4`, `.../<number>/<number>_720p.mp4`.
_VIDEO = re.compile(r"/(\d+)/\1(?:_\d+p)?\.(?:mp4|m3u8)")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAGS.sub("", fragment)).split())


def title_names(text: str) -> list[str]:
    heading = _H1.search(text)
    return [name for name in ([_text(heading.group(1))] if heading else []) if name]


def site_voice(text: str) -> str:
    """The voice-over the page says the title has."""
    found = _VOICED.search(text)
    return _text(found.group(1)) if found else ""


def playlists(text: str) -> list[str]:
    return list(dict.fromkeys("https:" + src if src.startswith("//") else src for src in _PLAYLIST.findall(text)))


def playlist_elements(text: str) -> list[tuple[str, str, str]]:
    """(the group, the text, the file list) of each element of the playlist, a folder's elements under its title."""
    start = _PLAYERJS.search(text)
    key = _FILE_KEY.search(text, start.end()) if start else None
    if key is None:
        return []
    try:
        data, _end = json.JSONDecoder().raw_decode(text, key.end())
    except ValueError:
        return []

    def walk(items, group: str) -> list[tuple[str, str, str]]:
        found = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("comment") or item.get("title") or "").strip()
            if isinstance(item.get("folder"), list):
                found += walk(item["folder"], title)
            else:
                found.append((group, title, str(item.get("file") or "")))
        return found

    return walk(data, "")


def video_number(files: str) -> str | None:
    found = _VIDEO.search(files)
    return found.group(1) if found else None


class PvaShowSite:
    name = "pvashow"
    domains = ("pvashow.com",)
    source_type = "web_video"

    def __init__(self, players: PlayerRegistry | None = None) -> None:
        self.players = players or PlayerRegistry.standard()

    def configure(self, *, players: PlayerRegistry | None = None, **_wiring) -> None:
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
        page = (await read_page(url, ctx, SITE_NAME)).text
        addresses = playlists(page)
        if not addresses:
            raise SiteError(reason("no_players_empty", site=SITE_NAME))
        ctx.progress(PHASE_RELEASE, 1, 1 + len(addresses))
        voice = site_voice(page)
        entries: list[Entry] = []
        known: dict[PlayerRef, Reading] = {}
        for done, address in enumerate(addresses, start=2):
            answer = await read_page(address, ctx, SITE_NAME, headers={"Referer": url})
            host = urlparse(answer.final_url or address).hostname or ""
            for group, text, files in playlist_elements(answer.text):
                number = video_number(files)
                if number is None:
                    continue
                ref = PlayerRef(AllVideoPlayer.name, embed_url(host, number))
                entries.append(Entry(key=number, ref=ref, text=text, group=group, voice=RowVoice(name=voice),
                                     site_fields={"playlist": address}))
                known[ref] = reading_of_files([files])
            ctx.progress(PHASE_RELEASE, done, 1 + len(addresses))
        if not entries:
            raise SiteError(reason("title_no_videos", player=SITE_NAME))
        # One video in two places of the playlists is one row.
        first: dict[str, Entry] = {}
        for entry in entries:
            first.setdefault(entry.key, entry)
        unique = list(first.values())
        recent = last_of_each(unique, lambda each: each.group, ctx.recent)
        listed = [Listed(self.players.get(AllVideoPlayer.name), url, recent, known)]
        return TableDraft(names=title_names(page), site_voice=voice, rows=await table_rows(self.players, [], ctx, listed))
