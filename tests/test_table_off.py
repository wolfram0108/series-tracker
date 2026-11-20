"""A player the owner switched off for every source is not asked (ReadContext.off): the sources module holds the setting
as the event of the settings says it, a read gives it to the site, and the core of the reads keeps the rows of the
switched-off player as its last read left them, adding none."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core import Bus
from core.contracts import RowState, TableRow
from core.contracts.voices import PLAYERS_OFF_KEY
from core.db import Database
from core.contracts.provider import ManifestFamily
from modules.sources.module import SourcesModule
from modules.sources.provider import RemoteFamily
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
    sources = SourcesModule(Bus(), Database(str(migrated(tmp_path / "sources.db"))), torrent_cache_dir=str(tmp_path / "cache"))
    # The player of the rows of the card, as a provider of the socket names it in its manifest.
    sources.players.register(RemoteFamily(ManifestFamily(
        name="line", label="Линии", video="video", owns=r"^app://banri/[0-9]+#line=(?P<line>[^&]+)&item=(?P<video>[^&]+)$"), None))
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
