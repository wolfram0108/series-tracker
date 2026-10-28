"""Layout of a series: which source gives the reference and where every other voice-over comes from.

Pure functions over the voice catalog of one site (a list of sources, each a player + studio with
the episodes it really has). The reference is chosen per episode (`choose_reference`) or fixed by
the user; every other voice-over is downloaded from its fullest source, with the remaining sources
of the same voice as fallbacks in priority order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .identity import NO_RULES, Equivalence, group_key, voice_groups

# Source priority, most preferred first; empirical, by bitrate. The first one wins a resolution tie.
DEFAULT_PRIORITY = ("alloha", "vk", "cvh", "allvideo", "vibix", "kodik")


@dataclass(frozen=True)
class Source:
    url: str
    player: str
    studio: str
    kind: str
    episodes: tuple[int, ...]
    links: dict[str, str] = field(default_factory=dict, compare=False)

    @classmethod
    def of(cls, raw: dict[str, Any]) -> "Source":
        return cls(raw["url"], raw["player"], raw["studio"], raw.get("kind") or "",
                   tuple(sorted(raw.get("episodes") or ())), dict(raw.get("links") or {}))


def rank(player: str, priority: tuple[str, ...] | list[str] = DEFAULT_PRIORITY,
         rules: Equivalence = NO_RULES) -> int:
    """Position of a player in the priority list; unknown players come last.

    Players the rules call the same take the place of the first of them in the list."""
    order = [rules.player(kind) for kind in priority]
    return order.index(rules.player(player)) if rules.player(player) in order else len(order)


def choose_reference(candidates: list[dict[str, Any]],
                     priority: tuple[str, ...] | list[str] = DEFAULT_PRIORITY,
                     rules: Equivalence = NO_RULES) -> dict[str, Any]:
    """Reference of one episode among its candidates ({player, resolution, ...}).

    The biggest resolution wins; a tie goes to the source higher in the priority list, then to the
    one listed first."""
    if not candidates:
        raise ValueError("нет кандидатов на референс")
    return min(enumerate(candidates), key=lambda item: (
        -(item[1].get("resolution") or 0), rank(item[1]["player"], priority, rules), item[0]))[1]


def _describe(source: Source) -> dict[str, Any]:
    described = {"url": source.url, "player": source.player, "studio": source.studio,
                 "kind": source.kind, "episodes": list(source.episodes),
                 "count": len(source.episodes), "ranges": _ranges(source.episodes)}
    if source.links:
        described["links"] = source.links
    return described


def _ranges(numbers) -> str:
    spans: list[list[int]] = []
    for number in sorted(set(numbers)):
        if spans and number == spans[-1][1] + 1:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in spans)


def plan_layout(sources: list[dict[str, Any]], mode: str = "manual",
                reference_url: str | None = None,
                priority: tuple[str, ...] | list[str] = DEFAULT_PRIORITY,
                rules: Equivalence = NO_RULES) -> dict[str, Any]:
    """Voice-overs of a series and where each one comes from.

    Manual mode: `reference_url` names the source that is the reference of every episode; its voice
    is already in the video, so other sources of that voice are skipped. Auto mode: the reference is
    decided per episode while scanning (resolution, then priority), so nothing is excluded here and
    every voice is listed with the order in which voices would win a resolution tie."""
    parsed = [Source.of(raw) for raw in sources]
    groups = voice_groups([source.studio for source in parsed], rules)
    reference = next((s for s in parsed if s.url == reference_url), None) if mode == "manual" else None
    if mode == "manual" and reference is None:
        raise ValueError("референс не найден среди источников")

    voices: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for members in groups:
        group = [parsed[i] for i in members]
        label = group[0].studio
        key = group_key([source.studio for source in group], rules)
        if reference is not None and reference in group:
            voices.append({"key": key, "label": label, "role": "reference",
                           "source": _describe(reference), "fallbacks": []})
            skipped += [{"label": label, "reason": "озвучка уже в референсе", **_describe(s)}
                        for s in group if s is not reference]
            continue
        ordered = sorted(enumerate(group), key=lambda item: (
            -len(item[1].episodes), rank(item[1].player, priority, rules), item[0]))
        primary = ordered[0][1]
        fallbacks = sorted(((i, s) for i, s in enumerate(group) if s is not primary),
                           key=lambda item: (rank(item[1].player, priority, rules), item[0]))
        voices.append({"key": key, "label": label, "role": "dub", "source": _describe(primary),
                       "fallbacks": [_describe(s) for _, s in fallbacks]})
    if mode == "auto":
        voices = [row for _, row in sorted(enumerate(voices), key=lambda item: (
            rank(item[1]["source"]["player"], priority, rules), item[0]))]
    else:
        voices.sort(key=lambda row: row["role"] != "reference")
    return {"mode": mode, "reference": _describe(reference) if reference else None,
            "voices": voices, "skipped": skipped, "priority": list(priority)}


def download_chain(row: dict[str, Any], episode: int) -> list[dict[str, Any]]:
    """Sources to try for one episode of a voice: the main one first, then fallbacks by priority."""
    return [source for source in [row["source"], *row["fallbacks"]]
            if episode in source["episodes"]]
