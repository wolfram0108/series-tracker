"""The address and the work folder of the alignment server: checked, stored, and taken at once without a restart."""
import json
from pathlib import Path

import asyncio

import pytest

from core import BaseModule, Bus, Runner
from core.contracts import ConformSettingsDraft
from modules.conform import ConformModule
from modules.conform import settings as conform_settings
from modules.gateway import SSE_MAP
from conform_fakes import FakeClient

HERE, THERE = "/storage/work", "/mnt/share/work"


def draft(url="http://daemon:8787", here=HERE, there=THERE):
    return ConformSettingsDraft(url=url, work_dir=here, work_dir_remote=there)


class Settings(BaseModule):
    """The settings module as far as the bridge uses it: a dict behind get and set_many."""

    name = "settings"

    def __init__(self, bus, values=None):
        self.values = dict(values or {})
        super().__init__(bus)

    def register(self):
        self.handle("settings.value.get", self.on_get)
        self.handle("settings.values.by_prefix", self.on_prefix)
        self.handle("settings.values.set_many", self.on_set_many)

    async def on_prefix(self, env):
        return {"values": {key: value for key, value in self.values.items() if key.startswith(env.payload["prefix"])}}

    async def on_get(self, env):
        return {"key": env.payload["key"], "value": self.values.get(env.payload["key"])}

    async def on_set_many(self, env):
        self.values.update(env.payload["values"])
        return {"values": env.payload["values"]}


class Probe(BaseModule):
    name = "probe"


# What the service asked before the parameters were settings: what a store with none of them gives.
DEFAULT_PARAMS = {"audio_method": "muq", "drift_speed_pct": None, "keep_tmp": False, "fill_silence": True, "extend_edges": True}
DOMAIN = {"audio_method": {"values": ["band", "muq"], "default": "band"},
          "drift_speed_pct": {"min": 0.25, "max": 10.0, "default": 1.25}}


async def bridge(stored=None, default_url=""):
    bus = Bus()
    settings, probe, made = Settings(bus, stored), Probe(bus), []

    def factory(url):
        client = FakeClient()
        client.base_url, client.closed = url, False

        async def close():
            client.closed = True

        client.close = close
        made.append(client)
        return client

    runner = Runner(bus, [settings, ConformModule(bus, default_url=default_url, client_factory=factory,
                                                  backoff_start=0.01), probe])
    await runner.start()
    return bus, settings, probe, made, runner


def codes(saved):
    return sorted((problem["field"], problem["detail"]["code"]) for problem in saved["problems"])


@pytest.mark.asyncio
async def test_nothing_stored_the_address_of_the_environment_applies_and_no_folder_is_assumed():
    _, _, probe, made, runner = await bridge(default_url="http://env:8787")
    try:
        view = await probe.request("conform.settings.get", {}, timeout=3)
        assert view == {"url": "http://env:8787", "work_dir": "", "work_dir_remote": "", "clear_finished": False,
                        "params": DEFAULT_PARAMS, "domain": None, "revision": 0}
        assert [c.base_url for c in made] == ["http://env:8787"]
        assert await probe.request("conform.workspace", {}, timeout=3) == {"downloads": "", "conform": ""}
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_stored_address_beats_the_environment_and_a_stored_empty_one_means_none():
    _, _, probe, made, runner = await bridge({"conform.url": "http://stored:1"}, default_url="http://env:8787")
    try:
        assert [c.base_url for c in made] == ["http://stored:1"]
    finally:
        await runner.stop()
    _, _, _, made, runner = await bridge({"conform.url": ""}, default_url="http://env:8787")
    try:
        assert made == []
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_saved_address_is_written_announced_and_taken_at_once():
    bus, settings, probe, made, runner = await bridge(default_url="http://env:8787")
    changed = bus.subscribe("conform.settings.changed")
    try:
        saved = await probe.request("conform.settings.set", draft("http://new:9000/", HERE + "/").model_dump(), timeout=5)
        assert saved["saved"] is True and saved["problems"] == [] and saved["settings"] == {
            "url": "http://new:9000", "work_dir": HERE, "work_dir_remote": THERE, "clear_finished": False,
            "params": DEFAULT_PARAMS, "domain": None, "revision": 1}
        assert settings.values == {"conform.url": "http://new:9000", "conform.work_dir": HERE,
                                   "conform.work_dir_remote": THERE, "conform.revision": "1"}
        assert [c.base_url for c in made] == ["http://env:8787", "http://new:9000"]
        assert made[0].closed is True and made[1].closed is False
        assert (await announced(changed, lambda view: view["url"] == "http://new:9000"))["url"] == "http://new:9000"
        assert await probe.request("conform.workspace", {}, timeout=3) == {
            "downloads": f"{HERE}/downloads", "conform": f"{HERE}/conform"}
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_the_choice_to_clear_finished_jobs_is_read_from_the_pipeline_flag_and_a_save_never_writes_it():
    _, settings, probe, _, runner = await bridge({"conform.clear_finished": "true"}, default_url="http://env:8787")
    try:
        assert (await probe.request("conform.settings.get", {}, timeout=3))["clear_finished"] is True
        saved = await probe.request("conform.settings.set", draft().model_dump(), timeout=5)
        assert saved["saved"] is True and saved["settings"]["clear_finished"] is True
        assert settings.values["conform.clear_finished"] == "true"  # the save left the flag as the debug tab set it
        settings.values["conform.clear_finished"] = "false"
        assert (await probe.request("conform.settings.get", {}, timeout=3))["clear_finished"] is False
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_saving_an_empty_address_puts_the_bridge_to_rest():
    _, settings, probe, made, runner = await bridge(default_url="http://env:8787")
    try:
        saved = await probe.request("conform.settings.set", draft(url="").model_dump(), timeout=5)
        assert saved["saved"] is True and settings.values["conform.url"] == ""
        assert made[0].closed is True
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_refused_save_writes_nothing_and_changes_nothing():
    _, settings, probe, made, runner = await bridge(default_url="http://env:8787")
    try:
        saved = await probe.request("conform.settings.set", draft(url="ftp://x", here="storage", there="").model_dump(), timeout=5)
        assert saved["saved"] is False and settings.values == {}
        assert codes(saved) == [("url", "conform_url_invalid"), ("work_dir", "work_dir_not_absolute"),
                                ("work_dir_remote", "work_dir_daemon_missing")]
        assert [c.base_url for c in made] == ["http://env:8787"] and saved["settings"]["url"] == "http://env:8787"
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_an_address_needs_both_names_of_the_folder_and_a_missing_address_needs_none():
    _, settings, probe, _, runner = await bridge()
    try:
        refused = await probe.request("conform.settings.set", draft(here="").model_dump(), timeout=5)
        assert codes(refused) == [("work_dir", "work_dir_here_missing")] and settings.values == {}
        saved = await probe.request("conform.settings.set", draft(url="", here="", there="").model_dump(), timeout=5)
        assert saved["saved"] is True
    finally:
        await runner.stop()


def test_the_work_folder_here_is_never_the_top_of_a_storage(monkeypatch):
    monkeypatch.setattr("modules.conform.settings.os.path.ismount", lambda path: path == "/storage")
    found = lambda **changes: [p.detail.code for p in conform_settings.check(draft(**changes))]
    assert found() == [] and found(here="/storage") == ["work_dir_is_root"] and found(there="/") == ["work_dir_is_root"]
    assert found(here="/") == ["work_dir_is_root"]


def test_a_draft_is_cleaned_before_it_is_checked_and_written():
    cleaned = conform_settings.clean(draft(url=" http://d:1// ", here=" /storage/w/ ", there="/m/w/"))
    assert (cleaned.url, cleaned.work_dir, cleaned.work_dir_remote) == ("http://d:1", "/storage/w", "/m/w")
    assert conform_settings.mapping_of("/storage/w", "/m/w") == {"/storage/w": "/m/w"}
    assert conform_settings.mapping_of("/storage/w", "") == {} and conform_settings.mapping_of(None, None) == {}


def test_a_saved_address_reaches_every_open_screen_by_the_stream_and_the_screen_listens_for_it():
    root = Path(__file__).resolve().parent.parent
    name = SSE_MAP["conform.settings.changed"].name
    assert name == "conform_settings_update"
    assert f"`{name}`" in (root / "contracts" / "sse_contract.md").read_text(encoding="utf-8")
    assert f'"{name}"' in (root / "web" / "src" / "composables" / "useRealtime.ts").read_text(encoding="utf-8")



# --- how the daemon is asked to align --------------------------------------------------------------------------------

async def announced(changed, matches, seconds: float = 3.0) -> dict:
    """The announced view that matches: the bridge also announces the view when the daemon connects."""
    async def first() -> dict:
        while True:
            view = (await changed.queue.get()).payload
            if matches(view):
                return view
    return await asyncio.wait_for(first(), seconds)


def params(**changes) -> dict:
    return {**DEFAULT_PARAMS, **changes}


@pytest.mark.asyncio
async def test_the_parameters_are_checked_against_what_the_daemon_accepts_and_written_whole():
    bus, settings, probe, made, runner = await bridge(default_url="http://env:8787")
    changed = bus.subscribe("conform.settings.changed")
    try:
        # The daemon has not said what it accepts: nothing can be checked, nothing is written.
        unknown = await probe.request("conform.params.set", params(audio_method="band"), timeout=5)
        assert unknown["saved"] is False and [p["detail"]["code"] for p in unknown["problems"]] == ["conform_params_unknown"]
        assert settings.values == {}

        probe_bridge = next(each for each in runner.modules if each.name == "conform")
        probe_bridge._daemon["params"] = DOMAIN
        wrong = await probe.request("conform.params.set", params(audio_method="dsp", drift_speed_pct=11.0), timeout=5)
        assert [p["field"] for p in wrong["problems"]] == ["audio_method", "drift_speed_pct"] and settings.values == {}

        saved = await probe.request("conform.params.set", params(audio_method="band", drift_speed_pct=2.5, keep_tmp=True,
                                                                 fill_silence=False, extend_edges=False), timeout=5)
        assert saved["saved"] is True and saved["settings"]["params"] == params(
            audio_method="band", drift_speed_pct=2.5, keep_tmp=True, fill_silence=False, extend_edges=False)
        assert saved["settings"]["domain"] == {"audio_methods": ["band", "muq"], "audio_default": "band",
                                               "drift_min": 0.25, "drift_max": 10.0, "drift_default": 1.25}
        assert settings.values == {"conform.audio_method": "band", "conform.drift_speed_pct": "2.5",
                                   "conform.keep_tmp": "true", "conform.fill_silence": "false",
                                   "conform.extend_edges": "false", "conform.revision": "1"}
        assert (await announced(changed, lambda view: view["params"]["audio_method"] == "band"))["params"]["keep_tmp"]
    finally:
        await runner.stop()


def test_the_jobs_carry_the_parameters_each_its_own_and_a_drift_of_none_is_the_daemons():
    from modules.conform import settings as conform_settings
    chosen = conform_settings.params_of({"conform.audio_method": "band", "conform.drift_speed_pct": "2.5",
                                         "conform.keep_tmp": "true", "conform.fill_silence": "false",
                                         "conform.extend_edges": "false"})
    assert conform_settings.episode_fields(chosen) == {"audio_method": "band", "keep_tmp": True, "fill_silence": False,
                                                       "drift_speed_pct": 2.5}
    assert conform_settings.compilation_fields(chosen) == {"audio_method": "band", "keep_tmp": True,
                                                           "extend_edges": False, "drift_speed_pct": 2.5}
    # Nothing stored, or a stored value of another kind: what the service asked before.
    assert conform_settings.params_of({"conform.drift_speed_pct": "fast", "conform.keep_tmp": "maybe"}).model_dump() == \
        DEFAULT_PARAMS
    assert conform_settings.episode_fields(conform_settings.DEFAULTS) == {"audio_method": "muq", "keep_tmp": False,
                                                                         "fill_silence": True}
