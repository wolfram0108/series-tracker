"""The first read of a title of the provider waits for the user, and a read takes the first or the last rows of each line
(core/row_limit.py): the rows of the earlier reads that it did not take stay in the table as they were."""

import pytest

from core.row_limit import limit_of, limited
from modules.sources.sites import ReadContext, TableDraft
from test_sources_table import PAGE, FakeSite, build, entry, row, assert_no_second_read


class Card(FakeSite):
    """A title of the provider: its page waits for the user's first read, and the rows it gives are those of its limit."""
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


async def test_a_read_of_a_span_of_rows_takes_those_rows_and_keeps_the_others_where_they_were(card):
    system, site = card

    async def read(rows: str) -> list[str]:
        system.probe.send_command("sources.table.read", {"url": PAGE, "rows": rows})
        await entry(site)
        await system.next(system.ready)
        return await system.stored_texts()

    assert await read("2") == ["Серия 1", "Серия 2"]
    assert await read("5-6") == ["Серия 1", "Серия 2", "Серия 5", "Серия 6"]
    assert await read("3-4") == ["Серия 1", "Серия 2", "Серия 5", "Серия 6", "Серия 3", "Серия 4"]


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
