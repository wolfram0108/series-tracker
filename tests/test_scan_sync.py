"""The wait of a scan for the pages of its series (modules/scan/sync.py): while a page is read, its progress is told as
the progress of every series and source that waits for it (Р-36), and the end of the wait, whatever its outcome, too."""
import asyncio

import pytest

from core.contracts import SeriesReadProgress, SeriesSource, TableProgress, TableState
from modules.scan.sync import TableSignals, sync_tables

PAGE = "https://site.test/release/1"


def source(id=1, series_id=7, url=PAGE) -> SeriesSource:
    return SeriesSource(id=id, series_id=series_id, url=url, source_type="web_video", parser_profile_id=1)


def asking(state: TableState):
    async def ask(topic, payload):
        assert topic == "sources.table.get"
        return {"url": PAGE, "state": state.value}
    return ask


def packet(current, total=20, rate=1.5) -> TableProgress:
    return TableProgress(url=PAGE, phase="episodes", current=current, total=total, rate=rate)


@pytest.mark.parametrize("outcome", ["ready", "failed", "given_up"])
async def test_the_progress_of_a_page_a_scan_waits_for_goes_out_for_its_series_and_its_end_too(outcome):
    told: list[SeriesReadProgress] = []
    signals = TableSignals(announce=told.append)
    waiting = asyncio.create_task(sync_tables([source()], asking(TableState.READING), signals,
                                              safeguard_s=0.2 if outcome == "given_up" else 5))
    while not signals._readers:  # the wait is registered before the verdict is awaited
        await asyncio.sleep(0)
    signals.progress(packet(3))
    signals.progress(packet(4))
    if outcome == "ready":
        signals.ready(PAGE)
    elif outcome == "failed":
        signals.failed(PAGE, "why")
    [synced] = await waiting
    assert synced.state == ("ready" if outcome == "ready" else "skipped")
    assert [(each.series_id, each.source_id, each.current, each.total, each.rate, each.active) for each in told] == [
        (7, 1, 3, 20, 1.5, True), (7, 1, 4, 20, 1.5, True), (7, 1, 4, 20, 1.5, False)]
    signals.progress(packet(5))  # nobody waits any more: nothing is told
    assert len(told) == 3


async def test_every_series_that_waits_for_the_page_hears_its_progress_and_another_page_tells_nobody():
    told: list[SeriesReadProgress] = []
    signals = TableSignals(announce=told.append)
    waiting = asyncio.create_task(sync_tables([source(1, 7), source(2, 8)], asking(TableState.READING), signals))
    while len(signals._readers.get(PAGE, {})) < 2:
        await asyncio.sleep(0)
    signals.progress(TableProgress(url="https://other.test/p", phase="episodes", current=1, total=2))
    signals.progress(packet(9))
    signals.ready(PAGE)
    await waiting
    assert sorted((each.series_id, each.source_id, each.current, each.active) for each in told) == [
        (7, 1, 9, False), (7, 1, 9, True), (8, 2, 9, False), (8, 2, 9, True)]


async def test_a_page_that_is_ready_at_once_has_no_progress_to_tell():
    told: list[SeriesReadProgress] = []
    signals = TableSignals(announce=told.append)
    [synced] = await sync_tables([source()], asking(TableState.READY), signals)
    assert synced.state == "ready" and told == []


async def test_a_page_nobody_has_read_waits_for_the_user_and_the_sync_goes_on_without_it_and_asks_with_the_rows_of_the_source():
    asked = []

    async def ask(topic, payload):
        asked.append(payload)
        return {"url": PAGE, "state": TableState.ABSENT.value}

    card = SeriesSource(id=1, series_id=7, url=PAGE, source_type="web_video", parser_profile_id=1, rows_per_line="-10")
    [synced] = await sync_tables([card], ask, TableSignals(announce=lambda _each: None), safeguard_s=1)
    assert synced.state == "skipped" and '"code":"table_not_read"' in synced.reason
    assert asked == [{"url": PAGE, "refresh": True, "rows": "-10"}]
