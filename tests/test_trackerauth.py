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
                 content_type="text/html"):
        self.url = url
        self.text = text
        self.status_code = status_code
        self.content = text.encode()
        self.headers = {"Content-Type": content_type}
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
