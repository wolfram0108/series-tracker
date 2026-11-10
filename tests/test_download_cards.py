"""The book of the download cards (modules/downloads/cards.py) on a clock of its own: the stages and their time, the way a
file comes, the check of its length, the attempts of a variant folded into one line."""
from core.contracts import DownloadNeed, DownloadVariant
from modules.downloads.cards import CardBook


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def states(card):
    return [stage.state for stage in card.stages]


def test_a_download_in_parts_skips_the_repack_and_its_stages_count_their_time_and_the_check_its_length():
    clock = Clock()
    book = CardBook(clock, lambda: "t")
    need = DownloadNeed(key="n", series_id=88, kind="dub", group="Kazoku Project", voice_number=15, cap=720)
    variant = DownloadVariant(source_id=55, source_n=1, player="kodik", player_label="Kodik", height=720)
    book.queued(need, variant)
    clock.now = 3
    book.attempt("n", 1, 3)
    clock.now = 4
    book.progress("n", {"phase": "link", "source_seconds": 1452.0})
    book.progress("n", {"method": "ranged"})
    book.progress("n", {"phase": "download", "progress": 50, "total_size_mb": 664.0, "dlspeed": 10 * 1024 ** 2, "eta": 34})
    clock.now = 70
    book.progress("n", {"phase": "check"})
    book.progress("n", {"phase": "check", "got_seconds": 1452.0})
    clock.now = 71
    book.finished("n", True, size_mb=664.0)
    [card] = book.view()
    assert card.status == "done" and card.method == "ranged"
    assert states(card) == ["done", "done", "done", "skipped", "done"]
    assert [round(stage.seconds) for stage in card.stages] == [3, 1, 66, 0, 1]
    assert (card.figures.source_seconds, card.figures.got_seconds, card.figures.size_mb) == (1452.0, 1452.0, 664.0)
    assert card.seconds == 71 and [(line.attempt, line.of, line.outcome) for line in card.log] == [(1, 3, "done")]


def test_the_attempts_of_a_variant_are_one_line_with_the_last_number_and_the_time_of_the_variant():
    clock = Clock()
    book = CardBook(clock, lambda: "t")
    need = DownloadNeed(key="n", series_id=88, kind="dub")
    book.queued(need, DownloadVariant(source_id=55, player="cvh"))
    for number in (1, 2, 3):
        book.attempt("n", number, 3)
        clock.now += 42
        book.attempt_failed("n")
    book.finished("n", False)
    [card] = book.view()
    [line] = card.log
    assert (line.attempt, line.of, line.outcome, line.stage, round(line.seconds)) == (3, 3, "failed", "link", 126)
    assert card.status == "failed" and states(card)[1] == "failed"


def test_a_need_of_the_next_sync_is_a_new_card_and_what_waits_shows_the_time_of_its_stage():
    clock = Clock()
    book = CardBook(clock, lambda: "t")
    book.queued(DownloadNeed(key="n", series_id=88, kind="dub", sync_started="a"), None)
    book.finished("n", False)
    book.queued(DownloadNeed(key="n", series_id=88, kind="dub", sync_started="b"), None)
    clock.now = 41
    [card] = book.view()
    assert card.status == "queued" and card.log == [] and round(card.stages[0].seconds) == 41 and card.seconds == 41
