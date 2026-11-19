"""The card of a series as the catalog answers it (`catalog.series.get`): its stored row, its sources, and the fields the
catalog adds from its memory at the moment of the answer."""

# Told by the catalog from what its contributors say now (the statuses, the work under way), not stored with the series:
# a reader that keeps what it counted from a card leaves them out of what the card was.
CARD_LIVE: tuple[str, ...] = ("statuses", "is_busy")
