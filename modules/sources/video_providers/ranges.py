"""Episode coverage as text, shared by the providers that describe their sources."""
from __future__ import annotations


def episode_ranges(numbers) -> str:
    """'1-471, 473-501': the real coverage of a source, holes included."""
    ordered = sorted(set(numbers))
    spans: list[list[int]] = []
    for number in ordered:
        if spans and number == spans[-1][1] + 1:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in spans)
