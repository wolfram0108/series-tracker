"""Recovery of a rejected Alloha identity: one capture, validation before publishing, persistence and safe file restart."""
import asyncio
import os
from types import SimpleNamespace

import pytest

from core.reasons import code_of
from modules.sources.players import alloha
from modules.sources.players.alloha import AllohaPlayer
from modules.sources.players.alloha_profile import Profiles, capture_profile
from modules.sources.players.alloha_session import AllohaRefused, Profile
from alloha_model import CDN, HOST, PROFILE, AllohaModel, Client, Response
from test_alloha_player import PAGE, episode_ref

OLD = Profile("e" * 64, PROFILE.user_agent)


async def checked(_profile):
    pass


async def test_cancelling_a_waiter_does_not_cancel_the_shared_capture_and_shutdown_does():
    profiles = Profiles(OLD)
    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def capture(_url):
        entered.set()
        try:
            await release.wait()
            return PROFILE
        finally:
            closed.set()

    profiles.capture = capture
    owner = asyncio.create_task(profiles.refresh(0, PAGE, checked))
    await entered.wait()
    waiter = asyncio.create_task(profiles.refresh(0, PAGE, checked))
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert not closed.is_set()
    release.set()
    await waiter
    assert profiles.profile == PROFILE and closed.is_set()

    profiles = Profiles(OLD)
    profiles.capture = capture
    entered.clear()
    closed.clear()
    release.clear()
    owner = asyncio.create_task(profiles.refresh(0, PAGE, checked))
    await entered.wait()
    await profiles.close()
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert closed.is_set() and profiles.profile is OLD


async def test_only_a_validated_profile_is_saved_encrypted_and_loaded_after_restart(tmp_path):
    path = tmp_path / "profile.enc"
    profiles = Profiles(OLD)
    profiles.path = path

    async def capture(_url):
        return PROFILE

    async def rejected(_profile):
        assert not path.exists() and profiles.profile is OLD
        raise AllohaRefused("rejected", status=403, profile_rejected=True)

    profiles.capture = capture
    with pytest.raises(AllohaRefused) as failure:
        await profiles.refresh(0, PAGE, rejected)
    assert code_of(str(failure.value)) == "alloha_profile_refresh_failed"
    assert profiles.profile is OLD and not path.exists()
    with pytest.raises(AllohaRefused) as failure:
        await profiles.refresh(0, PAGE, checked)
    assert code_of(str(failure.value)) == "alloha_profile_refresh_cooldown"

    profiles._not_before = 0
    await profiles.refresh(0, PAGE, checked)
    raw = path.read_text()
    assert raw.startswith("gAAAAA") and PROFILE.fingerprint not in raw and PROFILE.user_agent not in raw
    assert os.stat(path).st_mode & 0o777 == 0o600
    restored = Profiles(OLD)
    restored.path = path
    await restored.load()
    assert restored.profile == PROFILE
    assert list(tmp_path.iterdir()) == [path]


class Browser:
    def __init__(self, headers=None, fail=False):
        self.closed = 0
        self.headers = headers or {"borth": PROFILE.fingerprint + "|fresh-signature", "user-agent": PROFILE.user_agent}
        self.fail = fail
        self.page = self

    async def new_context(self, **_options):
        return self

    async def new_page(self):
        return self

    async def route(self, url, handler):
        self.embedding_url = url
        await handler(self)

    async def fulfill(self, **reply):
        self.embedding_html = reply["body"]

    def expect_request(self, predicate, timeout):
        self.predicate = predicate
        return self

    async def __aenter__(self):
        self.value = asyncio.get_running_loop().create_future()
        return self

    async def __aexit__(self, *_exc):
        pass

    async def goto(self, url, **options):
        self.opened = (url, options)
        if self.fail:
            raise TimeoutError("navigation")
        assert not self.predicate(SimpleNamespace(url="https://other.test/bnsi/movies/1", method="POST"))
        assert not self.predicate(SimpleNamespace(url=HOST + "/bnsi/movies/1", method="GET"))
        assert self.predicate(SimpleNamespace(url=HOST + "/bnsi/movies/1", method="POST"))
        self.value.set_result(self)

    async def all_headers(self):
        return self.headers

    async def close(self):
        self.closed += 1


@pytest.mark.parametrize("fail", [False, True])
async def test_capture_uses_the_players_real_request_and_closes_the_context_on_failure_too(fail):
    browser = Browser(fail=fail)

    async def get():
        return browser

    if fail:
        with pytest.raises(TimeoutError):
            await capture_profile(get, HOST + "/?token_movie=1", PAGE)
    else:
        assert await capture_profile(get, HOST + "/?token_movie=1", PAGE) == PROFILE
        assert browser.opened[0] == browser.embedding_url == PAGE
        assert 'src="' + HOST + '/?token_movie=1"' in browser.embedding_html
    assert browser.closed == 1


class RecoveringClient(Client):
    def __init__(self, model, profile, *, reject=False, wrote=None):
        super().__init__(model, profile)
        self.reject, self.wrote = reject, wrote

    async def request(self, method, url, **kw):
        if self.profile == OLD and url.startswith(CDN) and self.reject:
            if self.wrote is not None and (url.endswith("master.m3u8") or url.endswith("high.m3u8")
                                          or url.endswith("init.mp4") or url.endswith("seg0.m4s")):
                return await super().request(method, url, **kw)
            if self.wrote is not None:
                await self.wrote.wait()
            return Response(403, b"blocked", {"x-vd": "session_blocked"}, url=url)
        if self.profile == OLD and "/bnsi/" in url:
            kw["headers"] = {**kw["headers"], "Borth": kw["headers"]["Borth"].replace(OLD.fingerprint, PROFILE.fingerprint)}
        return await super().request(method, url, **kw)


async def test_a_parser_request_with_an_explicit_profile_refusal_can_recover_too(monkeypatch):
    from modules.sources.players.contract import Kind
    monkeypatch.setattr(alloha, "ASK_INTERVAL_S", 0)
    model = AllohaModel()

    class RefusedFiles(RecoveringClient):
        async def request(self, method, url, **kw):
            if self.profile == OLD and "/bnsi/" in url:
                return Response(403, b"", {"x-vd": "client_blocked"}, url=url)
            return await super().request(method, url, **kw)

    player = AllohaPlayer(OLD, lambda profile: RefusedFiles(model, profile))
    captures = []

    async def capture(url):
        captures.append(url)
        return PROFILE

    player._profiles.capture = capture
    reading = player.read(await player.fetch(episode_ref(1), None, PAGE))
    assert reading.kind is Kind.PLAYABLE and captures == [episode_ref(1).url]
