"""The tables of site pages: a page is read once per term and by one read at a time, on sites that are only registered.

The site here is a fake that the module knows through the registry alone (M-34); the module, the bus and the database are
real. Time comes from a clock the test moves, and the order of events from the signals of the fake site, never from waits.
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, inspect

from core import BaseModule, Bus
from core.contracts import ReleaseTable, RowState, RowVoice, TableGetRequest, TableRow
from core.db import Database
from core.reasons import parse, reason
from modules.sources.module import SourcesModule
from modules.sources.repository import SourcesRepository
from modules.sources.sites import ReadContext, SiteError, SiteRegistry, TableDraft, on_domain
from sources_support import migrate

PAGE = "https://fake.test/p/1"
OTHER_PAGE = "https://fake.test/p/2"
# A test that waits longer than this for an event is stuck: the event is a signal, the number only ends a broken test.
STUCK_S = 5.0
# How long the absence of a second read is watched while the first is held open: it ends the watch, nothing waits for it.
ABSENCE_WINDOW_S = 0.3


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "tables.db"
    migrate(path, "upgrade")
    return path


# --- the fake site ----------------------------------------------------------------------------------------------------

def row(key: str, text: str, **fields) -> TableRow:
    return TableRow(**{"key": key, "player": "vk", "player_label": "VK", "text": text, "heights": [720, 1080],
                       "state": RowState.PLAYABLE, "link": f"https://cdn.test/{key}", **fields})


def episodes(read_number: int) -> list[TableRow]:
    return [row("a1", f"Серия 1 (чтение {read_number})"), row("a2", f"Серия 2 (чтение {read_number})")]


class FakeSite:
    """A site that exists only in the registry. `go` holds a read open; `entered` tells that a read began."""

    def __init__(self, name: str = "fake", host: str = "fake.test") -> None:
        self.name, self.domains = name, (host,)
        self.source_type = "web_video"
        self.reads: list[str] = []
        self.fresh: list[bool] = []  # whether each read was one the user asked for
        self.entered: asyncio.Queue[str] = asyncio.Queue()
        self.go = asyncio.Event()
        self.go.set()
        self.failure: Exception | None = None
        self.rows = episodes
        self.steps: list[tuple[str, int, int]] = []

    def match_url(self, url: str) -> bool:
        return urlparse(url).path.startswith("/p/")

    def canonical_url(self, url: str) -> str:
        parts = urlparse(url)
        return f"https://{parts.hostname}{parts.path}"

    async def read(self, url: str, ctx: ReadContext) -> TableDraft:
        self.reads.append(url)
        self.fresh.append(ctx.fresh)
        self.entered.put_nowait(url)
        for step in self.steps:
            ctx.progress(*step)
        await self.go.wait()
        if self.failure is not None:
            raise self.failure
        return TableDraft(names=["Фейк"], site_voice="Ruchime", rows=self.rows(len(self.reads)))


async def entry(site: FakeSite) -> str:
    return await asyncio.wait_for(site.entered.get(), STUCK_S)


async def assert_no_second_read(site: FakeSite) -> None:
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(site.entered.get(), ABSENCE_WINDOW_S)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


class Probe(BaseModule):
    name = "probe"


@dataclass
class System:
    bus: Bus
    probe: Probe
    sources: SourcesModule
    site: FakeSite
    clock: Clock
    db: Database
    ready: object
    failed: object
    progress: object

    async def get(self, url: str = PAGE, **request) -> dict:
        return await self.probe.request("sources.table.get", {"url": url, **request}, timeout=STUCK_S)

    async def next(self, subscription) -> dict:
        return (await asyncio.wait_for(subscription.queue.get(), STUCK_S)).payload

    async def warm(self, url: str = PAGE) -> None:
        """The first complete read of a page, with its signal of entry taken, so the next entry is the next read's."""
        await self.get(url)
        await entry(self.site)
        await self.next(self.ready)

    async def stored(self, url: str = PAGE) -> dict | None:
        return await self.db.fetch_one("SELECT state, reason, complete, read_at FROM release_tables WHERE url = ?", (url,))

    async def stored_texts(self, url: str = PAGE) -> list[str]:
        records = await self.db.fetch_all("SELECT text FROM release_rows WHERE url = ? ORDER BY seq", (url,))
        return [record["text"] for record in records]

    async def stop(self) -> None:
        await self.probe.stop()
        await self.sources.stop()


async def build(path: Path, cache_dir: Path, *sites: FakeSite, clock: Clock | None = None, bus: Bus | None = None,
                **options) -> System:
    bus, clock = bus or Bus(), clock or Clock()
    site = sites[0] if sites else FakeSite()
    db = Database(str(path))
    sources = SourcesModule(bus, db, torrent_cache_dir=str(cache_dir), sites=sites or (site,), table_clock=clock,
                            **options)
    probe = Probe(bus)
    system = System(bus, probe, sources, site, clock, db, bus.subscribe("sources.table.ready"),
                    bus.subscribe("sources.table.failed"), bus.subscribe("sources.table.progress"))
    await sources.start()
    await probe.start()
    return system


@pytest.fixture
async def system(db_path, tmp_path):
    built = await build(db_path, tmp_path / "cache")
    yield built
    await built.stop()


# --- the term of a table (M-3, T-2) -----------------------------------------------------------------------------------

async def test_a_page_asked_again_within_the_hour_is_not_read_again(system):
    first = await system.get()
    assert first["state"] == "reading" and first["table"] is None
    assert (await system.next(system.ready))["url"] == PAGE

    system.clock.advance(minutes=59, seconds=59)
    again = await system.get()
    assert again["state"] == "ready"
    assert [each["text"] for each in again["table"]["rows"]] == ["Серия 1 (чтение 1)", "Серия 2 (чтение 1)"]
    assert again["table"]["complete"] is True and again["table"]["site"] == "fake"
    assert len(system.site.reads) == 1


async def test_a_page_asked_after_the_hour_is_read_again_and_the_old_table_serves_meanwhile(system):
    await system.get()
    await system.next(system.ready)
    system.clock.advance(hours=1)
    system.site.go.clear()

    stale = await system.get()
    assert stale["state"] == "reading"
    assert [each["text"] for each in stale["table"]["rows"]] == ["Серия 1 (чтение 1)", "Серия 2 (чтение 1)"]
    system.site.go.set()
    await system.next(system.ready)

    fresh = await system.get()
    assert fresh["state"] == "ready"
    assert [each["text"] for each in fresh["table"]["rows"]] == ["Серия 1 (чтение 2)", "Серия 2 (чтение 2)"]
    assert len(system.site.reads) == 2


async def test_each_page_has_its_own_term(system):
    await system.get(PAGE)
    await system.next(system.ready)
    system.clock.advance(minutes=40)
    await system.get(OTHER_PAGE)
    await system.next(system.ready)
    system.clock.advance(minutes=30)

    assert (await system.get(OTHER_PAGE))["state"] == "ready"
    assert (await system.get(PAGE))["state"] == "reading"
    await system.next(system.ready)
    assert system.site.reads == [PAGE, OTHER_PAGE, PAGE]


async def test_a_table_dated_in_the_future_is_not_trusted(system):
    await system.get()
    await system.next(system.ready)
    system.clock.advance(hours=-2)

    assert (await system.get())["state"] == "reading"
    await system.next(system.ready)
    assert len(system.site.reads) == 2


# --- one read per page at a time (M-5, I-12, I-20) --------------------------------------------------------------------

async def test_two_series_asking_for_one_page_make_one_read_and_both_get_the_signal(system):
    second_consumer = system.bus.subscribe("sources.table.ready")
    system.site.go.clear()

    first, second = await asyncio.gather(system.get(PAGE), system.get(PAGE + "?utm=1#top"))

    assert first["state"] == second["state"] == "reading"
    assert first["table"] is None and second["table"] is None
    assert await entry(system.site) == PAGE
    await assert_no_second_read(system.site)
    system.site.go.set()
    for subscription in (system.ready, second_consumer):
        assert (await system.next(subscription))["url"] == PAGE
    assert len(system.site.reads) == 1
    assert (await system.get())["state"] == "ready"


async def test_reading_now_does_not_start_a_second_read_while_one_goes(system):
    system.site.go.clear()
    await system.get()
    await entry(system.site)

    await system.sources.tables.read(TableGetRequest(url=PAGE))

    await assert_no_second_read(system.site)
    system.site.go.set()
    await system.next(system.ready)
    assert len(system.site.reads) == 1


async def test_the_command_to_read_reads_a_page_whose_table_is_still_good_and_takes_nothing_shared(system):
    await system.warm()

    system.probe.send_command("sources.table.read", {"url": PAGE})

    assert await entry(system.site) == PAGE
    await system.next(system.ready)
    assert len(system.site.reads) == 2
    # The read the term began may take what an earlier read left for sharing; the one the user asked for may not.
    assert system.site.fresh == [False, True]


# --- a failed read keeps the previous table (M-4, S-10, I-13) ---------------------------------------------------------

async def test_a_failed_read_names_the_reason_and_the_previous_complete_table_stays_in_force(system):
    await system.get()
    await system.next(system.ready)
    before = (await system.get())["table"]
    system.clock.advance(hours=2)
    system.site.failure = SiteError(reason("site_down", status=503))

    reply = await system.get()
    assert reply["state"] == "reading" and reply["table"] == before
    assert await system.next(system.failed) == {"url": PAGE, "reason": reason("site_down", status=503)}

    stored = await system.stored()
    assert stored["state"] == "failed" and stored["reason"] == reason("site_down", status=503) and stored["complete"] == 1
    assert await system.stored_texts() == ["Серия 1 (чтение 1)", "Серия 2 (чтение 1)"]
    # The next request is the next attempt, and meanwhile the consumer still has the table that was complete.
    again = await system.get()
    assert again["state"] == "reading" and again["table"] == before
    await system.next(system.failed)
    assert len(system.site.reads) == 3


async def test_a_page_never_read_completely_has_no_table_after_a_failed_read(system):
    system.site.failure = SiteError(reason("site_down"))

    assert (await system.get())["table"] is None
    await system.next(system.failed)

    assert (await system.get())["table"] is None
    await system.next(system.failed)
    assert await system.stored_texts() == []
    stored = await system.stored()
    assert stored["state"] == "failed" and stored["complete"] == 0 and stored["read_at"] is None


async def test_an_unexpected_exception_is_logged_as_an_error_and_is_a_failed_read(system, caplog):
    system.site.failure = RuntimeError("the markup changed")

    with caplog.at_level(logging.ERROR, logger="sources"):
        await system.get()
        failure = await system.next(system.failed)

    assert failure["url"] == PAGE and failure["reason"].startswith('{"code":"read_failed"')
    assert "the markup changed" in failure["reason"]
    assert [record.levelno for record in caplog.records if record.name == "sources"] == [logging.ERROR]
    assert (await system.stored())["state"] == "failed"


async def test_a_repeated_row_key_rejects_the_whole_table(system):
    await system.get()
    await system.next(system.ready)
    system.clock.advance(hours=2)
    system.site.rows = lambda number: [row("a1", "Серия 1"), row("a2", "Серия 2"), row("a1", "Серия 3")]

    await system.get()
    failure = await system.next(system.failed)

    assert failure["reason"] == reason("duplicate_key", key="a1", player="vk")
    assert await system.stored_texts() == ["Серия 1 (чтение 1)", "Серия 2 (чтение 1)"]


async def test_a_read_that_does_not_end_is_cut_by_the_safeguard_and_the_page_is_free_again(db_path, tmp_path):
    system = await build(db_path, tmp_path / "cache", table_safeguard_s=0.05)
    try:
        system.site.go.clear()
        await system.get()
        failure = await system.next(system.failed)
        assert failure["url"] == PAGE and parse(failure["reason"])["code"] == "read_timeout"

        system.site.go.set()
        assert (await system.get())["state"] == "reading"
        await system.next(system.ready)
    finally:
        await system.stop()


# --- only a complete table is published (I-13) ------------------------------------------------------------------------

async def test_the_rows_of_a_read_in_progress_are_not_served(system):
    system.site.go.clear()
    await system.get()
    await entry(system.site)

    assert (await system.get())["table"] is None
    assert await system.stored_texts() == []
    system.site.go.set()
    await system.next(system.ready)
    assert len((await system.get())["table"]["rows"]) == 2


async def test_a_table_replaces_the_rows_of_the_page_in_one_transaction(db_path):
    repo = SourcesRepository(Database(str(db_path)))
    stamp = "2026-11-01T12:00:00+00:00"
    old = ReleaseTable(url=PAGE, site="fake", names=["Фейк"], read_at=stamp, complete=True, rows=episodes(1))
    await repo.save_table(old)
    # Two rows with one key pass the model and fail at the third insert: nothing of the new table may remain.
    broken = old.model_copy(update={"rows": [row("b1", "Новая 1"), row("b2", "Новая 2"), row("b1", "Новая 3")]})

    with pytest.raises(Exception):
        await repo.save_table(broken)

    assert await repo.last_table(PAGE) == old


async def test_only_a_complete_table_is_stored(db_path):
    repo = SourcesRepository(Database(str(db_path)))

    with pytest.raises(ValueError):
        await repo.save_table(ReleaseTable(url=PAGE, site="fake", rows=episodes(1)))

    assert await repo.last_table(PAGE) is None


async def test_a_new_table_replaces_the_old_one_whole_and_a_page_may_have_no_rows(system):
    await system.get()
    await system.next(system.ready)
    system.clock.advance(hours=1)
    system.site.rows = lambda number: [row("c1", "Серия 9")]
    await system.get()
    await system.next(system.ready)
    assert await system.stored_texts() == ["Серия 9"]

    system.clock.advance(hours=1)
    system.site.rows = lambda number: []
    await system.get()
    await system.next(system.ready)

    empty = await system.get()
    assert empty["state"] == "ready" and empty["table"]["rows"] == [] and empty["table"]["complete"] is True


async def test_a_row_comes_back_as_the_site_gave_it_in_the_order_of_the_site(system):
    given = [
        row("z9", "109-110 серия", player="kodik", player_label="Kodik", group="1 сезон",
            voice=RowVoice(name="Студия Константина Исаева", kind="voice"), heights=[480, 720],
            duration_s=855.0, published="2026-10-01", site_fields={"value": 110, "compilation": [109, 110]}),
        row("a1", "Серия 2", state=RowState.NOT_OBTAINED, reason=reason("player_no_answer", player="VK"), heights=[]),
        row("m5", "Серия 3", state=RowState.ABSENT, reason=reason("player_video_missing", player="VK"), heights=[]),
    ]
    system.site.rows = lambda number: given

    await system.get()
    await system.next(system.ready)
    table = ReleaseTable.model_validate((await system.get())["table"])

    assert table.rows == given
    assert table.names == ["Фейк"] and table.site_voice == "Ruchime" and table.url == PAGE
    assert table.read_at == "2026-11-01T12:00:00+00:00"


# --- a stop in the middle of a read (S-8, I-12, I-21) -----------------------------------------------------------------

async def test_a_stop_in_the_middle_of_a_read_makes_the_next_start_read_the_page_again(db_path, tmp_path):
    first = await build(db_path, tmp_path / "cache")
    first.site.go.clear()
    await first.get()
    await entry(first.site)
    assert (await first.stored())["state"] == "reading"

    await first.stop()

    assert first.ready.queue.empty() and first.failed.queue.empty()
    assert (await first.stored())["state"] == "reading"
    second_site = FakeSite()
    second = await build(db_path, tmp_path / "cache", second_site, clock=first.clock)
    try:
        # The subscriptions of `build` come before the start, so the end of the resumed read cannot be missed.
        assert (await second.next(second.ready))["url"] == PAGE
        assert second_site.reads == [PAGE]
        assert (await second.get())["state"] == "ready"
        assert (await second.stored())["state"] == "ready"
    finally:
        await second.stop()


async def test_the_table_that_was_complete_before_the_stop_serves_while_the_read_goes_again(db_path, tmp_path):
    clock = Clock()
    first = await build(db_path, tmp_path / "cache", clock=clock)
    await first.warm()
    clock.advance(hours=2)
    first.site.go.clear()
    await first.get()
    await entry(first.site)
    await first.stop()

    second_site = FakeSite()
    second_site.go.clear()
    second = await build(db_path, tmp_path / "cache", second_site, clock=clock)
    try:
        await entry(second_site)
        resumed = await second.get()
        assert resumed["state"] == "reading"
        assert [each["text"] for each in resumed["table"]["rows"]] == ["Серия 1 (чтение 1)", "Серия 2 (чтение 1)"]
        second_site.go.set()
        await second.next(second.ready)
        assert len(second_site.reads) == 1
    finally:
        await second.stop()


async def test_a_page_marked_as_read_whose_site_is_gone_is_reported_failed_at_start(db_path, tmp_path):
    await SourcesRepository(Database(str(db_path))).mark_reading("https://gone.test/p/7", "gone", "2026-11-01T11:00:00+00:00")

    system = await build(db_path, tmp_path / "cache")
    try:
        assert await system.next(system.failed) == {"url": "https://gone.test/p/7", "reason": reason("site_not_found")}
        assert (await system.stored("https://gone.test/p/7"))["state"] == "failed"
    finally:
        await system.stop()


async def test_a_start_without_the_tables_still_serves_the_rest_of_the_module(tmp_path, caplog):
    with caplog.at_level(logging.ERROR, logger="sources"):
        system = await build(tmp_path / "empty.db", tmp_path / "cache")
    try:
        assert [record.levelno for record in caplog.records if record.name == "sources"] == [logging.ERROR]
        assert (await system.get("https://nowhere.test/p/1"))["state"] == "failed"
    finally:
        await system.stop()


# --- what the module says about an address no site reads --------------------------------------------------------------

async def test_an_address_of_no_site_is_refused_at_once_and_no_read_begins(system):
    reply = await system.get("https://nowhere.test/p/1")

    assert reply == {"url": "https://nowhere.test/p/1", "state": "failed", "table": None,
                     "reason": reason("site_not_found")}
    assert system.site.reads == [] and system.failed.queue.empty()


async def test_the_command_to_read_an_address_of_no_site_ends_with_the_failure_event(system):
    system.probe.send_command("sources.table.read", {"url": "https://nowhere.test/p/1"})

    assert await system.next(system.failed) == {"url": "https://nowhere.test/p/1", "reason": reason("site_not_found")}


async def test_progress_of_a_read_goes_out_under_the_address_of_the_page(system):
    system.site.steps = [("players", 1, 4), ("rows", 23, 23)]

    await system.get(PAGE + "?utm=1")
    await system.next(system.ready)

    first, second = await system.next(system.progress), await system.next(system.progress)
    assert {key: first[key] for key in ("url", "phase", "current", "total")} == {
        "url": PAGE, "phase": "players", "current": 1, "total": 4}
    assert {key: second[key] for key in ("url", "phase", "current", "total")} == {
        "url": PAGE, "phase": "rows", "current": 23, "total": 23}
    assert first["rate"] is None and second["rate"] is None  # each is the first step of its phase


def test_the_pace_of_a_read_is_the_steps_of_its_phase_per_second_since_the_phase_began():
    from modules.sources.table import Pace
    now = [100.0]
    pace = Pace(clock=lambda: now[0])
    assert pace.rate("episodes", 0) is None  # the phase begins: no time has passed yet
    now[0] = 104.0
    assert pace.rate("episodes", 10) == 2.5
    now[0] = 110.0
    assert pace.rate("episodes", 20) == 2.0
    assert pace.rate("players", 5) is None  # a new phase counts from its own beginning
    now[0] = 112.0
    assert pace.rate("players", 5) == 2.5
    assert pace.rate("players", 0) is None


# --- the parameters of a request: a table as it is, the site of an address ------------------------------

async def test_a_table_asked_for_as_it_is_costs_no_read_whatever_its_age(system):
    assert await system.get(refresh=False) == {"url": PAGE, "state": "absent", "table": None, "reason": None}
    assert system.site.reads == []
    await system.warm()
    system.clock.advance(days=3)

    old = await system.get(refresh=False)

    assert old["state"] == "ready" and old["table"]["read_at"] == "2026-11-01T12:00:00+00:00"
    assert len(system.site.reads) == 1
    await assert_no_second_read(system.site)


async def test_a_table_asked_for_as_it_is_while_a_read_goes_says_reading_and_has_the_last_complete_one(system):
    await system.warm()
    system.clock.advance(hours=2)
    system.site.go.clear()
    await system.get()
    await entry(system.site)

    held = await system.get(refresh=False)

    assert held["state"] == "reading" and [each["text"] for each in held["table"]["rows"]][0].endswith("(чтение 1)")
    system.site.go.set()
    await system.next(system.ready)


async def test_a_table_asked_for_as_it_is_says_failed_with_the_reason_and_keeps_the_last_complete_table(system):
    await system.warm()
    system.clock.advance(hours=2)
    system.site.failure = SiteError(reason("site_down", status=503))
    await system.get()
    await entry(system.site)
    await system.next(system.failed)

    verdict = await system.get(refresh=False)

    assert verdict["state"] == "failed" and verdict["reason"] == reason("site_down", status=503)
    assert [each["text"] for each in verdict["table"]["rows"]][0].endswith("(чтение 1)")
    assert len(system.site.reads) == 2
    await assert_no_second_read(system.site)


async def test_a_page_whose_first_read_failed_is_failed_with_no_table_and_not_absent(system):
    system.site.failure = SiteError(reason("site_down"))
    await system.get()
    await system.next(system.failed)

    verdict = await system.get(refresh=False)

    assert verdict["state"] == "failed" and verdict["table"] is None and verdict["reason"] == reason("site_down")


async def test_a_read_that_succeeds_after_a_failed_one_takes_the_verdict_away(system):
    await system.warm()
    system.clock.advance(hours=2)
    system.site.failure = SiteError(reason("site_down"))
    await system.get()
    await system.next(system.failed)
    assert (await system.get(refresh=False))["state"] == "failed"

    system.site.failure = None
    await system.get()
    await system.next(system.ready)
    after = await system.get(refresh=False)

    assert after["state"] == "ready" and after["reason"] is None
    assert [each["text"] for each in after["table"]["rows"]][0].endswith("(чтение 3)")


async def test_the_verdict_of_a_forced_read_that_failed_is_seen_as_it_is_while_the_table_is_still_fresh(system):
    await system.warm()
    system.site.failure = SiteError(reason("site_down"))
    system.probe.send_command("sources.table.read", {"url": PAGE})
    await entry(system.site)
    await system.next(system.failed)

    assert (await system.get(refresh=False))["state"] == "failed"
    # The term is the other question: a table younger than the hour is the table, whatever the last forced read said.
    assert (await system.get())["state"] == "ready"


async def test_the_site_of_an_address_and_its_canonical_form_are_told_with_no_read(system):
    found = await system.probe.request("sources.site.resolve", {"url": PAGE + "?utm=1#top"}, timeout=STUCK_S)
    nowhere = await system.probe.request("sources.site.resolve", {"url": "https://nowhere.test/p/1"}, timeout=STUCK_S)

    assert found == {"site": "fake", "canonical_url": PAGE, "source_type": "web_video"}
    assert nowhere == {"site": None, "canonical_url": None, "source_type": None}
    assert system.site.reads == []


# --- a site is added by registering it (M-34, T-42) -------------------------------------------------------------------

async def test_a_second_site_is_served_beside_the_first_with_no_change_to_the_core(db_path, tmp_path):
    other = FakeSite("other", "other.test")
    other.rows = lambda number: [row("o1", "Other 1")]
    system = await build(db_path, tmp_path / "cache", FakeSite(), other)
    try:
        await system.get("https://other.test/p/5")
        assert (await system.next(system.ready))["url"] == "https://other.test/p/5"
        reply = await system.get("https://other.test/p/5")
        assert reply["table"]["site"] == "other" and reply["table"]["rows"][0]["text"] == "Other 1"
        assert system.site.reads == [] and other.reads == ["https://other.test/p/5"]
    finally:
        await system.stop()


def test_sites_are_found_by_domain_and_then_by_their_own_reading_of_the_address():
    first, second = FakeSite("first", "first.test"), FakeSite("second", "second.test")
    registry = SiteRegistry([first, second])

    assert registry.find("https://first.test/p/1") is first
    assert registry.find("https://www.second.test/p/1") is second
    assert registry.find("https://first.test/catalog") is None
    assert registry.find("https://third.test/p/1") is None


def test_a_site_repeating_a_name_or_a_domain_is_refused():
    registry = SiteRegistry([FakeSite("first", "first.test")])

    with pytest.raises(ValueError):
        registry.register(FakeSite("first", "elsewhere.test"))
    with pytest.raises(ValueError):
        registry.register(FakeSite("second", "FIRST.test"))
    with pytest.raises(ValueError):
        registry.register(FakeSite("", "x.test"))


def test_a_site_that_asks_for_the_wiring_of_the_module_gets_it():
    class Wired(FakeSite):
        def configure(self, **options):
            self.options = options

    wired = Wired()
    SiteRegistry([wired, FakeSite("plain", "plain.test")]).configure(browser_getter="pool")

    assert wired.options == {"browser_getter": "pool"}


def test_a_domain_covers_its_subdomains_and_nothing_that_only_ends_alike():
    assert on_domain("https://1.ruchime.org/release/x", ["ruchime.org"])
    assert on_domain("https://RUCHIME.org/release/x", ["ruchime.org"])
    assert not on_domain("https://notruchime.org/release/x", ["ruchime.org"])
    assert not on_domain("not an address", ["ruchime.org"])


def test_the_sites_of_two_module_instances_do_not_meet(db_path, tmp_path):
    first = SourcesModule(Bus(), Database(str(db_path)), sites=[FakeSite()])
    second = SourcesModule(Bus(), Database(str(db_path)))

    assert first.sites.find(PAGE) is not None and second.sites.find(PAGE) is None


# --- the schema -------------------------------------------------------------------------------------------------------

def test_the_migration_creates_the_tables_of_pages_and_takes_them_away(tmp_path):
    path = tmp_path / "m.db"
    migrate(path, "upgrade")
    engine = create_engine(f"sqlite:///{path}")
    tables = inspect(engine)
    assert {column["name"] for column in tables.get_columns("release_tables")} == {
        "url", "site", "names", "site_voice", "read_at", "complete", "state", "reason", "reading_since"}
    assert {column["name"] for column in tables.get_columns("release_rows")} == {
        "url", "seq", "key", "player", "player_label", "grp", "text", "voice_name", "voice_kind", "heights", "state",
        "reason", "duration_s", "published", "link", "site_fields"}
    assert tables.get_pk_constraint("release_rows")["constrained_columns"] == ["url", "key"]
    engine.dispose()

    migrate(path, "downgrade")
    engine = create_engine(f"sqlite:///{path}")
    assert inspect(engine).get_table_names() == []
    engine.dispose()
