"""A player the owner switched off for every source is not asked (ReadContext.off): the sources module holds the setting
as the event of the settings says it, a read gives it to the site, and the core of the reads keeps the rows of the
switched-off player as its last read left them, adding none."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core import Bus
from core.contracts import ReleaseTable, RowState, TableRow
from core.contracts.voices import PLAYERS_OFF_KEY
from core.db import Database
from app99_model import profile as app_profile
from modules.sources.module import SourcesModule
from modules.sources.sites import TableDraft
from schema_db import migrated

CARD = "app://banri/7"


def link(line: str, item: str) -> str:
    return f"{CARD}#line={line}&item={item}"


def row(line: str, place: int, height: int, token: str) -> TableRow:
    return TableRow(key=f"banri:{line}:{place}", player=f"line:{line}", text=str(place), heights=[height],
                    state=RowState.PLAYABLE, link=link(line, token))


class Banri:
    """The site of the card: it asks only the players it is not told are off, as a site does, and records what it was told."""
    name, domains, source_type = "banri", ("banri",), "web_video"

    def __init__(self) -> None:
        self.off: frozenset[str] | None = None

    def match_url(self, url: str) -> bool:
        return url.startswith("app://banri/")

    def canonical_url(self, url: str) -> str:
        return url

    async def read(self, url: str, ctx) -> TableDraft:
        self.off = ctx.off
        rows = [row("ffm3u8", 1, 1080, "a")] + ([] if "line:NBY" in ctx.off else [row("NBY", 2, 2160, "new")])
        return TableDraft(names=["万界独尊"], rows=rows)


@pytest.fixture
async def module(tmp_path):
    sources = SourcesModule(Bus(), Database(str(migrated(tmp_path / "sources.db"))), torrent_cache_dir=str(tmp_path / "cache"),
                            apps=[app_profile(label="半日 (Banri)")])
    await sources.start()
    yield sources
    await sources.stop()


async def event(sources: SourcesModule, value) -> None:
    await sources.on_settings_changed(SimpleNamespace(payload={"key": PLAYERS_OFF_KEY, "value": value}))


async def test_the_module_holds_the_players_the_setting_names_and_a_value_that_is_no_list_of_names_switches_none_off(module):
    assert module.players_off == frozenset()
    await event(module, json.dumps(["line:CO4K", "kodik"]))
    assert module.players_off == frozenset({"line:CO4K", "kodik"})
    for wrong in ("not json", json.dumps({"kodik": 1}), json.dumps([1, 2]), "", None):
        await event(module, wrong)
        assert module.players_off == frozenset(), wrong
    await event(module, json.dumps(["kodik"]))
    await module.on_settings_changed(SimpleNamespace(payload={"key": "voices.groups", "value": "[]"}))
    assert module.players_off == frozenset({"kodik"})


async def test_a_read_gives_the_site_the_players_off_and_keeps_their_rows_of_the_last_read_adding_none(module):
    old = ReleaseTable(url=CARD, site="banri", names=["万界独尊"], read_at="2026-11-12T10:00:00Z", complete=True,
                       rows=[row("ffm3u8", 1, 720, "a"), row("NBY", 1, 2160, "kept"), row("NBY", 2, 1080, "gone")])
    await module.repo.save_table(old)
    site = Banri()
    await event(module, json.dumps(["line:NBY"]))

    table = await module.tables._read(site, CARD, fresh=True)

    assert site.off == frozenset({"line:NBY"})
    # What was asked is the new read; the switched-off player's rows are the last read's, as they were.
    assert {each.key: each.heights for each in table.rows} == {
        "banri:ffm3u8:1": [1080], "banri:NBY:1": [2160], "banri:NBY:2": [1080]}

    await event(module, json.dumps([]))
    switched_on = await module.tables._read(site, CARD, fresh=True)
    assert site.off == frozenset()
    # Switched on, the player is asked again and its rows are the new read's.
    assert {each.key: each.heights for each in switched_on.rows} == {"banri:ffm3u8:1": [1080], "banri:NBY:2": [2160]}


class Untouchable:
    """A player nothing may be asked of: any call is the defect."""
    series_scope = True

    def __init__(self, name: str) -> None:
        self.name, self.label = name, name

    def __getattr__(self, attribute: str):
        raise AssertionError(f"the player {self.name} was asked: {attribute}")


async def test_the_shared_reading_of_the_players_of_a_page_asks_none_of_the_players_off():
    from modules.sources.players.catalog import Entry
    from modules.sources.players.contract import PlayerRef
    from modules.sources.players.registry import PlayerRegistry
    from modules.sources.sites import ReadContext
    from modules.sources.sites.catalogs import Listed, read_catalogs, table_rows

    series, listing = Untouchable("kodik"), Untouchable("cvh")
    registry = PlayerRegistry([series, listing])
    ctx = ReadContext(None, lambda *_: None, off=frozenset({"kodik", "cvh"}))
    refs = {"kodik": PlayerRef("kodik", "https://kodikplayer.com/serial/1/h/720p")}
    # Its catalog is not read, and the page does not hold it unread either: nothing was asked, so nothing failed.
    found = await read_catalogs(registry, [("https://page", refs)], ctx)
    assert found.read == [] and found.unread == []
    entry = Entry(key="1", ref=PlayerRef("cvh", "https://cvh/1"), text="1")
    body = await table_rows(registry, [("https://page", refs)], ctx, [Listed(listing, "https://page", [entry])])
    assert body.rows == [] and body.unread == []
