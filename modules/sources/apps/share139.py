"""The public shares of 139 Cloud (中国移动云盘) read as an application: no account, no search of the cloud — the profile names the
folders of the shares the owner connected, and each is a card whose episodes are the video files the folder lists.

`getOutLinkInfoV6` lists a folder of a share (`linkID`, `pCaID`, a window of at most 200 entries: `coLst` the files, each
`{coID, coName, coSize, extInfo}`, `caLst` the folders); `getContentInfoFromOutLink` gives the stream of a file
(`contentInfo.presentURL`, an HLS the cloud makes of the file for its preview, signed for a few hours): it is asked for each
resolve, and the address is never kept. Both are POSTs of JSON to one host, an answer is `{code, desc, data}` with code 0 on
success.

A card is a share of the profile (`shares`: the number of the card → `title`, `year`, `link_id`, `folder_id`, `password`), a
line a quality group of its files by the width the cloud stores for each (`preview4k`, `preview1080`, `preview_unknown`),
an episode a video file: its label is its name as the folder gives it, its token its content id. Nothing here reads an
episode number out of a name or leaves a file of a version out (a file without a subtitle, a repaired one): the names are
what the rules of the profile read. The preview is the cloud's own transcode (H.264, 8 bit, SDR), not the stored file.

The protocol follows the client of the owner's prototype (source-prototypes/chinese_sources, sourcekit/share139.py).
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable

import httpx

from core.reasons import reason

from ..players.http import retry_after

from . import AppCardData, AppError, AppItem, AppLine, AppPage, AppProfile, AppStream

logger = logging.getLogger("sources")

API = "https://share-kd-njs.yun.139.com/yun-share/richlifeApp/devapp/IOutLink/"
STREAM_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://yun.139.com/"}
REQUEST_TIMEOUT_S = 30.0
# The window of a folder the cloud gives at one request; a folder with more entries is a card of its own subfolders.
LISTED_AT_MOST = 200
_VIDEO = re.compile(r"\.(?:mp4|mkv|m4v|avi|ts)$", re.I)
_PARTS = re.compile(r"(\d+)")
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


class Share139Client:
    """The requests of the public shares the profile connects."""

    def __init__(self, profile: AppProfile, http: Callable[[], httpx.AsyncClient]) -> None:
        self.profile = profile
        self._settings = profile.settings
        self._http = http
        try:
            shares = json.loads(self._settings.get("shares") or "{}")
        except ValueError:
            shares = {}
        self._shares: dict[str, dict] = {str(key): each for key, each in shares.items() if isinstance(each, dict)} \
            if isinstance(shares, dict) else {}

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

    async def _listing(self, share: dict, folder: str) -> dict:
        return await self.call("getOutLinkInfoV6", {"getOutLinkInfoReq": {
            "linkID": share["link_id"], "pCaID": folder, "passwd": share.get("password") or "", "account": "",
            "bNum": 1, "eNum": LISTED_AT_MOST}})

    def _share(self, card: str) -> dict:
        share = self._shares.get(str(card))
        if share is None and not card and len(self._shares) == 1:
            share = next(iter(self._shares.values()))
        if share is None or not share.get("link_id"):
            raise AppError(reason("app_title_absent", app=self.profile.label, card=str(card)))
        return share

    # --- what every protocol gives (apps/__init__.py) -------------------------------------------------------------

    async def card(self, card_id: str) -> AppCardData:
        share = self._share(card_id)
        folder = share.get("folder_id") or "root"
        data = await self._listing(share, folder)
        if not data.get("coLst") and len(data.get("caLst") or []) == 1:
            # A share whose root holds one folder is that folder.
            data = await self._listing(share, str(data["caLst"][0]["caID"]))
        if data.get("nextPageCursor"):
            raise AppError(reason("app_folder_too_large", app=self.profile.label, limit=LISTED_AT_MOST))
        files = [each for each in data.get("coLst") or [] if isinstance(each, dict) and each.get("coID") and each.get("coName")]
        videos = sorted((each for each in files if _VIDEO.search(str(each["coName"]))), key=lambda each: natural(str(each["coName"])))
        if len(videos) != len(files):
            logger.info("приложение %s, карточка %s: в папке %d файлов не видео — не серии", self.profile.name, card_id,
                        len(files) - len(videos))
        groups: dict[str, tuple[str, list[dict]]] = {}
        for each in videos:
            code, name = quality_of(width_of(each))
            groups.setdefault(code, (name, []))[1].append(each)
        lines = [AppLine(code, name, False, "", tuple(AppItem(place, str(each["coName"]), str(each["coID"]))
                                                      for place, each in enumerate(members, start=1)))
                 for code, (name, members) in groups.items()]
        names = [str(share.get("title") or data.get("lkName") or "").strip()]
        return AppCardData([name for name in names if name], str(share.get("year") or "").strip(), lines)

    async def resolve(self, line: str, token: str, card: str = "") -> AppStream:
        share = self._share(card)
        data = await self.call("getContentInfoFromOutLink", {"getContentInfoFromOutLinkReq": {
            "contentId": token, "linkID": share["link_id"], "account": ""},
            "commonAccountInfo": {"account": "", "accountType": 1}})
        info = data.get("contentInfo")
        address = info.get("presentURL") if isinstance(info, dict) else None
        if not isinstance(address, str) or not address.startswith(("http://", "https://")):
            raise AppError(reason("app_no_stream", app=self.profile.label, parser=line, message=""))
        return AppStream(address, dict(STREAM_HEADERS))

    async def search_page(self, query: str, page: int) -> AppPage:
        """The shares the profile connects whose title holds the words: the cloud has no index of public folders."""
        words = query.strip().casefold()
        found = [{"id": key, "name": str(each.get("title") or key), "sub": "", "year": str(each.get("year") or ""), "area": "",
                  "class": "", "remarks": "", "blurb": "", "pic": ""}
                 for key, each in self._shares.items() if page == 1 and words in str(each.get("title") or key).casefold()]
        return AppPage(found, 1, len(found))
