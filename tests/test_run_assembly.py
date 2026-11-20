"""The assembly of the process: run.py builds every module over one bus, they come up together on a fresh database
(no qBittorrent, no network), and every topic the gateway asks has an owner among them."""
import asyncio
import importlib
import sys

import httpx
import pytest

from core import logging as core_logging
from schema_db import migrated

@pytest.fixture
def assembled(tmp_path, monkeypatch):
    """`run` imported afresh over a database with the schema of the migrations."""
    path = tmp_path / "assembly.db"
    migrated(path)
    for name, value in {"ST_DB_PATH": str(path), "ST_AUTH_REQUIRED": "false", "ST_COOKIE_SECURE": "false",
                        "ST_SECRET_KEY": "assembly", "ST_DOCS_ENABLED": "true"}.items():
        monkeypatch.setenv(name, value)
    for name in ("ST_QBIT_URL", "ST_CONFORM_URL", "ST_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    sys.modules.pop("run", None)
    yield importlib.import_module("run")
    sys.modules.pop("run", None)


def names(run) -> list[str]:
    return [module.name for module in run.modules]


def test_composition_starts_after_the_modules_it_asks_and_before_conform_and_torrents(assembled):
    order = names(assembled)
    assert len(set(order)) == len(order), "a module is assembled twice"
    # composition asks catalog, sources, rules and voices at its start and publishes composition.changed,
    # which scan and voices must already be subscribed to.
    for before in ("catalog", "sources", "rules", "voices", "scan"):
        assert order.index(before) < order.index("composition"), before
    for after in ("conform", "torrents"):
        assert order.index("composition") < order.index(after), after


async def test_the_assembled_system_comes_up_on_one_bus_answers_and_stops(assembled, monkeypatch):
    read = asyncio.Event()
    groups: list[set] = []
    monkeypatch.setattr(core_logging, "set_debug_groups", lambda found: (groups.append(found), read.set()))
    await assembled.db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("debug_enabled_scan", "true"))
    async with assembled._lifespan(assembled.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=assembled.app), base_url="http://test") as client:
            series = await client.get("/api/series")
            status = await client.get("/api/scanner/status")
            schema = await client.get("/openapi.json")
            sources = await client.get("/api/series/1/sources")
        # The signal of the whole system, not a pause, is what makes the gateway read the debug groups.
        await asyncio.wait_for(read.wait(), 5)
    assert series.status_code == 200 and series.json() == []
    assert status.status_code == 200
    assert sources.status_code == 404  # the catalog answered: there is no such series
    paths = schema.json()["paths"]
    assert {"/api/series/{series_id}/sources", "/api/series/{series_id}/output", "/api/sources/preview",
            "/api/sources/resolve", "/api/series/{series_id}/output/settings"} <= set(paths)
    assert not {"/api/web_video/layout", "/api/web_video/coverage", "/api/parser-profiles/scrape-titles",
                "/api/series/{series_id}/vk-quality-priority"} & set(paths)
    assert groups == [{"scan"}]
