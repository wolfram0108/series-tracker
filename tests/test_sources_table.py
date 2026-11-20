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

from core import BaseModule, Bus
from core.contracts import RowState, TableRow, UnreadPart
from core.contracts.release_table import site_of
from core.db import Database
from core.reasons import parse, reason
from modules.settings import SettingsModule
from modules.sources.module import SourcesModule
from modules.sources.repository import SourcesRepository
from modules.sources.sites import ReadContext, TableDraft
from schema_db import migrated

PAGE = "https://fake.test/p/1"
OTHER_PAGE = "https://fake.test/p/2"
# A test that waits longer than this for an event is stuck: the event is a signal, the number only ends a broken test.
STUCK_S = 5.0
# How long the absence of a second read is watched while the first is held open: it ends the watch, nothing waits for it.
ABSENCE_WINDOW_S = 0.3


@pytest.fixture
def db_path(tmp_path):
    return migrated(tmp_path / "tables.db")


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
        self.last: list[list[str]] = []  # the texts of the rows of the last complete table each read was given
        self.entered: asyncio.Queue[str] = asyncio.Queue()
        self.go = asyncio.Event()
        self.go.set()
        self.failure: Exception | None = None
        self.rows = episodes
        self.unread = lambda _read_number: []
        self.steps: list[tuple[str, int, int]] = []

    def match_url(self, url: str) -> bool:
        return urlparse(url).path.startswith("/p/")

    def canonical_url(self, url: str) -> str:
        parts = urlparse(url)
        return f"https://{parts.hostname}{parts.path}"

    async def read(self, url: str, ctx: ReadContext) -> TableDraft:
        self.reads.append(url)
        self.fresh.append(ctx.fresh)
        self.last.append([each.text for each in ctx.last_rows])
        self.entered.put_nowait(url)
        for step in self.steps:
            ctx.progress(*step)
        await self.go.wait()
        if self.failure is not None:
            raise self.failure
        return TableDraft(names=["Фейк"], site_voice="Ruchime", channel="fake-channel", rows=self.rows(len(self.reads)),
                          unread=self.unread(len(self.reads)))


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

async def test_the_term_of_a_table_is_the_term_of_its_site_kept_over_a_start_and_a_value_out_of_it_is_refused(
        db_path, tmp_path):
    """The term is the setting `sources.terms` (minutes by the site, an hour for a site it does not name): a table within it
    serves as it is, past it the page is read again; the stored setting is the term after a start. The reply and the event
    of a table say until when it is current, and a change of the term of its site tells it again."""
    bus = Bus()
    settings = SettingsModule(bus, Database(str(db_path)))
    await settings.start()
    system = await build(db_path, tmp_path / "cache", bus=bus)
    site = site_of(PAGE)
    try:
        for wrong in ("0", "1.5", "час", None, 50000):
            refused = await system.probe.request("sources.terms.set", {"site": site, "minutes": wrong})
            assert (refused["ok"], parse(refused["reason"])["code"]) == (False, "sources_term_invalid")
        assert await system.probe.request("sources.terms.set", {"site": site, "minutes": 180}) == {"ok": True, "minutes": 180}
        first = await system.get()
        ready = await system.next(system.ready)
        read_at = datetime.fromisoformat(ready["read_at"])
        assert datetime.fromisoformat(ready["fresh_until"]) == read_at + timedelta(minutes=180)
        system.clock.advance(hours=2, minutes=59)
        served = await system.get()
        assert served["state"] == "ready" and len(system.site.reads) == 1 and first["state"] == "reading"
        assert datetime.fromisoformat(served["fresh_until"]) == read_at + timedelta(minutes=180)
        fresh = bus.subscribe("sources.table.fresh")
        assert (await system.probe.request("sources.terms.set", {"site": site, "minutes": 240}))["ok"]
        told = await system.next(fresh)
        assert (told["url"], datetime.fromisoformat(told["fresh_until"])) == (PAGE, read_at + timedelta(minutes=240))
        assert (await system.probe.request("sources.tables.fresh", {"urls": [PAGE, OTHER_PAGE]}))["pages"] == [told]
        system.clock.advance(hours=1, minutes=1)
        assert (await system.get())["state"] == "reading"
        await system.next(system.ready)
        await system.stop()
        system = await build(db_path, tmp_path / "cache", system.site, clock=system.clock, bus=bus)
        assert system.sources.term.minutes == {site: 240}
    finally:
        await system.stop()
        await settings.stop()


# --- one read per page at a time (M-5, I-12, I-20) --------------------------------------------------------------------

# --- a failed read keeps the previous table (M-4, S-10, I-13) ---------------------------------------------------------

async def test_an_error_with_no_words_is_a_failed_read_named_by_its_type(system):
    """A request of the read that ran out of its fuse raises a TimeoutError with no text: the read still ends, failed,
    and the page is not left `reading`."""
    system.site.failure = TimeoutError()

    await system.get()
    failure = await system.next(system.failed)

    assert failure["url"] == PAGE and "TimeoutError" in failure["reason"]
    assert (await system.stored())["state"] == "failed"


# --- a part of a page that cannot be read ------------------------------------------------------------------------------

GAP = UnreadPart(part="kodik:2", player_label="Kodik", names=["Комната Диди"],
                 reason=reason("player_status", player="Kodik", status=500))


def parted(read_number: int, *parts: str) -> list[TableRow]:
    """A row the page lists itself, then the rows of the catalogs (parts) the read got."""
    every = {"k1": row("k1:1", f"Kodik 1 (чтение {read_number})", player="kodik", part="kodik:1"),
             "k2": row("k2:1", f"Kodik 2 (чтение {read_number})", player="kodik", part="kodik:2")}
    return [row("own", f"Своя (чтение {read_number})"), *(every[part] for part in parts)]


async def read_again(system: System) -> dict:
    system.clock.advance(hours=2)
    await system.get()
    await system.next(system.ready)
    return (await system.get())["table"]


async def test_a_part_unread_over_a_table_stored_before_rows_named_their_parts_fails_the_read(system):
    await system.get()
    await system.next(system.ready)
    await system.db.execute("UPDATE release_tables SET unread = NULL WHERE url = ?", (PAGE,))
    system.site.rows = lambda number: parted(number, "k1")
    system.site.unread = lambda number: [GAP]
    system.clock.advance(hours=2)

    await system.get()

    assert (await system.next(system.failed))["reason"] == GAP.reason
    assert await system.stored_texts() == ["Серия 1 (чтение 1)", "Серия 2 (чтение 1)"]
    # The read that gets the page whole marks it: a part that fails after it keeps its rows.
    system.site.rows = lambda number: parted(number, "k1", "k2")
    system.site.unread = lambda number: []
    assert [each["text"] for each in (await read_again(system))["rows"]][-1] == "Kodik 2 (чтение 3)"
    system.site.rows = lambda number: parted(number, "k1")
    system.site.unread = lambda number: [GAP]
    assert [each["text"] for each in (await read_again(system))["rows"]][-1] == "Kodik 2 (чтение 3)"


# --- only a complete table is published (I-13) ------------------------------------------------------------------------

# --- a stop in the middle of a read (S-8, I-12, I-21) -----------------------------------------------------------------

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
        # Two failures, each said once: the mirrors of the trackers and the reads to take up again.
        assert [record.levelno for record in caplog.records if record.name == "sources"] == [logging.ERROR] * 2
        assert (await system.get("https://nowhere.test/p/1"))["state"] == "failed"
    finally:
        await system.stop()


# --- what the module says about an address no site reads --------------------------------------------------------------

# --- the parameters of a request: a table as it is, the site of an address ------------------------------

# --- a site is added by registering it (M-34, T-42) -------------------------------------------------------------------

# --- the tables belong to the sources of the series (Р-101) -----------------------------------------------------------

class Catalog(BaseModule):
    """The catalog as `sources` asks it: the series with the addresses of their sources."""

    name = "catalog"

    def __init__(self, bus) -> None:
        self.series: dict[int, list[str]] = {}
        self.failing = False
        self.asked = 0
        super().__init__(bus)

    def register(self) -> None:
        self.handle("catalog.series.list", self.on_list)

    async def on_list(self, env) -> list[dict]:
        self.asked += 1
        if self.failing:
            raise RuntimeError("the catalog is down")
        return [{"id": series_id, "sources": [{"url": url} for url in urls]} for series_id, urls in self.series.items()]


@dataclass
class Pages:
    system: System
    catalog: Catalog
    purged: object
    forgotten: object

    async def counted(self) -> dict:
        """The sources are counted once more and the count is over: the cascade of a series that never was reports it."""
        self.system.probe.publish_event("series.deleted", {"series_id": 99})
        return await self.system.next(self.purged)

    def source_removed(self, series_id: int = 1) -> None:
        self.system.probe.publish_event("series.source.removed", {"series_id": series_id, "source_id": 1})


@pytest.fixture
async def pages(db_path, tmp_path):
    built = await build(db_path, tmp_path / "cache")
    catalog = Catalog(built.bus)
    await catalog.start()
    yield Pages(built, catalog, built.bus.subscribe("series.purged"), built.bus.subscribe("sources.pages.forgotten"))
    await catalog.stop()
    await built.stop()


async def test_the_read_of_a_page_that_goes_is_stopped_and_its_end_does_not_write_the_page_again(pages):
    pages.catalog.series = {1: [PAGE]}
    await pages.system.warm()
    await pages.counted()
    pages.system.site.go.clear()
    pages.system.probe.send_command("sources.table.read", {"url": PAGE})
    assert await entry(pages.system.site) == PAGE

    pages.catalog.series = {}
    pages.source_removed()

    assert (await pages.system.next(pages.forgotten)) == {"urls": [PAGE]}
    assert await pages.system.stored() is None
    assert await pages.system.next(pages.system.failed) == {"url": PAGE, "reason": reason("read_cancelled")}
    assert pages.system.sources.tables._reading == {}
