"""The card of a series as the catalog answers it (`catalog.series.get`): its stored row, its sources, and the fields the
catalog adds from its memory at the moment of the answer."""

# Told by the catalog from what its contributors say now (the statuses, the work under way), not stored with the series:
# a reader that keeps what it counted from a card leaves them out of what the card was.
CARD_LIVE: tuple[str, ...] = ("statuses", "is_busy")


def card_version(card: dict) -> dict:
    """What a card is as stored: without its live fields, its sources in one order (the list of the catalog and its
    answer for one series give them in their own)."""
    stored = {key: value for key, value in card.items() if key not in CARD_LIVE}
    stored["sources"] = sorted(stored.get("sources", []), key=lambda each: each["id"])
    return stored
