"""The first read of a card of an application waits for the user, and a read takes the first or the last rows of each line
(core/row_limit.py): the rows of the earlier reads that it did not take stay in the table as they were."""
import asyncio

import pytest

from core.row_limit import limit_of, limited, normalized
from modules.sources.sites import ReadContext, TableDraft
from test_sources_table import PAGE, FakeSite, build, entry, row, assert_no_second_read


@pytest.mark.parametrize("value,expected", [
    ("all", "all"), ("ALL", "all"), ("", "all"), (None, "all"), (" 10 ", "10"), ("-10", "-10"), ("0", None), ("-0", None),
    ("1.5", None), ("десять", None), ("--3", None), ("+3", None),
])
def test_the_setting_of_the_rows_of_a_line_is_all_n_or_minus_n(value, expected):
    assert normalized(value) == expected


def test_a_limit_takes_the_first_or_the_last_rows_of_a_line_in_the_order_they_came():
    line = list(range(1, 31))
    assert limited(line, limit_of("10")) == list(range(1, 11))
    assert limited(line, limit_of("-10")) == list(range(21, 31))
    assert limited(line, limit_of("all")) == line and limited(line, limit_of("")) == line
    assert limited([1, 2], limit_of("10")) == [1, 2] and limited([1, 2], limit_of("-10")) == [1, 2]


class Card(FakeSite):
    """A card of an application: its page waits for the user's first read, and the rows it gives are those of its limit."""
    first_read_by_user = True

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[str] = []
        self.line = [1, 2, 3, 4, 5, 6]

    async def read(self, url: str, ctx: ReadContext) -> TableDraft:
        self.reads.append(url)
        self.asked.append(ctx.rows)
        self.entered.put_nowait(url)
        await self.go.wait()
        taken = limited(self.line, limit_of(ctx.rows))
        return TableDraft(names=["Карточка"], rows=[row(f"k{n}", f"Серия {n}", heights=[100 + len(self.asked)]) for n in taken])


@pytest.fixture
async def card(db_path, tmp_path):
    site = Card()
    built = await build(db_path, tmp_path / "cache", site)
    yield built, site
    await built.stop()


async def test_a_card_nobody_has_read_is_not_read_by_a_request_and_is_read_by_the_command_of_the_user(card):
    system, site = card
    reply = await system.get()
    assert reply["state"] == "absent" and reply["table"] is None
    await assert_no_second_read(site)
    assert site.reads == []

    system.probe.send_command("sources.table.read", {"url": PAGE, "rows": "2"})
    await entry(site)
    await system.next(system.ready)
    assert site.asked == ["2"] and await system.stored_texts() == ["Серия 1", "Серия 2"]


async def test_a_card_that_was_read_is_read_again_by_its_term_with_the_limit_of_the_request(card):
    system, site = card
    system.probe.send_command("sources.table.read", {"url": PAGE, "rows": "all"})
    await entry(site)
    await system.next(system.ready)
    system.clock.advance(hours=2)

    reply = await system.get(rows="-2")
    assert reply["state"] == "reading"
    await entry(site)
    await system.next(system.ready)
    assert site.asked == ["all", "-2"]


async def test_a_read_of_the_first_or_the_last_rows_keeps_the_rows_of_the_earlier_reads_and_a_read_of_all_replaces_them(card):
    system, site = card

    async def read(rows: str) -> list[str]:
        system.probe.send_command("sources.table.read", {"url": PAGE, "rows": rows})
        await entry(site)
        await system.next(system.ready)
        return await system.stored_texts()

    assert await read("2") == ["Серия 1", "Серия 2"]
    assert await read("-2") == ["Серия 1", "Серия 2", "Серия 5", "Серия 6"]
    # What a read took is its own: the row of the second read is the new one, the others are as they were left.
    heights = {record["text"]: record["heights"] for record in await system.db.fetch_all(
        "SELECT text, heights FROM release_rows WHERE url = ?", (PAGE,))}
    assert heights["Серия 1"] == "[101]" and heights["Серия 6"] == "[102]"
    site.line = [1, 2, 3]
    assert await read("all") == ["Серия 1", "Серия 2", "Серия 3"]


async def test_a_stop_in_the_middle_of_the_read_of_a_card_is_no_read_to_begin_again_at_the_start(db_path, tmp_path):
    site = Card()
    system = await build(db_path, tmp_path / "cache", site)
    try:
        site.go.clear()
        system.probe.send_command("sources.table.read", {"url": PAGE, "rows": "2"})
        await entry(site)
    finally:
        await system.stop()
    again = await build(db_path, tmp_path / "cache", site)
    try:
        assert (await again.stored())["state"] == "failed"
        assert (await again.get())["state"] == "failed"
        await assert_no_second_read(site)
    finally:
        await again.stop()
