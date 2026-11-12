"""Static DASH with complete, separately addressed MP4 tracks (CO4K / SegmentBase).

Read XML ourselves: the signed BaseURL query must survive XML entity decoding exactly once.
Segmented, encrypted and multi-period presentations need a different downloader.
"""
from dataclasses import dataclass, field
import re
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET


class UnsupportedDash(ValueError):
    pass


def is_dash(head: bytes) -> bool:
    return re.search(rb"<(?:[\w.-]+:)?MPD(?:\s|>)", head[:4096]) is not None


@dataclass(frozen=True)
class Tracks:
    videos: tuple[str, ...] = field(repr=False)
    audio: str = field(repr=False)


def tracks(text: str, address: str) -> Tracks:
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise UnsupportedDash("DASH: XML declarations of entities are unsupported")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise UnsupportedDash("DASH: invalid or incomplete manifest") from exc

    def name(node):
        return node.tag.rsplit("}", 1)[-1]

    def children(node, tag):
        return [child for child in node if name(child) == tag]

    def base(node, parent):
        urls = children(node, "BaseURL")
        try:
            return urljoin(parent, (urls[0].text or "").strip()) if urls else parent
        except ValueError as exc:
            raise UnsupportedDash("DASH: invalid track address") from exc

    if name(root) != "MPD" or root.get("type", "static") != "static":
        raise UnsupportedDash("DASH: only static presentations are supported")
    if any(name(node) in {"ContentProtection", "SegmentTemplate", "SegmentList"} for node in root.iter()):
        raise UnsupportedDash("DASH: encrypted or segmented tracks are unsupported")
    periods = children(root, "Period")
    if len(periods) != 1:
        raise UnsupportedDash("DASH: expected one period")
    period = periods[0]
    parent = base(period, base(root, address))
    videos, audios = [], []
    for adaptation in children(period, "AdaptationSet"):
        for representation in children(adaptation, "Representation"):
            mime = representation.get("mimeType", adaptation.get("mimeType", ""))
            kind = representation.get("contentType", adaptation.get("contentType", mime.split("/")[0]))
            if kind not in {"video", "audio"}:
                continue
            chain = (root, period, adaptation, representation)
            if not any(children(node, "BaseURL") for node in chain):
                raise UnsupportedDash("DASH: track has no file address")
            for node in chain:
                for segment in children(node, "SegmentBase"):
                    if any(init.get("sourceURL") for init in children(segment, "Initialization")):
                        raise UnsupportedDash("DASH: separate initialization file is unsupported")
            url = base(representation, base(adaptation, parent))
            try:
                parsed = urlsplit(url)
            except ValueError as exc:
                raise UnsupportedDash("DASH: invalid track address") from exc
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                raise UnsupportedDash("DASH: track address must be HTTP(S)")
            target = videos if kind == "video" else audios
            if url not in target:
                target.append(url)
    if not videos or not audios:
        raise UnsupportedDash("DASH: both video and audio tracks are required")
    if len(videos) > 8:
        raise UnsupportedDash("DASH: too many video representations to measure")
    if len(audios) > 1:
        # Which of several audio tracks an episode has is a choice nobody made: refused with its reason, not guessed.
        raise UnsupportedDash("DASH: several audio tracks")
    return Tracks(tuple(videos), audios[0])
