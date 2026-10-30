"""Pure reading of the answer of the VK video embed: which page it is and what it gives.

No network, no time, no retries: the same status and text always read the same, and every
answer is exactly one kind. What to do about a kind (ask again, wait, give up) is decided elsewhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# VK titles the player page "Video embed"; its site shell, served instead of the player, is titled "VK".
EMBED_TITLE = "video embed"
NOT_FOUND_TEXT = "видеофайл не найден"
# A private video can still expose mp4 addresses in the page config, so a refusal outranks them.
REFUSALS = ("access denied", "access restricted", "protected by privacy", "private video")

_TITLE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_RESOLUTION = re.compile(r"mp4_(\d+)")
_HIDDEN = re.compile(r"<head[\s>].*?</head>|<script.*?</script>|<style.*?</style>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
MESSAGE_LENGTH = 200


class EmbedKind(str, Enum):
    PLAYABLE = "playable"
    NOT_FOUND = "not_found"
    RESTRICTED = "restricted"
    EMPTY_EMBED = "empty_embed"
    NOT_EMBED = "not_embed"
    HTTP_ERROR = "http_error"


@dataclass(frozen=True)
class EmbedAnswer:
    kind: EmbedKind
    status: int
    resolutions: tuple[int, ...] = ()
    title: str = ""
    message: str = ""


def _visible(text: str) -> str:
    return " ".join(_TAG.sub(" ", _HIDDEN.sub(" ", text)).split())[:MESSAGE_LENGTH]


def read_embed(status: int, text: str) -> EmbedAnswer:
    if status == 404:
        return EmbedAnswer(EmbedKind.NOT_FOUND, status)
    if status != 200:
        return EmbedAnswer(EmbedKind.HTTP_ERROR, status)
    lowered = text.lower()
    for phrase in REFUSALS:
        if phrase in lowered:
            return EmbedAnswer(EmbedKind.RESTRICTED, status, message=phrase)
    resolutions = tuple(sorted({int(height) for height in _RESOLUTION.findall(lowered)}))
    if resolutions:
        return EmbedAnswer(EmbedKind.PLAYABLE, status, resolutions)
    if NOT_FOUND_TEXT in lowered:
        return EmbedAnswer(EmbedKind.NOT_FOUND, status, message=NOT_FOUND_TEXT)
    found = _TITLE.search(text)
    title = " ".join(found.group(1).split()) if found else ""
    if title.lower() == EMBED_TITLE:
        return EmbedAnswer(EmbedKind.EMPTY_EMBED, status, title=title, message=_visible(text))
    return EmbedAnswer(EmbedKind.NOT_EMBED, status, title=title)
