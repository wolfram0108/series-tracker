"""Тесты trackerauth: персистентность кук, релогин при протухании,
rate-limit, антибот-челлендж. Сеть замокана на уровне ТРАНСПОРТА —
путь requests (хуки на ответ, склейка заголовков, куки) остаётся
настоящим, иначе тест не видит того, что видит боевой код.
"""

import pytest
import requests
import requests.adapters

from core import Bus, BaseModule, BusRequestError, Runner
from core.db import Database
from modules.trackerauth import TrackerauthModule
from modules.trackerauth import module as ta_module
from modules.trackerauth.repository import account_fingerprint

KZ_URL = "https://kinozal.me/details.php?id=1"
RT_URL = "https://rutracker.org/forum/viewtopic.php?t=1"



def _response(url, *, text="", status=200, content_type="text/html",
              headers=None):
    resp = requests.Response()
    resp.url = url
    resp.status_code = status
    resp._content = text.encode()
    resp.headers.update({"Content-Type": content_type, **(headers or {})})
    resp.raw = None
    return resp


def _challenge(url):
    """Заглушка CF: статус 403 и служебный заголовок — по нему и судим."""
    return _response(url, status=403, text="Один момент…",
                     headers={"cf-mitigated": "challenge"})


def _login_form(url):
    # реальный Kinozal отдаёт форму со слешем — action="/takelogin.php"
    # (находка 42: детектор должен распознавать её, а не точную строку)
    return _response(url, text='<form method="post" action="/takelogin.php">'
                               'вход</form>')


class Transport:
    """Ответы вместо сети; помнит ушедшие запросы целиком."""

    def __init__(self):
        self.sent: list[requests.PreparedRequest] = []
        self.handler = lambda req: _response(req.url, text="<html>ok</html>")

    def send(self, request, **kwargs):
        self.sent.append(request)
        return self.handler(request)

    @property
    def agents(self) -> list[str | None]:
        return [r.headers.get("User-Agent") for r in self.sent]


@pytest.fixture
def transport(monkeypatch):
    tr = Transport()
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send",
                        lambda self, request, **kw: tr.send(request, **kw))
    return tr


@pytest.fixture
async def system(db_path, monkeypatch):
    db = Database(db_path)
    await db.execute(
        "INSERT INTO auth (auth_type, username, password) VALUES (?, ?, ?)",
        ("kinozal", "user", "pass"))
    bus = Bus()
    module = TrackerauthModule(bus, db)

    class Probe(BaseModule):
        name = "probe"

    probe = Probe(bus)
    runner = Runner(bus, [module, probe])
    await runner.start()
    yield bus, db, module, probe, monkeypatch
    await runner.stop()


def _solver(calls, user_agent="ClearanceUA/1.0"):
    async def fake_solve(url, **kwargs):
        calls["solves"] += 1
        return user_agent, {"cf_clearance": "КЛИРЭНС"}
    return fake_solve


@pytest.mark.asyncio
async def test_login_rate_limit_protects_tracker(system, transport):
    _, _, module, probe, _ = system
    transport.handler = lambda req: (
        _response("https://kinozal.me/userdetails.php")
        if "takelogin.php" in req.url else _login_form(req.url))

    # первый fetch: протухло → релогин → всё ещё страница логина → ошибка
    with pytest.raises(BusRequestError, match='"code":"tracker_still_logged_out"'):
        await probe.request("trackerauth.fetch", {
            "service": "kinozal", "url": KZ_URL}, timeout=5)

    # второй fetch сразу же: релогин запрещён rate-limit'ом
    with pytest.raises(BusRequestError, match='"code":"tracker_login_rate_limited"'):
        await probe.request("trackerauth.fetch", {
            "service": "kinozal", "url": KZ_URL}, timeout=5)


# --- антибот-челлендж (Cloudflare) ------------------------------------------------

@pytest.mark.asyncio
async def test_challenge_rate_limit_protects_from_browser_loop(system,
                                                               transport):
    """Если клирэнс не помогает, браузер не должен запускаться по кругу."""
    _, _, _, probe, monkeypatch = system
    calls = {"solves": 0}

    transport.handler = lambda req: _challenge(req.url)
    monkeypatch.setattr(ta_module.cf_challenge, "solve", _solver(calls, "UA"))

    with pytest.raises(BusRequestError, match='"code":"tracker_challenge_refused"'):
        await probe.request("trackerauth.fetch", {
            "service": "rutracker", "url": RT_URL}, timeout=10)

    with pytest.raises(BusRequestError, match='"code":"tracker_challenge_rate_limited"'):
        await probe.request("trackerauth.fetch", {
            "service": "rutracker", "url": RT_URL}, timeout=10)
    assert calls["solves"] == 1, "второй заход отсечён лимитом, браузер не поднят"


# --- личность сессии --------------------------------------------------------------

@pytest.mark.asyncio
async def test_vk_keeps_default_user_agent(system, transport):
    """VK API на браузерном UA вырезает поле files (находка 44) — трекерный
    UA сюда попадать не должен ни при каких правках."""
    _, db, module, probe, _ = system
    await db.execute(
        "INSERT INTO auth (auth_type, username, password) VALUES (?, ?, ?)",
        ("vk", "u", "token"))
    transport.handler = lambda req: _response(req.url, text="{}",
                                              content_type="application/json")

    await probe.request("trackerauth.fetch", {
        "service": "vk", "url": "https://api.vk.com/method/video.get"},
        timeout=5)

    assert transport.agents[0].startswith("python-requests/")


# --- вход привязан к учётным данным (находка 69) -----------------------------------

async def _store_session(module, fingerprint):
    """Живая сессия прежней учётки в БД: куки авторизации + клирэнс."""
    session = requests.Session()
    session.cookies.set("uid", "OLDACCOUNT", domain="kinozal.me")
    session.cookies.set("cf_clearance", "CLEARANCE1", domain="kinozal.me")
    await module.repo.save_cookies("kinozal", "kinozal.me", session,
                                   logged_in=True, fingerprint=fingerprint)


@pytest.mark.asyncio
async def test_session_of_other_account_is_not_applied(system, transport):
    """Смена учётки в настройках: прежний вход не применяется и выполняется
    вход под новой записью. Без привязки запросы продолжали идти под
    прежним аккаунтом — с его суточными лимитами и правами."""
    _, _, module, probe, _ = system
    await _store_session(module, account_fingerprint(
        {"username": "user", "password": "pass"}))
    await probe.request("trackerauth.credentials.set",
                        {"service": "kinozal", "username": "newuser",
                         "password": "newpass"}, timeout=5)

    state = {"logged_in": False}

    def handler(req):
        if "takelogin.php" in req.url:
            state["logged_in"] = True
            return _response("https://kinozal.me/userdetails.php")
        return (_response(req.url, text="<html>раздача</html>")
                if state["logged_in"] else _login_form(req.url))

    transport.handler = handler
    reply = await probe.request("trackerauth.fetch", {
        "service": "kinozal", "url": KZ_URL}, timeout=5)
    assert reply["status"] == 200

    first_cookies = transport.sent[0].headers.get("Cookie") or ""
    assert "uid=OLDACCOUNT" not in first_cookies, \
        "куки прежней учётки применяться не должны"
    assert "cf_clearance=CLEARANCE1" in first_cookies, \
        "клирэнс про клиента, а не про учётку — браузер дёргать заново не нужно"

    logins = [r for r in transport.sent if "takelogin.php" in r.url]
    assert len(logins) == 1, "вход под новой учётной записью обязан произойти"
    assert "username=newuser" in (logins[0].body or "")
