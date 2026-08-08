"""Тесты trackerauth: персистентность кук, релогин при протухании,
rate-limit. Сеть полностью замокана; живые трекеры — только этап 6.
"""
import asyncio
import json
import subprocess
import sys

import pytest
import requests

from core import Bus, BaseModule, BusRequestError, Runner
from core.db import Database
from modules.trackerauth import TrackerauthModule
from modules.trackerauth import module as ta_module


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"},
        cwd=".", check=True, capture_output=True)
    return str(path)


class FakeResponse:
    def __init__(self, *, url, text="", status_code=200,
                 content_type="text/html", headers=None):
        self.url = url
        self.text = text
        self.status_code = status_code
        self.content = text.encode()
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.cookies = requests.cookies.RequestsCookieJar()

    def raise_for_status(self):
        pass


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


def _ok_page(url="https://kinozal.me/details.php?id=1"):
    return FakeResponse(url=url, text="<html>раздача</html>")


def _login_page(url="https://kinozal.me/details.php?id=1"):
    # реальный Kinozal отдаёт форму со слешем — action="/takelogin.php"
    # (находка 42: детектор должен распознавать её, а не точную строку)
    return FakeResponse(url=url,
                        text='<form method="post" action="/takelogin.php">'
                             'вход</form>')


@pytest.mark.asyncio
async def test_fetch_with_live_session(system):
    _, _, module, probe, monkeypatch = system
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, *a, **kw: _ok_page())
    reply = await probe.request("trackerauth.fetch", {
        "service": "kinozal", "url": "https://kinozal.me/details.php?id=1"},
        timeout=5)
    assert reply["status"] == 200
    assert "раздача" in reply["text"]


@pytest.mark.asyncio
async def test_password_encrypted_at_rest(system):
    """Этап 3Б: legacy-пароль зашифрован на старте; наружу — флаг, не секрет."""
    from core import crypto
    _, db, _, probe, _ = system
    row = await db.fetch_one(
        "SELECT password FROM auth WHERE auth_type='kinozal'")
    assert row["password"] != "pass"            # в БД не открытый текст
    assert crypto.is_encrypted(row["password"])
    reply = await probe.request("trackerauth.credentials.get",
                                {"service": "kinozal"}, timeout=5)
    assert reply["credentials"]["has_password"] is True
    assert "password" not in reply["credentials"]  # секрет не отдаётся (Этап 3А)


@pytest.mark.asyncio
async def test_set_credentials_encrypts(system):
    """Новый пароль через credentials.set ложится в БД зашифрованным."""
    from core import crypto
    _, db, _, probe, _ = system
    await probe.request("trackerauth.credentials.set",
                        {"service": "rutracker", "username": "u",
                         "password": "topsecret"}, timeout=5)
    row = await db.fetch_one(
        "SELECT password FROM auth WHERE auth_type='rutracker'")
    assert crypto.is_encrypted(row["password"])
    assert crypto.decrypt(row["password"]) == "topsecret"


@pytest.mark.asyncio
async def test_stale_session_triggers_relogin_and_retry(system):
    _, db, module, probe, monkeypatch = system
    calls = {"requests": 0, "logins": 0}

    def fake_request(self, *args, **kwargs):
        calls["requests"] += 1
        # до логина — страница входа, после — нормальная
        return _ok_page() if calls["logins"] else _login_page()

    def fake_post(self, url, **kwargs):
        calls["logins"] += 1
        return FakeResponse(url="https://kinozal.me/userdetails.php")

    monkeypatch.setattr(requests.Session, "request", fake_request)
    monkeypatch.setattr(requests.Session, "post", fake_post)

    reply = await probe.request("trackerauth.fetch", {
        "service": "kinozal", "url": "https://kinozal.me/details.php?id=1"},
        timeout=5)
    assert reply["status"] == 200 and "раздача" in reply["text"]
    assert calls["logins"] == 1, "должен был случиться ровно один релогин"
    assert calls["requests"] == 2, "исходный запрос должен повториться"

    # куки и факт логина персистированы
    row = await db.fetch_one("SELECT * FROM tracker_sessions "
                             "WHERE service='kinozal'")
    assert row is not None and row["last_login_at"]


@pytest.mark.asyncio
async def test_login_rate_limit_protects_tracker(system):
    _, _, module, probe, monkeypatch = system
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, *a, **kw: _login_page())
    monkeypatch.setattr(requests.Session, "post",
                        lambda self, url, **kw: FakeResponse(
                            url="https://kinozal.me/userdetails.php"))

    # первый fetch: протухло → релогин → всё ещё страница логина → ошибка
    with pytest.raises(BusRequestError, match="разлогинены"):
        await probe.request("trackerauth.fetch", {
            "service": "kinozal",
            "url": "https://kinozal.me/details.php?id=1"}, timeout=5)

    # второй fetch сразу же: релогин запрещён rate-limit'ом
    with pytest.raises(BusRequestError, match="rate-limit"):
        await probe.request("trackerauth.fetch", {
            "service": "kinozal",
            "url": "https://kinozal.me/details.php?id=1"}, timeout=5)


@pytest.mark.asyncio
async def test_failed_login_does_not_consume_rate_limit(system):
    """Неудачный логин (сетевая ошибка/таймаут одного зеркала) НЕ должен
    выставлять rate-limit и блокировать логин на другом доступном
    зеркале того же трекера — иначе перебор зеркал теряет смысл."""
    _, _, module, probe, monkeypatch = system
    posts = {"n": 0}
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, *a, **kw: _login_page())

    def fake_post(self, url, **kwargs):
        posts["n"] += 1
        if posts["n"] == 1:  # первое зеркало: таймаут на логине
            raise requests.exceptions.ConnectionError("Read timed out")
        return FakeResponse(url="https://kinozal.tv/userdetails.php")

    monkeypatch.setattr(requests.Session, "post", fake_post)

    # первое зеркало (kinozal.me): логин падает по сети
    with pytest.raises(BusRequestError):
        await probe.request("trackerauth.fetch", {
            "service": "kinozal",
            "url": "https://kinozal.me/details.php?id=1"}, timeout=5)

    # второе зеркало сразу же: rate-limit НЕ блокирует — логин реально
    # вызывается (дальше «разлогинены», т.к. request-мок всё ещё отдаёт
    # форму входа; до бага здесь была бы ошибка rate-limit)
    with pytest.raises(BusRequestError, match="разлогинены"):
        await probe.request("trackerauth.fetch", {
            "service": "kinozal",
            "url": "https://kinozal.tv/details.php?id=1"}, timeout=5)
    assert posts["n"] == 2, "второй логин должен состояться, не отсечён лимитом"


# --- антибот-челлендж (Cloudflare) ------------------------------------------------

RT_URL = "https://rutracker.org/forum/viewtopic.php?t=1"


def _challenge_page():
    """Заглушка CF: статус 403 и служебный заголовок — по нему и судим."""
    return FakeResponse(url=RT_URL, status_code=403, text="Один момент…",
                        headers={"cf-mitigated": "challenge"})


@pytest.mark.asyncio
async def test_challenge_passed_by_browser_then_request_retried(system):
    """Челлендж — не разлогин: сессию не трогаем, поднимаем браузер,
    забираем клирэнс и повторяем ИСХОДНЫЙ запрос уже с ним."""
    from core import crypto
    _, db, _, probe, monkeypatch = system
    calls = {"requests": 0, "solves": 0, "agents": []}

    def fake_request(self, *args, **kwargs):
        calls["requests"] += 1
        calls["agents"].append((kwargs.get("headers") or {}).get("User-Agent"))
        if not calls["solves"]:
            return _challenge_page()
        return FakeResponse(url=RT_URL, text="<html>тема</html>")

    async def fake_solve(url, **kwargs):
        calls["solves"] += 1
        return "ClearanceUA/1.0", {"cf_clearance": "КЛИРЭНС"}

    monkeypatch.setattr(requests.Session, "request", fake_request)
    monkeypatch.setattr(ta_module.cf_challenge, "solve", fake_solve)

    reply = await probe.request("trackerauth.fetch", {
        "service": "rutracker", "url": RT_URL}, timeout=10)

    assert reply["status"] == 200 and "тема" in reply["text"]
    assert calls["solves"] == 1, "браузер поднимается ровно один раз"
    assert calls["requests"] == 2, "исходный запрос должен повториться"
    assert calls["agents"][1] == "ClearanceUA/1.0", \
        "повтор идёт с UA, под которым выдан клирэнс"

    row = await db.fetch_one(
        "SELECT * FROM tracker_sessions WHERE service='rutracker'")
    assert row["user_agent"] == "ClearanceUA/1.0"
    assert json.loads(crypto.decrypt(row["cookies_json"]))["cf_clearance"] == \
        "КЛИРЭНС", \
        "клирэнс персистится — после рестарта челлендж проходить заново не надо"


@pytest.mark.asyncio
async def test_challenge_rate_limit_protects_from_browser_loop(system):
    """Если клирэнс не помогает, браузер не должен запускаться по кругу."""
    _, _, _, probe, monkeypatch = system
    solves = {"n": 0}

    async def fake_solve(url, **kwargs):
        solves["n"] += 1
        return "UA", {"cf_clearance": "x"}

    monkeypatch.setattr(requests.Session, "request",
                        lambda self, *a, **kw: _challenge_page())
    monkeypatch.setattr(ta_module.cf_challenge, "solve", fake_solve)

    with pytest.raises(BusRequestError, match="не пускает"):
        await probe.request("trackerauth.fetch", {
            "service": "rutracker", "url": RT_URL}, timeout=10)

    with pytest.raises(BusRequestError, match="rate-limit"):
        await probe.request("trackerauth.fetch", {
            "service": "rutracker", "url": RT_URL}, timeout=10)
    assert solves["n"] == 1, "второй заход отсечён лимитом, браузер не поднят"


@pytest.mark.asyncio
async def test_clearance_survives_relogin(system):
    """Клирэнс — про клиента, а не про учётку: смена логина его не роняет
    (иначе каждый релогин тянул бы лишний прогон браузера)."""
    _, _, module, _, monkeypatch = system
    session = requests.Session()
    session.cookies.set("cf_clearance", "КЛИРЭНС", domain="kinozal.me")
    session.cookies.set("uid", "42", domain="kinozal.me")
    monkeypatch.setattr(requests.Session, "post",
                        lambda self, url, **kw: FakeResponse(
                            url="https://kinozal.me/userdetails.php"))

    await module._login("kinozal", "https://kinozal.me/x", session)

    assert session.cookies.get("cf_clearance") == "КЛИРЭНС"
    assert session.cookies.get("uid") is None, "прочие куки логин сбрасывает"


@pytest.mark.asyncio
async def test_user_agent_restored_with_cookies(system):
    """UA прогретой сессии восстанавливается вместе с куками: врозь
    cf_clearance недействителен."""
    _, db, module, _, _ = system
    await module.repo.save_cookies(
        "rutracker", "rutracker.org", requests.Session(),
        logged_in=False, user_agent="ClearanceUA/1.0")

    module._sessions.clear()
    module._user_agents.clear()
    await module._get_session("rutracker", RT_URL)

    assert module._user_agents[("rutracker", "rutracker.org")] == \
        "ClearanceUA/1.0"


@pytest.mark.asyncio
async def test_cookies_survive_restart(system):
    bus, db, module, probe, monkeypatch = system
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, *a, **kw: _ok_page())
    await probe.request("trackerauth.fetch", {
        "service": "kinozal", "url": "https://kinozal.me/x"}, timeout=5)

    # «рестарт»: новый экземпляр модуля с той же БД
    module._sessions.clear()
    await db.execute("UPDATE tracker_sessions SET cookies_json=?",
                     (json.dumps({"uid": "42", "pass": "secret"}),))
    session = await module._get_session("kinozal", "https://kinozal.me/x")
    assert session.cookies.get("uid") == "42", \
        "куки должны восстановиться из БД без нового логина"
