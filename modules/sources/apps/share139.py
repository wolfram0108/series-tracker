"""The public shares of 139 Cloud (中国移动云盘) read as an application: no account — a card is a folder of a public share, and its
episodes are the video files the folder lists.

`getOutLinkInfoV6` lists a folder of a share (`linkID`, `pCaID`, a window of at most 200 entries: `coLst` the files, each
`{coID, coName, coSize, extInfo}`, `caLst` the folders; a folder of more is read window by window); `getContentInfoFromOutLink`
gives the stream of a file (`contentInfo.presentURL`, an HLS the cloud makes of the file for its preview, signed for a few
hours): it is asked for each resolve, and the address is never kept. Both are POSTs of JSON to one host, an answer is
`{code, desc, data}` with code 0 on success.

Where the cards come from. The shares the owner connected by hand are in the profile (`shares`: the number of the card →
`title`, `year`, `link_id`, `folder_id`, `password`). The rest the search finds: 139 Cloud has no search of what others
published, so the search reads the public indexes that announce such shares (apps/share139_search.py), looks into the first
few candidates through the cloud itself (a folder its owner deleted is no candidate; one that wants a password says so) and
offers the folders of the shares it found. Each folder it offers gets a number (`share139_folders`), the card in the address of
a source, so a chosen card is read again without the search. A candidate is the folder somebody published: what it holds is
known only when its card is read. A title no index knows has no card: the search says where it looked, and the way in is the
link a person found elsewhere — pasted where the title is asked, it is looked into the same way and offered the same way.

A line of a card is a quality group of its files by the width the cloud stores for each (`preview4k`, `preview1080`,
`preview_unknown`), an episode a video file: its label is its name as the folder gives it, its token its content id. Nothing
here reads an episode number out of a name or leaves a file of a version out (a file without a subtitle, a repaired one): the
names are what the rules of the profile read. The preview is the cloud's own transcode (H.264, 8 bit, SDR), not the stored
file. A folder of folders and no file is not read for the files of its subfolders: it is a card of its own for each of them,
and the search offers them so.

The protocol follows the client of the owner's prototype (source-prototypes/chinese_sources, sourcekit/share139.py, and for the
search source-prototypes/share139-search-20261113).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol
from urllib.parse import urlsplit

import httpx

from core.reasons import describe, reason

from ..players.http import retry_after

from . import AppCardData, AppError, AppItem, AppLine, AppPage, AppProfile, AppStream
from .share139_search import (DEFAULT_SOURCES, Candidate, IndexAnswerError, normalize, pansou_candidates, pasted_shares,
                              telegram_candidates, title_matches)

logger = logging.getLogger("sources")

API = "https://share-kd-njs.yun.139.com/yun-share/richlifeApp/devapp/IOutLink/"
STREAM_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://yun.139.com/"}
REQUEST_TIMEOUT_S = 30.0
# The window of a folder the cloud gives at one request; a folder of more is read window by window, at most CATALOG_REQUESTS
# of them (a card that is not read whole is no card: it says so, and is not shown as complete).
LISTED_AT_MOST = 200
CATALOG_REQUESTS = 8
# The cloud and the indexes are asked one request after another, a start no sooner than this after the last one.
REQUEST_INTERVAL_S = 1.5
# One search looks into this many candidates through the cloud itself; the rest are offered unchecked.
VERIFIED_AT_MOST = 3
# A pasted text is read for this many shares, and a pasted share of folders is offered as at most this many folders.
PASTED_AT_MOST = 5
PASTED_FOLDERS_AT_MOST = 30
PAGE_SIZE = 20
SEARCH_SOURCES_AT_MOST = 6
# How long what a search learned is believed: the candidates of a query (none found: a short while), the state of a folder.
FOUND_FOR_S, NOTHING_FOUND_FOR_S = 1800.0, 120.0
ACCESSIBLE_FOR_S, REFUSED_FOR_S = 300.0, 120.0
# An index that refused or failed is not asked again for this long.
SOURCE_COOLDOWN_S = 600.0
INDEX_ANSWER_AT_MOST = 2 * 1024 * 1024
# An index answers in a second or two when it is up: one that does not in this time is down for this search (the window of the
# search waits for it), and is not asked again for SOURCE_COOLDOWN_S.
INDEX_TIMEOUT_S = 10.0
_VIDEO = re.compile(r"\.(?:mp4|mkv|m4v|avi|ts)$", re.I)
_PARTS = re.compile(r"(\d+)")
_GONE = re.compile(r"不存在|取消|已失效|已过期|deleted|expired", re.I)
_PASSWORD = re.compile(r"提取码|密码|passwd|password", re.I)
_CHANNEL = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,63}")
QUALITIES = (("preview4k", 3840, "139 public preview 4K SDR"), ("preview1080", 1280, "139 public preview 1080p"),
             ("preview_unknown", 0, "139 public preview, quality unmeasured"))


def natural(name: str) -> list:
    """The order of file names a person reads: the numbers in a name by their value (`E2` before `E10`)."""
    return [int(part) if part.isdigit() else part.casefold() for part in _PARTS.split(name)]


def width_of(entry: dict) -> int:
    """The width the cloud stores for a file (`extInfo`, an object or JSON of one); 0 when it states none."""
    meta = entry.get("extInfo") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {}
    try:
        return int(meta.get("width") or 0) if isinstance(meta, dict) else 0
    except (ValueError, TypeError):
        return 0


def quality_of(width: int) -> tuple[str, str]:
    code, _least, name = next(each for each in QUALITIES if width >= each[1])
    return code, name


def _message_of(error: AppError) -> str:
    """What the cloud said in words in a refusal (`message` of its reason)."""
    try:
        return str(json.loads(error.reason).get("message") or "")
    except (ValueError, AttributeError):
        return ""


def search_sources_of(settings: dict | None) -> list[dict]:
    """The indexes the search reads: the profile's `search_sources` (JSON; an empty list is a search of the shares already
    known only), else the default ones. A list that is none of that is a fault of the environment and stops the start."""
    said = (settings or {}).get("search_sources")
    try:
        sources = json.loads(said) if isinstance(said, str) and said.strip() else None
    except ValueError as exc:
        raise ValueError("share139: `search_sources` is not JSON") from exc
    if sources is None:
        return [dict(each) for each in DEFAULT_SOURCES]
    if not isinstance(sources, list) or len(sources) > SEARCH_SOURCES_AT_MOST:
        raise ValueError(f"share139: `search_sources` is a list of at most {SEARCH_SOURCES_AT_MOST} indexes")
    for each in sources:
        if not isinstance(each, dict):
            raise ValueError("share139: an index of `search_sources` is an object")
        if each.get("kind") == "telegram" and _CHANNEL.fullmatch(str(each.get("channel") or "")):
            continue
        if each.get("kind") == "pansou" and urlsplit(str(each.get("url") or "")).scheme in ("http", "https") \
                and 0 < len(each.get("channels") or ["-"]) <= 3:
            continue
        raise ValueError(f"share139: the index {each!r} is none of `telegram` (a `channel`) and `pansou` (an `url`, up to three `channels`)")
    return sources


class ShareFolders(Protocol):
    """The memory of the folders the search found (the sources module keeps it: `share139_folders`)."""

    async def share_folder_register(self, link_id: str, folder_id: str, *, password: str = "", title: str = "",
                                    publisher_url: str = "", published_at: str = "", found_by: str = "") -> int: ...

    async def share_folder(self, card: int) -> dict | None: ...

    async def share_folders(self) -> list[dict]: ...


@dataclass(frozen=True)
class Checked:
    """What looking into the root of a share said: whether it can be read, and what it holds."""
    state: str                      # accessible | password_required | gone | uncertain
    until: float
    folders: tuple[dict, ...] = ()  # its folders, when it is accessible and holds no video file
    holds_videos: bool = False
    name: str = ""                  # the name the cloud gives the share (`lkName`)


@dataclass(frozen=True)
class Found:
    """The candidates a query was answered with, and what went wrong asking the indexes."""
    until: float
    candidates: tuple[Candidate, ...]
    errors: tuple[str, ...]
    answered: int  # how many indexes answered at all (an index that found none is an answer)
    asked: tuple[str, ...] = ()   # which ones answered, and which did not
    failed: tuple[str, ...] = ()


class Share139Client:
    """The requests of the public shares: those the profile connects and those the search finds."""

    def __init__(self, profile: AppProfile, http: Callable[[], httpx.AsyncClient], *, folders: ShareFolders | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable = asyncio.sleep,
                 interval: float = REQUEST_INTERVAL_S) -> None:
        self.profile = profile
        self._settings = profile.settings
        self._http = http
        self._folders = folders
        self._clock, self._sleep, self._interval = clock, sleep, interval
        try:
            shares = json.loads(self._settings.get("shares") or "{}")
        except ValueError:
            shares = {}
        self._shares: dict[str, dict] = {str(key): each for key, each in shares.items() if isinstance(each, dict)} \
            if isinstance(shares, dict) else {}
        self._sources = search_sources_of(self._settings)
        self._started = float("-inf")
        self._pacing = asyncio.Lock()
        self._searching = asyncio.Lock()
        self._found: dict[str, Found] = {}
        self._cooldowns: dict[str, float] = {}
        self._checked: dict[tuple[str, str], Checked] = {}

    # --- the requests -----------------------------------------------------------------------------------------------

    async def _start(self) -> None:
        """One request after another: the next starts no sooner than the interval after the last one."""
        async with self._pacing:
            wait = self._started + self._interval - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._started = self._clock()

    async def call(self, method: str, body: dict) -> dict:
        """The `data` of a successful answer of the cloud to `method`."""
        began = time.monotonic()
        try:
            response = await self._http().post(API + method, json=body, headers={**STREAM_HEADERS, "Content-Type": "application/json"},
                                               timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=8.0))
        except httpx.HTTPError as exc:
            raise AppError(reason("app_unreachable", app=self.profile.label, error=type(exc).__name__), True) from exc
        logger.info("приложение %s, %s: статус %s (%.1f с)", self.profile.name, method, response.status_code, time.monotonic() - began)
        if response.status_code != 200:
            raise AppError(reason("app_status", app=self.profile.label, status=response.status_code),
                           response.status_code >= 500 or response.status_code == 429,
                           status=response.status_code, retry_after=retry_after(response))
        try:
            answer = response.json()
        except ValueError:
            answer = None
        if not isinstance(answer, dict):
            raise AppError(reason("app_answer_broken", app=self.profile.label, step=method))
        if str(answer.get("code")) != "0":
            raise AppError(reason("app_refused", app=self.profile.label, step=method,
                                  message=str(answer.get("desc") or answer.get("code") or "")[:300]))
        if not isinstance(answer.get("data"), dict):
            raise AppError(reason("app_answer_broken", app=self.profile.label, step=method))
        return answer["data"]

    async def _listing(self, share: dict, folder: str, start: int = 1) -> dict:
        await self._start()
        return await self.call("getOutLinkInfoV6", {"getOutLinkInfoReq": {
            "linkID": share["link_id"], "pCaID": folder, "passwd": share.get("password") or "", "account": "",
            "bNum": start, "eNum": start + LISTED_AT_MOST - 1}})

    async def _folder(self, share: dict, folder: str) -> tuple[list[dict], list[dict], str]:
        """(the files, the folders, the name of the share) of a folder, window by window. A page that comes again, or a folder
        longer than the windows allowed, is an error: a part is never given as the whole."""
        files, folders, seen, name = [], [], set(), ""
        for number in range(CATALOG_REQUESTS):
            data = await self._listing(share, folder, 1 + number * LISTED_AT_MOST)
            name = name or str(data.get("lkName") or "")
            page_files = [each for each in data.get("coLst") or [] if isinstance(each, dict)]
            page_folders = [each for each in data.get("caLst") or [] if isinstance(each, dict)]
            marks = tuple([str(each.get("coID")) for each in page_files] + [str(each.get("caID")) for each in page_folders])
            if marks and marks in seen:
                raise AppError(reason("app_pagination_repeated", app=self.profile.label))
            seen.add(marks)
            files += page_files
            folders += page_folders
            if not data.get("nextPageCursor") and len(page_files) + len(page_folders) < LISTED_AT_MOST:
                return files, folders, name
            if not marks:
                raise AppError(reason("app_answer_broken", app=self.profile.label, step="getOutLinkInfoV6"))
        raise AppError(reason("app_folder_too_large", app=self.profile.label, limit=CATALOG_REQUESTS * LISTED_AT_MOST))

    async def _share(self, card: str) -> dict:
        """The share of a card: one of the profile, or one the search found."""
        share = self._shares.get(str(card))
        if share is None and not card and len(self._shares) == 1:
            share = next(iter(self._shares.values()))
        if share is None and self._folders is not None and str(card).isdigit():
            held = await self._folders.share_folder(int(card))
            if held is not None:
                share = {"link_id": held["link_id"], "folder_id": held["folder_id"], "password": held["password"],
                         "title": held["title"]}
        if share is None or not share.get("link_id"):
            raise AppError(reason("app_title_absent", app=self.profile.label, card=str(card)))
        return share

    # --- what every protocol gives (apps/__init__.py) -------------------------------------------------------------

    async def card(self, card_id: str) -> AppCardData:
        share = await self._share(card_id)
        files, folders, name = await self._folder(share, share.get("folder_id") or "root")
        if not files and len(folders) == 1:
            # A share whose root holds one folder is that folder.
            files, folders, name = await self._folder(share, str(folders[0]["caID"]))
        files = [each for each in files if each.get("coID") and each.get("coName")]
        videos = sorted((each for each in files if _VIDEO.search(str(each["coName"]))), key=lambda each: natural(str(each["coName"])))
        if len(videos) != len(files):
            logger.info("приложение %s, карточка %s: в папке %d файлов не видео — не серии", self.profile.name, card_id,
                        len(files) - len(videos))
        if folders and not videos:
            # A folder of folders is no card of episodes: each of its folders is one, and which is wanted is the owner's word.
            raise AppError(reason("app_folder_selection_required", app=self.profile.label,
                                  folders=", ".join(str(each.get("caName") or each.get("caID")) for each in folders[:12])))
        if folders:
            logger.info("приложение %s, карточка %s: в папке подпапок %d — читаются файлы самой папки", self.profile.name, card_id,
                        len(folders))
        groups: dict[str, tuple[str, list[dict]]] = {}
        for each in videos:
            code, quality = quality_of(width_of(each))
            groups.setdefault(code, (quality, []))[1].append(each)
        lines = [AppLine(code, quality, False, "", tuple(AppItem(place, str(each["coName"]), str(each["coID"]))
                                                         for place, each in enumerate(members, start=1)))
                 for code, (quality, members) in groups.items()]
        names = [str(share.get("title") or name or "").strip()]
        return AppCardData([each for each in names if each], str(share.get("year") or "").strip(), lines)

    async def resolve(self, line: str, token: str, card: str = "") -> AppStream:
        share = await self._share(card)
        data = await self.call("getContentInfoFromOutLink", {"getContentInfoFromOutLinkReq": {
            "contentId": token, "linkID": share["link_id"], "account": ""},
            "commonAccountInfo": {"account": "", "accountType": 1}})
        info = data.get("contentInfo")
        address = info.get("presentURL") if isinstance(info, dict) else None
        if not isinstance(address, str) or not address.startswith(("http://", "https://")):
            raise AppError(reason("app_no_stream", app=self.profile.label, parser=line, message=""))
        return AppStream(address, dict(STREAM_HEADERS))

    # --- the search -------------------------------------------------------------------------------------------------

    async def search_page(self, query: str, page: int) -> AppPage:
        """The shares of the profile whose title holds the words, the folders the search found before, and what the public
        indexes offer now. A page of the results is `PAGE_SIZE` cards; what the search looks into is bounded (see below).
        A text that holds the address of a public share is no title: it is the share a person found where no index knows
        the title, and the answer is the folders of that share (the indexes are not asked)."""
        pasted = pasted_shares(query)
        cards = [] if pasted else [self._card(key, each.get("title") or key, year=each.get("year"), state="")
                                   for key, each in self._shares.items()
                                   if not query.strip() or title_matches(query, str(each.get("title") or key))]
        note = ""
        if query.strip() and self._folders is not None and (pasted or normalize(query)):
            async with self._searching:
                if pasted:
                    cards += await self._pasted_cards(pasted)
                else:
                    found, note = await self._found_cards(query.strip())
                    cards += found
        size = len(cards)
        return AppPage(cards[(page - 1) * PAGE_SIZE: page * PAGE_SIZE], max(1, math.ceil(size / PAGE_SIZE)), size,
                       "" if cards else note)

    @staticmethod
    def _card(card: object, name: object, *, year: object = "", state: str = "", where: str = "") -> dict:
        return {"id": str(card), "name": str(name), "sub": "", "year": str(year or ""), "area": "", "class": "",
                "remarks": state, "blurb": where, "pic": ""}

    def _checker(self) -> Callable[[str, str], Awaitable[Checked | None]]:
        """What looking into a share says, for one search: of the shares it is asked about the first `VERIFIED_AT_MOST` that
        were not looked into lately are looked into, the rest are not known (None). What is known holds for its term, by the
        share and the code it was asked with."""
        looked = 0

        async def seen(link_id: str, password: str) -> Checked | None:
            nonlocal looked
            checked = self._checked.get((link_id, password))
            if (checked is None or checked.until <= self._clock()) and looked < VERIFIED_AT_MOST:
                looked += 1
                checked = await self._look(link_id, password)
            if checked is not None and checked.until > self._clock():
                return checked
            return None

        return seen

    async def _offer(self, seen: Callable[[str, str], Awaitable[Checked | None]], candidates: list[Candidate],
                     folders_of: Callable[[Checked], list[dict]], dropped: list[str],
                     titles: dict[tuple[str, str], str] | None = None) -> list[dict]:
        """The cards of the shares of the candidates, each looked into (`seen`): a share its owner deleted is dropped (its link
        goes to `dropped`), one that wants a password is offered with that said, one not looked into is offered unchecked; a
        share of folders and no video file is offered as each of the folders `folders_of` picks of it. The number of a card
        is the one the memory of the folders gives. A candidate that has no title of its own (a pasted link) keeps the title
        `titles` has of its folder: the name the cloud gives a share is what is offered only for a folder not known yet."""
        assert self._folders is not None
        cards = []
        for candidate in candidates:
            checked = await seen(candidate.link_id, candidate.password)
            if checked is not None and checked.state == "gone":
                dropped.append(candidate.link_id)
                continue
            state = checked.state if checked is not None else "unchecked"
            title = candidate.title or (checked.name if checked is not None else "") or candidate.link_id
            picked = folders_of(checked) if checked is not None and checked.state == "accessible" and not checked.holds_videos \
                and len(checked.folders) > 1 else []
            for folder, name in [(str(each["caID"]), f"{title} / {each.get('caName') or each['caID']}") for each in picked] \
                    or [("root", title)]:
                name = (not candidate.title and (titles or {}).get((candidate.link_id, folder))) or name
                card = await self._folders.share_folder_register(
                    candidate.link_id, folder, password=candidate.password, title=name, publisher_url=candidate.publisher_url,
                    published_at=candidate.published_at, found_by=candidate.found_by)
                cards.append(self._card(card, name, state=state, where=candidate.publisher_url))
        return cards

    async def _found_cards(self, query: str) -> tuple[list[dict], str]:
        """The cards of the folders the search found for the words: the candidates the indexes offer now, then the folders
        found before that they do not offer again; and, when there is none, what the search can say of that (a reason: where
        it looked). Each share is looked into (the first `VERIFIED_AT_MOST` that were not looked into lately): one its owner
        deleted is dropped, one that wants a password is offered with that said, and a share of folders is offered as each of
        the folders the words name."""
        assert self._folders is not None
        found = await self._candidates(query)
        fresh = sorted(found.candidates, key=lambda each: each.published_at, reverse=True)
        offered = {(each.link_id, "root") for each in fresh}
        older = [each for each in await self._folders.share_folders()
                 if title_matches(query, each["title"]) and (each["link_id"], each["folder_id"]) not in offered]
        seen, dropped = self._checker(), []
        cards = await self._offer(seen, fresh, lambda checked: [each for each in checked.folders
                                                                if title_matches(query, str(each.get("caName") or ""))], dropped)
        for each in older:
            checked = await seen(each["link_id"], each["password"])
            if checked is not None and checked.state == "gone":
                dropped.append(each["link_id"])
                continue
            cards.append(self._card(each["card"], each["title"], state=checked.state if checked is not None else "unchecked",
                                    where=each["publisher_url"]))
        if dropped:
            logger.info("приложение %s, поиск «%s»: папки удалены владельцами и не предлагаются — %s", self.profile.name, query,
                        ", ".join(dropped))
        if not cards and found.errors and not found.answered:
            raise AppError(reason("app_search_unavailable", app=self.profile.label, causes="; ".join(found.errors)), True)
        if cards or not found.answered:
            return cards, ""
        # Nothing offered: the owner is told where the search looked, and that a link found elsewhere is a way in.
        if found.failed:
            return cards, reason("app_share_unlisted_partial", app=self.profile.label, indexes=", ".join(found.asked),
                                 failed=", ".join(found.failed))
        return cards, reason("app_share_unlisted", app=self.profile.label, indexes=", ".join(found.asked))

    async def _pasted_cards(self, pasted: list[tuple[str, str]]) -> list[dict]:
        """The cards of the shares a person pasted: each looked into through the cloud, a share of folders offered as every one
        of its folders (which is wanted is the owner's word). Shares all of which their owners deleted are no answer but the
        words saying so."""
        candidates = [Candidate(link, code, "", "", "", "pasted") for link, code in pasted[:PASTED_AT_MOST]]
        dropped: list[str] = []
        known = {(each["link_id"], each["folder_id"]): each["title"] for each in await self._folders.share_folders() if each["title"]}
        cards = await self._offer(self._checker(), candidates, lambda checked: list(checked.folders[:PASTED_FOLDERS_AT_MOST]), dropped,
                                  known)
        if not cards and dropped:
            raise AppError(reason("app_share_gone", app=self.profile.label, links=", ".join(dropped)))
        return cards

    async def _look(self, link_id: str, password: str) -> Checked:
        """Into the root of a share, through the cloud itself: it is there and can be read, it wants a password, its owner
        deleted it, or the answer is not sure (the cloud was unreachable)."""
        share = {"link_id": link_id, "password": password}
        try:
            files, folders, name = await self._folder(share, "root")
        except AppError as exc:
            words = _message_of(exc)
            state = "gone" if _GONE.search(words) else "password_required" if _PASSWORD.search(words) else "uncertain"
            checked = Checked(state, self._clock() + REFUSED_FOR_S)
        else:
            holds_videos = any(_VIDEO.search(str(each.get("coName") or "")) for each in files)
            checked = Checked("accessible", self._clock() + ACCESSIBLE_FOR_S, tuple(folders), holds_videos, name)
        self._checked[(link_id, password)] = checked
        return checked

    async def _candidates(self, query: str) -> Found:
        """The shares the indexes offer for the words, the answer of the last half hour when there is one (and the answer
        «none» for two minutes): the indexes are asked one after another, never all of them at once."""
        key = normalize(query)
        held = self._found.get(key)
        if held is not None and held.until > self._clock():
            return held
        found: dict[str, Candidate] = {}
        errors: list[str] = []
        asked: list[str] = []
        failed: list[str] = []
        for spec in self._sources:
            if spec.get("fallback") and found:
                continue
            identity = json.dumps(spec, sort_keys=True)
            name = spec.get("channel") or urlsplit(str(spec.get("url"))).hostname or str(spec.get("kind"))
            if self._cooldowns.get(identity, 0.0) > self._clock():
                errors.append(describe(reason("app_search_source_cooling", app=self.profile.label, source=str(spec.get("kind")))))
                failed.append(name)
                continue
            try:
                for each in await self._ask_index(spec, query):
                    found.setdefault(each.link_id, each)
                asked.append(name)
            except AppError as exc:
                errors.append(describe(exc.reason))
                failed.append(name)
                logger.warning("приложение %s, поиск «%s»: индекс %s не ответил — %s", self.profile.name, query, name,
                               describe(exc.reason))
                if exc.transient or exc.status in (401, 403, 429):
                    self._cooldowns[identity] = self._clock() + SOURCE_COOLDOWN_S
        result = Found(self._clock() + (FOUND_FOR_S if found else NOTHING_FOUND_FOR_S), tuple(found.values()), tuple(errors),
                       len(asked), tuple(asked), tuple(failed))
        if found or not errors:
            self._found[key] = result
        if len(self._found) > 200:
            self._found.pop(min(self._found, key=lambda each: self._found[each].until), None)
        return result

    async def _ask_index(self, spec: dict, query: str) -> list[Candidate]:
        await self._start()
        headers = {"User-Agent": "Mozilla/5.0"}
        if spec["kind"] == "telegram":
            address = httpx.URL("https://t.me/s/" + str(spec["channel"]), params={"q": query})
        else:
            address = httpx.URL(str(spec["url"]), params={"kw": query, "res": "all", "src": "tg", "conc": 1, "cloud_types": "mobile",
                                                         "channels": ",".join(spec.get("channels") or ["yunpan139"])})
            if spec.get("token"):
                headers["Authorization"] = "Bearer " + str(spec["token"])
        body = await self._fetch(address, headers)
        try:
            if spec["kind"] == "telegram":
                return telegram_candidates(body.decode("utf-8"), query)
            return pansou_candidates(json.loads(body), query)
        except (ValueError, UnicodeError, IndexAnswerError) as exc:
            raise AppError(reason("app_answer_broken", app=self.profile.label, step=f"search:{spec['kind']}")) from exc

    async def _fetch(self, address: httpx.URL, headers: dict) -> bytes:
        """The body of an answer of an index, at most `INDEX_ANSWER_AT_MOST` of it."""
        began = time.monotonic()
        try:
            async with self._http().stream("GET", address, headers=headers, follow_redirects=False,
                                           timeout=httpx.Timeout(INDEX_TIMEOUT_S, connect=5.0)) as response:
                logger.info("приложение %s, поиск в %s: статус %s (%.1f с)", self.profile.name, address.host, response.status_code,
                            time.monotonic() - began)
                if response.status_code != 200:
                    raise AppError(reason("app_status", app=self.profile.label, status=response.status_code),
                                   response.status_code >= 500 or response.status_code == 429, status=response.status_code,
                                   retry_after=retry_after(response))
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > INDEX_ANSWER_AT_MOST:
                        raise AppError(reason("app_answer_broken", app=self.profile.label, step="search"))
                return body
        except httpx.HTTPError as exc:
            raise AppError(reason("app_unreachable", app=self.profile.label, error=type(exc).__name__), True) from exc
