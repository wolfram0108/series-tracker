"""The module `sources` on the bus: the queries about the sites it reads, the stream of a candidate, and the class guards of its code (phases worded, no blocking disk call in async code)."""
import ast
import asyncio
import re
from pathlib import Path

import pytest

from core import BaseModule, Bus
from core.contracts import ReleaseTable, RowState, TableRow
from core.db import Database
from provider_mock import MockProvider, serving
from modules.sources.module import SourcesModule
from modules.sources.players.vibix import link_of
from modules.sources.provider import ProviderClient
from modules.sources.sites import PHASES
from modules.sources.sites.builtin import builtin_sites
from schema_db import migrated
from sources_support import OWNED, ROOT, STEP_LIMIT_S, Web, host_that

DOLLS = "https://dunhuatv.ru/28-praktikuja-ci-sto-tysjach-let-smotret-onlajn.html"


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
async def stand(tmp_path):
    path = migrated(tmp_path / "sources.db")
    bus = Bus()
    sources, probe = SourcesModule(bus, Database(str(path)), torrent_cache_dir=str(tmp_path / "cache")), Probe(bus)
    await sources.start()
    await probe.start()
    yield sources, probe
    await probe.stop()
    await sources.stop()


# --- the sites a module reads when it is given no others ---------------------------------------------------------------------

def test_a_module_given_no_sites_reads_the_builtin_ones_and_each_module_has_its_own(tmp_path):
    path = tmp_path / "sources.db"
    first, second = (SourcesModule(Bus(), Database(str(path))) for _ in range(2))

    assert [site.name for site in builtin_sites()] == ["ruchime", "dunhuatv", "animakima", "vk_channel", "animesss",
                                                       "pvashow", "yummyani", "maccms", "kinozal", "rutracker", "anilibria",
                                                       "anilibria_tv", "astar"]
    assert first.sites.find("https://1.ruchime.org/release/x").name == "ruchime"
    assert first.sites.find("https://vkvideo.ru/@chan|q").name == "vk_channel"
    assert first.sites.find(DOLLS).name == "dunhuatv" and first.sites.find("https://example.org/x") is None
    assert first.sites.find("https://1.ruchime.org/release/x") is not second.sites.find("https://1.ruchime.org/release/x")
    # The players are the module's: one object of each, whatever the number of the sites that read with them.
    ruchime, channel = first.sites.find("https://1.ruchime.org/release/x"), first.sites.find("https://vk.com/@chan")
    assert ruchime.players is channel.players is first.sites.find(DOLLS).players is first.players


async def test_the_settings_of_the_sources_are_the_mirrors_of_each_tracker_and_every_domain_read_now_announced_on_a_save(stand):
    sources, probe = stand
    announced = sources.bus.subscribe("sources.settings.changed")
    await probe.request("sources.tracker.set_mirrors", {"tracker_id": 3, "mirrors": ["kinozal.me", "kinozal.new"]}, timeout=5)
    view = await probe.request("sources.settings.get", {}, timeout=5)
    # The save is announced with the screen whole, the same the query gives: an open screen asks nothing back.
    assert (await asyncio.wait_for(announced.queue.get(), 5)).payload == view

    assert {each["id"]: each["mirrors"] for each in view["trackers"]}[3] == ["kinozal.me", "kinozal.new"]
    readers = view["readers"]
    trackers = [each for each in readers if each["kind"] == "tracker"]
    assert {each["name"]: each["domains"] for each in trackers}["Kinozal.me"] == ["kinozal.me", "kinozal.new"]
    assert len(trackers) == len(view["trackers"]) == len(await sources.repo.all_trackers())
    # The sites follow the registry: a site registered is listed with no change here.
    assert [(each["name"], each["domains"]) for each in readers if each["kind"] == "site"] == [
        (site.name, list(site.domains)) for site in sources.sites if site.source_type != "torrent"]
    assert ("maccms", ["jszyapi.com", "xinlangapi.com", "lziapi.com", "subocaiji.com"]) in [
        (each["name"], each["domains"]) for each in readers]


# --- where each player of the order of sources is ----------------------------------------------------------------------------

def page(url: str, site: str, *rows: tuple[str, str, list[int]]) -> ReleaseTable:
    return ReleaseTable(url=url, site=site, names=["Название"], read_at="2026-11-12T10:00:00Z", complete=True,
                        rows=[TableRow(key=key, player=player, text=key, heights=heights,
                                       state=RowState.PLAYABLE if heights else RowState.ABSENT)
                              for key, player, heights in rows])


@pytest.fixture
async def with_lines(tmp_path):
    """A module with a provider of the socket whose one application has two lines: they are the players its manifest names."""
    mock = MockProvider()
    mock.add_app("banri", "半日 (Banri)")
    mock.card("banri", 7, ["万界独尊"], [("1", "NBY", "n1", [1080]), ("2", "CO4K", "c1", [720])])
    async with serving(mock) as server:
        bus = Bus()
        sources = SourcesModule(bus, Database(str(migrated(tmp_path / "sources.db"))), torrent_cache_dir=str(tmp_path / "cache"),
                                provider=ProviderClient(server.url), provider_reconnect=(0.05, 0.2))
        probe, states = Probe(bus), bus.subscribe("sources.provider.state")
        await sources.start()
        await probe.start()
        while (await asyncio.wait_for(states.queue.get(), STEP_LIMIT_S)).payload["state"] != "up":
            pass
        yield sources, probe
        await probe.stop()
        await sources.stop()


async def test_every_player_says_where_it_is_among_the_pages_asked_about_and_the_best_height_of_its_rows(with_lines):
    sources, probe = with_lines
    ruchime = "https://ruchime.ru/watch/1"
    await sources.repo.save_table(page(DOLLS, "dunhuatv", ("k1", "kodik", [480, 720]), ("c1", "cvh", [1080]),
                                       ("c2", "cvh", [])))
    await sources.repo.save_table(page(ruchime, "ruchime", ("k1", "kodik", [720])))
    await sources.repo.save_table(page("app://banri/7", "banri", ("n1", "line:NBY", [1080]), ("n2", "line:NBY", [2160])))
    await sources.repo.save_table(page("https://dunhuatv.ru/other.html", "dunhuatv", ("k1", "kodik", [2160])))

    asked = await probe.request("sources.players.list", {"urls": [DOLLS, ruchime, "app://banri/7"]}, timeout=5)
    facts = {each["name"]: each for each in asked["players"]}
    # A page not asked about says nothing, whatever it holds.
    assert facts["kodik"]["kind"] == "site" and facts["kodik"]["height"] == 720
    assert [(each["label"], each["sources"]) for each in facts["kodik"]["places"]] == [("dunhuatv", 1), ("ruchime", 1)]
    assert facts["cvh"]["height"] == 1080 and [each["name"] for each in facts["cvh"]["places"]] == ["dunhuatv"]
    assert facts["alloha"]["places"] == [] and facts["alloha"]["height"] is None
    # A line is in every application that admits it, with none of the pages when no page asked about is its.
    assert facts["line:NBY"]["kind"] == "line" and facts["line:NBY"]["height"] == 2160
    assert facts["line:NBY"]["places"] == [{"name": "banri", "label": "半日 (Banri)", "sources": 1}]
    assert facts["line:CO4K"]["places"] == [{"name": "banri", "label": "半日 (Banri)", "sources": 0}]

    bare = {each["name"]: each for each in (await probe.request("sources.players.list", {}, timeout=5))["players"]}
    assert set(bare) == set(facts) and bare["kodik"]["places"] == [] and bare["line:NBY"]["places"][0]["sources"] == 0


# --- the stream of a candidate ------------------------------------------------------------------------------------------------

async def test_the_module_resolves_the_stream_of_a_link_a_player_owns_and_leaves_any_other_address(stand):
    sources, probe = stand
    await sources._http.aclose()
    sources._http = Web().route("/embed/", 'var p = new Playerjs({file: "[1080p]https://cdn.test/1080.mp4/"});').client()

    allvideo = await probe.request("sources.video.stream", {"url": "https://fsst.online/embed/77/"}, timeout=5)
    other = await probe.request("sources.video.stream", {"url": "https://vk.com/video-1_2"}, timeout=5)

    assert allvideo == {"handled": True, "stream_url": "https://cdn.test/1080.mp4/",
                        "headers": {"Referer": "https://fsst.online/"}, "progressive": True, "playlist": False,
                        "player_fetch": False}
    assert other == {"handled": False}


@pytest.mark.parametrize("name", ["vk", "allvideo", "vibix", "cvh", "kodik"])
async def test_a_link_a_player_owns_that_it_cannot_resolve_is_an_error_and_never_a_page_for_a_generic_downloader(stand, name):
    sources, probe = stand
    await sources._http.aclose()
    sources._http = host_that("busy").client()
    # No browser pool, so the catalog of Vibix cannot be captured (and no test opens a real browser).
    sources.players.get("vibix")._browser_getter = None

    reply = await probe.request("sources.video.stream", {"url": OWNED[name]}, timeout=STEP_LIMIT_S)

    assert reply["handled"] is True and reply["transient"] is True and reply["error"]


# --- the class guards ---------------------------------------------------------------------------------------------------------

def test_every_phase_of_a_read_has_its_wording_and_every_wording_has_a_phase():
    catalog = (ROOT / "web" / "src" / "i18n" / "ru.ts").read_text(encoding="utf-8")
    worded = set(re.findall(r'"sources\.phase\.(\w+)"', catalog))

    # `unknown` words a phase code that a newer server may send and this screen does not know yet.
    assert worded - {"unknown"} == set(PHASES)


BLOCKING_METHODS = {"write_text", "write_bytes", "read_text", "read_bytes", "mkdir", "touch", "unlink", "rename", "rmdir",
                    "chmod", "symlink_to"}
BLOCKING_FUNCTIONS = {"open", "makedirs", "remove", "rmtree", "copyfile", "move"}


def is_blocking(call: ast.Call) -> bool:
    function = call.func
    if isinstance(function, ast.Attribute):
        if function.attr in BLOCKING_METHODS:
            return True
        return function.attr in BLOCKING_FUNCTIONS and getattr(function.value, "id", "") in {"os", "shutil"}
    return getattr(function, "id", "") in BLOCKING_FUNCTIONS


def blocking_calls_in_async_code(source: str) -> list[int]:
    """Lines of the calls into the file system made straight in `async def` bodies (not in the sync helpers they hand to
    `core.storage.call`): such a call holds the event loop for as long as the disk takes."""
    found: set[int] = set()
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, ast.AsyncFunctionDef):
            continue
        stack = list(function.body)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.Lambda)):
                continue
            if isinstance(node, ast.Call) and is_blocking(node):
                found.add(node.lineno)
            stack.extend(ast.iter_child_nodes(node))
    return sorted(found)


def test_no_async_code_of_the_module_touches_the_disk_by_itself():
    offenders = {path.relative_to(ROOT).as_posix(): lines for path in (ROOT / "modules" / "sources").rglob("*.py")
                 if (lines := blocking_calls_in_async_code(path.read_text(encoding="utf-8")))}

    assert offenders == {}, f"a call into the file system in async code goes through core.storage.call: {offenders}"


def test_the_guard_sees_a_write_in_an_async_body_and_not_in_a_helper_given_to_the_storage_call():
    bad = "import os\nasync def f(path):\n    path.write_text('x')\n    os.makedirs('d')\n    with open('f') as handle:\n        pass\n"
    good = "async def f(path):\n    await storage.call(write, path)\n\ndef write(path):\n    path.write_text('x')\n"

    assert blocking_calls_in_async_code(bad) == [3, 4, 5]
    assert blocking_calls_in_async_code(good) == []


def test_every_log_of_the_module_is_in_the_group_of_the_module_and_none_in_the_group_of_a_site():
    groups = {}
    for path in (ROOT / "modules" / "sources").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in ("getLogger", "get_logger")
                    and node.args and isinstance(node.args[0], ast.Constant)):
                groups.setdefault(node.args[0].value, []).append(path.relative_to(ROOT).as_posix())

    assert set(groups) == {"sources"}, f"the group of the log is the name of the module: {groups}"
