"""The address and the work folder of the alignment server: checked, stored, and taken at once without a restart."""

import asyncio

import pytest

from core.bus import Bus
from core.module import BaseModule
from core.runner import Runner
from core.contracts import ConformSettingsDraft
from modules.conform import ConformModule
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
