"""The season invariant, a constraint of the data and its single source of truth.

The season the rules extracted from a title or a file name is a fact about the file itself and beats the season of the series
card (`series.season`). The card gives the season as a DEFAULT: it answers "which season, if the name does not show it" (the
usual case of VK, where the title carries none).

The reverse order silently lost what was recognised: specials (season 0) and files of another season collapsed into the
season of the card, and a release looked like one season."""
from __future__ import annotations

import re


def resolve(series_season: str | int | None,
           extracted_season: int | None) -> int | None:
    """The extracted season wins; the season of the card is the default for None."""
    if extracted_season is not None:
        return int(extracted_season)
    if series_season:
        m = re.search(r"\d+", str(series_season))
        if m:
            return int(m.group())
    return extracted_season
