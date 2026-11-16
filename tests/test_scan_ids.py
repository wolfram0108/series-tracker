"""The formula of the id of a torrent (Р-10): the id of a release already written must come out again, so the formula is
held by unchanging vectors (checked 05.11.2026 against the 44 torrents of the working database)."""
import pytest

from core import ids


@pytest.mark.parametrize("made, expected", [
    (lambda: ids.torrent_id("https://kinozal.tv/details.php?id=2034617", "05.05.2026 10:15:00"), "f9d0f14f9e9a13e0"),
    (lambda: ids.torrent_id("https://rutracker.org/forum/viewtopic.php?t=6612345", None), "a1d47e801dbc43ab"),
])
def test_the_formula_gives_the_id_it_gave_to_the_releases_already_written(made, expected):
    assert made() == expected
