"""The module `sources` on the bus: the queries about the sites it reads, the stream of a candidate, the handlers the web
providers still answer through, and the class guards of its code (phases worded, no blocking disk call in async code)."""
import ast
import re
from pathlib import Path

import pytest

from core import BaseModule, Bus
from core.db import Database
from modules.sources.module import SourcesModule
from modules.sources.players.vibix import link_of
from modules.sources.sites import PHASES
from modules.sources.sites.builtin import builtin_sites
from sources_support import ROOT, Web, migrate

DOLLS = "https://dunhuatv.ru/28-praktikuja-ci-sto-tysjach-let-smotret-onlajn.html"


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
async def stand(tmp_path):
    path = tmp_path / "sources.db"
    migrate(path)
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

    assert [site.name for site in builtin_sites()] == ["ruchime", "vk_channel"]
    assert first.sites.find("https://1.ruchime.org/release/x").name == "ruchime"
    assert first.sites.find("https://vkvideo.ru/@chan|q").name == "vk_channel"
    assert first.sites.find(DOLLS) is None
    assert first.sites.find("https://1.ruchime.org/release/x") is not second.sites.find("https://1.ruchime.org/release/x")
    # The players are the module's: one object of each, whatever the number of the sites that read with them.
    ruchime, channel = first.sites.find("https://1.ruchime.org/release/x"), first.sites.find("https://vk.com/@chan")
    assert ruchime.players is channel.players is first.players


# --- the stream of a candidate ------------------------------------------------------------------------------------------------

async def test_the_module_resolves_the_stream_of_a_link_a_player_owns_and_leaves_any_other_address(stand):
    sources, probe = stand
    await sources._http.aclose()
    sources._http = Web().route("/embed/", 'var p = new Playerjs({file: "[1080p]https://cdn.test/1080.mp4/"});').client()

    allvideo = await probe.request("sources.video.stream", {"url": "https://fsst.online/embed/77/"}, timeout=5)
    other = await probe.request("sources.video.stream", {"url": "https://vk.com/video-1_2"}, timeout=5)

    assert allvideo == {"handled": True, "stream_url": "https://cdn.test/1080.mp4/",
                        "headers": {"Referer": "https://fsst.online/"}, "progressive": False}
    assert other == {"handled": False}


async def test_a_vibix_link_the_module_cannot_resolve_is_an_error_and_never_a_page_for_a_generic_downloader(stand):
    sources, probe = stand
    # No browser pool: the catalog cannot be captured (and no test opens a real browser).
    sources.players.get("vibix")._browser_getter = None

    reply = await probe.request("sources.video.stream", {"url": link_of("https://1.ruchime.org/release/x/episode-1", 1, 2)},
                                timeout=5)

    # Without the pool the player is unavailable, which may pass.
    assert reply["handled"] is True and reply["transient"] is True and reply["error"]


# --- the handlers the web providers still answer through ---------------------------------------------------------------------

async def test_a_provider_without_a_layout_answers_an_empty_one_and_an_empty_coverage(stand):
    _sources, probe = stand

    layout = await probe.request("sources.video.layout", {"url": DOLLS}, timeout=5)
    coverage = await probe.request("sources.video.coverage", {"url": DOLLS}, timeout=5)

    assert layout == {"sources": [], "selected_source": None, "reason": ""}
    assert coverage == {"players": {}, "reason": ""}


# --- the class guards ---------------------------------------------------------------------------------------------------------

def test_every_phase_of_a_read_has_its_wording_and_every_wording_has_a_phase():
    catalog = (ROOT / "web" / "src" / "i18n" / "ru.ts").read_text(encoding="utf-8")
    worded = set(re.findall(r'"sources\.phase\.(\w+)"', catalog))

    assert worded == set(PHASES)


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
