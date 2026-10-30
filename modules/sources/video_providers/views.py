"""What the table of a release says, as pure functions: the best stream of an episode, the facts of a scan, the report of the
players, the coverage by ranges. Nothing here asks a host or a clock."""
from __future__ import annotations

from collections import Counter
from typing import Iterable, Sequence

from core.reasons import reason

from ..players.contract import Player
from .ranges import episode_ranges
from .table import Row


def pick(row: Row, off: frozenset[str], priority: dict[str, int]) -> tuple[int, str, str] | None:
    """The stream that wins an episode: the highest resolution, the player of the higher priority on a tie."""
    candidates = row.candidates(off)
    return max(candidates, key=lambda candidate: (candidate[0], -priority[candidate[1]])) if candidates else None


def facts(rows: Iterable[Row], off: frozenset[str], priority: dict[str, int]) -> tuple[list[dict], list[Row]]:
    """The facts a scan reports, oldest first, and the rows left without a stream."""
    found: list[dict] = []
    skipped: list[Row] = []
    for row in rows:
        chosen = pick(row, off, priority)
        if chosen is None:
            skipped.append(row)
            continue
        resolution, source, url = chosen
        found.append({
            "title": row.title,
            "url": url,
            "download_url": row.cells[source].download,
            "publication_date": row.publication_date,
            "resolution": resolution,
        })
    found.sort(key=lambda fact: fact["publication_date"])
    return found, skipped


def availability_report(rows: Sequence[Row], players: Sequence[Player], off: frozenset[str],
                        priority: dict[str, int]) -> dict:
    """Aggregate per-player availability for the series status; a disabled player is never counted."""
    chosen = {row.number: pick(row, off, priority) for row in rows}
    report: dict[str, dict] = {}
    for adapter in players:
        present = [row for row in rows if adapter.name in row.refs]
        if adapter.name in off:
            report[adapter.name] = {"present": len(present), "available": 0, "selected": 0, "unverified": 0,
                                    "max_resolution": None, "reason": reason("source_disabled")}
            continue
        available = [row for row in rows if any(source == adapter.name for _resolution, source, _url in row.candidates(off))]
        available_numbers = {row.number for row in available}
        selected = [row for row in rows if chosen[row.number] and chosen[row.number][1] == adapter.name]
        reasons = Counter(row.player_reasons.get(adapter.name) for row in present if row.number not in available_numbers)
        reasons.pop(None, None)
        report[adapter.name] = {
            "present": len(present),
            "available": len(available),
            "selected": len(selected),
            "unverified": sum(1 for row in present if row.unverified(off)),
            "max_resolution": max((chosen[row.number][0] for row in selected if chosen[row.number][0]), default=None),
            "reason": reasons.most_common(1)[0][0] if reasons else "",
        }
    holes = [{"episode": row.number, "reason": row.reason, "unverified": row.unverified(off)}
             for row in rows if not row.candidates(off)]
    return {
        "episodes": len(rows),
        "sources": dict(Counter(chosen[row.number][1] for row in rows if chosen[row.number])),
        "players": report,
        "holes": holes,
    }


def coverage(rows: Sequence[Row], players: Sequence[Player]) -> dict[str, dict]:
    """Per player: episodes that show it, episodes it plays, and their resolution grouped by ranges."""
    result: dict[str, dict] = {}
    for adapter in players:
        by_resolution: dict[int, set[int]] = {}
        for row in rows:
            for resolution, source, _url in row.candidates():
                if source == adapter.name:
                    by_resolution.setdefault(resolution, set()).add(row.number)
        numbers = sorted(set().union(*by_resolution.values())) if by_resolution else []
        result[adapter.name] = {
            "present": sum(1 for row in rows if adapter.name in row.refs),
            "available": len(numbers), "count": len(numbers), "ranges": episode_ranges(numbers),
            "resolutions": [{"resolution": height, "ranges": episode_ranges(by_resolution[height]),
                             "count": len(by_resolution[height])}
                            for height in sorted(by_resolution, reverse=True)],
        }
    return result


def played(rows: Sequence[Row], player: str) -> set[int]:
    return {row.number for row in rows if any(source == player for _resolution, source, _url in row.candidates())}


def causes(rows: Sequence[Row], player: str) -> Counter:
    """Why a player that shows on an episode does not play there."""
    done = played(rows, player)
    found = Counter(row.player_reasons.get(player) for row in rows if player in row.refs and row.number not in done)
    found.pop(None, None)
    return found
