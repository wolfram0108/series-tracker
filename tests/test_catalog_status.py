"""Тесты статусной модели (Р-11): агрегатор и модуль catalog.

Семантика — от замысла (разбор этапа 4), не дифф со старым кодом:
старый StatusManager хранил статус на трёх этажах и пересчитывал его
поллингом; новый — вычисляет из свёрток и публикует только изменения.
"""
import asyncio
import sqlite3
import subprocess
import sys

import pytest

from core import BaseModule, Bus, Runner
from core.db import Database
from modules.catalog import CatalogModule
from modules.catalog.aggregator import StatusAggregator


# --- агрегатор: чистая логика ----------------------------------------------------

def test_empty_is_waiting():
    agg = StatusAggregator()
    assert agg.statuses(1) == ["waiting"]


def test_union_and_hierarchy_order():
    agg = StatusAggregator()
    agg.set_contribution(1, "downloads", {"downloading": True})
    agg.set_contribution(1, "scan", {"scanning": True, "error": True})
    # порядок — старый STATUS_HIERARCHY: error первым (порядок пилюль)
    assert agg.statuses(1) == ["error", "scanning", "downloading"]


def test_vk_ready_and_waiting_coexist():
    # семантика VK: скачана часть, остальное ждёт (фронт: stripes-stopped)
    agg = StatusAggregator()
    agg.set_contribution(1, "downloads", {"ready": True, "waiting": True})
    assert agg.statuses(1) == ["ready", "waiting"]


def test_sliced_ready_is_full_when_nothing_waits():
    # сценарий «Мой старший брат»: серия из нарезанных компиляций.
    # slicing вкладывает ready, плановой активности/ожидания нет —
    # полный «готов» без «белого» waiting.
    agg = StatusAggregator()
    agg.set_contribution(1, "slicing",
                         {"slicing": False, "error": False, "ready": True})
    assert agg.statuses(1) == ["ready"]


def test_ready_from_slicing_pairs_with_waiting_from_downloads():
    # часть нарезана (slicing.ready), исходник/остальное ещё ждёт
    # (downloads.waiting): готовность сосуществует с «белым».
    agg = StatusAggregator()
    agg.set_contribution(1, "slicing", {"ready": True})
    agg.set_contribution(1, "downloads", {"waiting": True})
    assert agg.statuses(1) == ["ready", "waiting"]


def test_waiting_suppressed_by_activity():
    # waiting — факт «есть что ждать», но при активности не показывается
    agg = StatusAggregator()
    agg.set_contribution(1, "downloads",
                         {"downloading": True, "waiting": True})
    assert agg.statuses(1) == ["downloading"]
    # активность кончилась — waiting снова виден
    agg.set_contribution(1, "downloads",
                         {"downloading": False, "waiting": True})
    assert agg.statuses(1) == ["waiting"]
    # активность из ДРУГОГО вклада тоже подавляет
    agg.set_contribution(1, "scan", {"scanning": True})
    assert agg.statuses(1) == ["scanning"]


def test_all_false_removes_contribution():
    agg = StatusAggregator()
    agg.set_contribution(1, "scan", {"scanning": True})
    changed = agg.set_contribution(1, "scan", {"scanning": False})
    assert changed == ["waiting"]
    assert agg.statuses(1) == ["waiting"]


def test_no_change_returns_none():
    agg = StatusAggregator()
    assert agg.set_contribution(1, "scan", {"scanning": True}) == ["scanning"]
    assert agg.set_contribution(1, "scan", {"scanning": True}) is None
    # пустая свёртка к ещё не публиковавшейся серии тоже не изменение
    assert agg.set_contribution(2, "scan", {"scanning": False}) is None


def test_unknown_flag_is_loud():
    agg = StatusAggregator()
    with pytest.raises(ValueError, match="недопустимые флаги"):
        agg.set_contribution(1, "scan", {"видимость": True})
    with pytest.raises(ValueError):
        # viewing в свёртках запрещён — он приходит только командами
        agg.set_contribution(1, "scan", {"viewing": True})


def test_viewing_and_clear_all():
    agg = StatusAggregator()
    agg.set_contribution(1, "downloads", {"downloading": True})
    assert agg.set_viewing(1, True) == ["downloading", "viewing"]
    assert agg.set_viewing(2, True) == ["viewing", "waiting"]
    changes = agg.clear_all_viewing()
    assert changes == {1: ["downloading"], 2: ["waiting"]}


def test_forget_series():
    agg = StatusAggregator()
    agg.set_contribution(1, "scan", {"scanning": True})
    agg.forget(1)
    assert agg.statuses(1) == ["waiting"]


# --- модуль через шину --------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{path}",
                        "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode) VALUES "
            "(1, 'http://x', 'Тайтл', 'Title', 'kinozal', '/media/t', "
            "'error, scanning', 'torrent', 0, 'search')")
        conn.commit()
    return str(path)


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
async def system(db_path):
    bus = Bus()
    catalog = CatalogModule(bus, Database(db_path))
    probe = Probe(bus)
    runner = Runner(bus, [catalog, probe])
    await runner.start()
    yield bus, probe
    await runner.stop()


async def _next_change(sub, timeout=2.0):
    env = await asyncio.wait_for(sub.queue.get(), timeout)
    return env.payload


@pytest.mark.asyncio
async def test_contribution_publishes_only_changes(system):
    bus, probe = system
    sub = bus.subscribe("series.status.changed")

    probe.publish_event("series.status.contribution", {
        "source": "downloads", "series_id": 1,
        "flags": {"downloading": True}})
    assert await _next_change(sub) == {"series_id": 1,
                                       "statuses": ["downloading"],
                                       "is_busy": False}

    # идентичная свёртка — события нет; следующее изменение приходит первым
    probe.publish_event("series.status.contribution", {
        "source": "downloads", "series_id": 1,
        "flags": {"downloading": True}})
    probe.publish_event("series.status.contribution", {
        "source": "downloads", "series_id": 1,
        "flags": {"downloading": False, "ready": True}})
    assert await _next_change(sub) == {"series_id": 1, "statuses": ["ready"],
                                       "is_busy": False}
    assert sub.queue.empty()


@pytest.mark.asyncio
async def test_viewing_commands_and_sse_guard(system):
    bus, probe = system
    sub = bus.subscribe("series.status.changed")

    probe.send_command("catalog.viewing.start", {"series_id": 1})
    assert await _next_change(sub) == {"series_id": 1,
                                       "statuses": ["viewing", "waiting"],
                                       "is_busy": False}

    # последний SSE-клиент отключился — viewing сброшен без heartbeat'ов
    probe.publish_event("gateway.sse.clients", {"count": 0})
    assert await _next_change(sub) == {"series_id": 1,
                                       "statuses": ["waiting"],
                                       "is_busy": False}


@pytest.mark.asyncio
async def test_series_list_no_state_column_and_statuses(system):
    _, probe = system
    probe.publish_event("series.status.contribution", {
        "source": "scan", "series_id": 1, "flags": {"scanning": True}})
    reply = await probe.request("catalog.status.get", {"series_id": 1},
                                timeout=5)
    # запрос после события могла обогнать очередь — дождёмся вклада
    for _ in range(50):
        if reply["statuses"] == ["scanning"]:
            break
        await asyncio.sleep(0.02)
        reply = await probe.request("catalog.status.get", {"series_id": 1},
                                    timeout=5)
    assert reply["statuses"] == ["scanning"]

    rows = await probe.request("catalog.series.list", {}, timeout=5)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Тайтл"
    assert row["statuses"] == ["scanning"]
    # протухающая колонка state не отдаётся наружу (Р-11)
    assert "state" not in row


@pytest.mark.asyncio
async def test_series_get_unknown_is_error(system):
    from core import BusRequestError
    _, probe = system
    with pytest.raises(BusRequestError, match="не найден"):
        await probe.request("catalog.series.get", {"series_id": 99}, timeout=5)
