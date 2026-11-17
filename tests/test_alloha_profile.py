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


@pytest.mark.parametrize("status, blocked, refresh", [(403, "session_blocked", True), (403, "client_blocked", True),
                                                      (403, "", False), (404, "", False), (500, "session_blocked", False)])
async def test_only_explicit_session_or_client_refusals_trigger_refresh(status, blocked, refresh):
    from modules.sources.players.alloha_session import AllohaSession

    class Http:
        async def request(self, *_args, **_kw):
            return Response(status, b"", {"x-vd": blocked})

    session = AllohaSession(PROFILE, Http())
    with pytest.raises(AllohaRefused) as failure:
        await session._ok("GET", CDN + "/master.m3u8")
    assert failure.value.profile_rejected is refresh


@pytest.mark.parametrize("close_code, refresh", [(4005, True), (1000, False), (1006, False)])
async def test_an_explicit_socket_session_refusal_can_refresh_but_an_ordinary_disconnect_cannot(close_code, refresh):
    from modules.sources.players.alloha_session import AllohaSession, Track

    class Socket:
        async def send_str(self, _message):
            pass

        async def recv_str(self):
            raise ConnectionError("closed")

        async def close(self, **_kw):
            pass

    socket = Socket()
    socket.close_code = close_code

    async def connect(_url, **_kw):
        return socket

    session = AllohaSession(PROFILE, None)
    session.socket_url, session.socket_key = "wss://socket.test/", "test-session"
    try:
        with pytest.raises(AllohaRefused) as failure:
            await session.play(Track("1", "Voice", 720, ()), connect)
        assert failure.value.profile_rejected is refresh
        assert str(close_code) in str(failure.value)
    finally:
        await session.close()


async def test_concurrent_refusals_share_one_capture_and_a_late_refusal_uses_the_validated_generation():
    profiles = Profiles(OLD)
    entered, release = asyncio.Event(), asyncio.Event()
    captures, verified = [], []

    async def capture(url):
        captures.append(url)
        entered.set()
        await release.wait()
        return PROFILE

    async def verify(profile):
        verified.append(profile)

    profiles.capture = capture
    calls = [asyncio.create_task(profiles.refresh(0, PAGE, verify)) for _ in range(6)]
    await entered.wait()
    assert captures == [PAGE] and profiles.profile is OLD
    release.set()
    await asyncio.gather(*calls)
    await profiles.refresh(0, PAGE, verify)
    assert captures == [PAGE] and verified == [PROFILE] and profiles.generation == 1


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


async def test_an_identical_browser_fingerprint_still_gets_validated_and_advances_the_generation():
    profiles = Profiles(PROFILE)
    captures = []

    async def capture(url):
        captures.append(url)
        return PROFILE

    profiles.capture = capture
    await profiles.refresh(0, PAGE, checked)
    await profiles.refresh(0, PAGE, checked)
    assert profiles.profile == PROFILE and profiles.generation == 1 and captures == [PAGE]


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


@pytest.mark.parametrize("raw", ["private corrupt contents", '[]', '{"fingerprint":123,"user_agent":"private"}'])
async def test_a_corrupted_cache_falls_back_to_the_initial_profile_without_logging_its_contents(tmp_path, caplog, raw):
    from core import crypto
    path = tmp_path / "profile.enc"
    path.write_text(crypto.encrypt(raw))
    profiles = Profiles(OLD)
    profiles.path = path
    await profiles.load()
    assert profiles.profile is OLD and "private corrupt contents" not in caplog.text


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


@pytest.mark.parametrize("partial", [False, True])
async def test_a_rejected_transfer_validates_one_new_profile_and_restarts_the_owned_file_without_duplicate_segments(
        tmp_path, monkeypatch, partial):
    monkeypatch.setattr(alloha, "ASK_INTERVAL_S", 0)
    model, wrote = AllohaModel(segments=12), asyncio.Event()
    target = tmp_path / "test.ts"
    captures = []

    def client(profile):
        return RecoveringClient(model, profile, reject=True, wrote=wrote if partial else None)

    player = AllohaPlayer(OLD, client)

    async def capture(url):
        captures.append(url)
        if partial:
            assert target.stat().st_size > 0
        return PROFILE

    async def progress(data):
        if data.get("phase") == "download":
            wrote.set()

    player._profiles.capture = capture
    result = await asyncio.wait_for(player.transfer(episode_ref(1).url, str(target), 720, 2, progress), 5)
    assert result["height"] == 720 and captures == [episode_ref(1).url]
    assert target.read_bytes() == b"INIT" + b"".join(model.body(720, i) for i in range(12))
    assert len(model.sockets) == model.closed == 3  # failed transfer, validation, complete retry


async def test_an_existing_target_is_never_removed_or_appended_during_recovery(tmp_path):
    path = tmp_path / "existing.ts"
    path.write_bytes(b"already there")
    player = AllohaPlayer(PROFILE)
    with pytest.raises(AllohaRefused) as failure:
        await player.transfer(episode_ref(1).url, str(path), 720, 2, checked)
    assert code_of(str(failure.value)) == "alloha_target_exists"
    assert path.read_bytes() == b"already there"


async def test_no_initial_profile_is_captured_once_before_the_first_download(tmp_path, monkeypatch):
    monkeypatch.setattr(alloha, "ASK_INTERVAL_S", 0)
    model = AllohaModel()
    player = AllohaPlayer(None, model.client)
    captures = []

    async def capture(url):
        captures.append(url)
        return PROFILE

    player._profiles.capture = capture
    url = episode_ref(1).url
    assert (await player.stream(url, None, 720, None))["player_fetch"]
    done = await player.transfer(url, str(tmp_path / "first.ts"), 720, 2, checked)
    assert done["height"] == 720 and captures == [url]
    assert len(model.sockets) == model.closed == 2


async def test_a_running_sessions_media_clients_keep_the_original_profile_during_refresh():
    model = AllohaModel()
    player = AllohaPlayer(OLD, model.client)
    async with player._session() as session:
        player._profiles.profile = PROFILE
        media = session.media_client()
        assert session.profile == media.profile == OLD
        await media.close()


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


@pytest.mark.parametrize("status", [404, 503])
async def test_missing_files_and_server_failures_do_not_launch_profile_capture(tmp_path, monkeypatch, status):
    monkeypatch.setattr(alloha, "ASK_INTERVAL_S", 0)
    model = AllohaModel()
    model.refuse_files = status
    player = AllohaPlayer(PROFILE, model.client)
    captures = []

    async def capture(url):
        captures.append(url)
        return PROFILE

    player._profiles.capture = capture
    with pytest.raises(AllohaRefused):
        await player.transfer(episode_ref(1).url, str(tmp_path / "failed.ts"), 720, 2, checked)
    assert captures == []
