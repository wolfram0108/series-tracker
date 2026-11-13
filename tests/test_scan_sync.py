"""The wait of a scan for the pages of its series (modules/scan/sync.py). How far a read is goes to the series by the layout
(modules/composition, tests/test_composition_reads.py), not by the scan that waits."""
import asyncio

import pytest

from core.contracts import SeriesSource, TableState
from modules.scan.sync import TableSignals, sync_tables

PAGE = "https://site.test/release/1"


def source(id=1, series_id=7, url=PAGE) -> SeriesSource:
    return SeriesSource(id=id, series_id=series_id, url=url, source_type="web_video", parser_profile_id=1)


def asking(state: TableState):
    async def ask(topic, payload):
        assert topic == "sources.table.get"
        return {"url": PAGE, "state": state.value}
    return ask


async def test_a_page_that_is_ready_at_once_is_not_waited_for():
    [synced] = await sync_tables([source()], asking(TableState.READY), TableSignals())
    assert synced.state == "ready"


@pytest.mark.parametrize("outcome", ["ready", "failed", "given_up"])
async def test_a_page_that_is_read_is_waited_for_to_its_verdict(outcome):
    signals = TableSignals()
    waiting = asyncio.create_task(sync_tables([source()], asking(TableState.READING), signals,
                                              safeguard_s=0.2 if outcome == "given_up" else 5))
    await asyncio.sleep(0.05)
    if outcome == "ready":
        signals.ready(PAGE)
    elif outcome == "failed":
        signals.failed(PAGE, "why")
    [synced] = await waiting
    assert synced.state == ("ready" if outcome == "ready" else "skipped")


async def test_a_page_nobody_has_read_waits_for_the_user_and_the_sync_goes_on_without_it_and_asks_with_the_rows_of_the_source():
    asked = []

    async def ask(topic, payload):
        asked.append(payload)
        return {"url": PAGE, "state": TableState.ABSENT.value}

    card = SeriesSource(id=1, series_id=7, url=PAGE, source_type="web_video", parser_profile_id=1, rows_per_line="-10")
    [synced] = await sync_tables([card], ask, TableSignals(), safeguard_s=1)
    assert synced.state == "skipped" and '"code":"table_not_read"' in synced.reason
    assert asked == [{"url": PAGE, "refresh": True, "rows": "-10"}]
