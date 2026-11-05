"""Тесты trackerauth: персистентность кук, релогин при протухании,
rate-limit, антибот-челлендж. Сеть замокана на уровне ТРАНСПОРТА —
путь requests (хуки на ответ, склейка заголовков, куки) остаётся
настоящим, иначе тест не видит того, что видит боевой код.
"""
import asyncio
import json
import subprocess
import sys

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


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"},
        cwd=".", check=True, capture_output=True)
    return str(path)


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
async def test_fetch_with_live_session(system, transport):
    _, _, module, probe, _ = system
    transport.handler = lambda req: _response(req.url,
                                              text="<html>раздача</html>")
    reply = await probe.request("trackerauth.fetch", {
        "service": "kinozal", "url": KZ_URL}, timeout=5)
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
async def test_stale_session_triggers_relogin_and_retry(system, transport):
    _, db, module, probe, _ = system
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
    assert reply["status"] == 200 and "раздача" in reply["text"]
    urls = [r.url for r in transport.sent]
    assert sum("takelogin.php" in u for u in urls) == 1, \
        "должен был случиться ровно один релогин"
    assert sum("details.php" in u for u in urls) == 2, \
        "исходный запрос должен повториться"

    row = await db.fetch_one("SELECT * FROM tracker_sessions "
                             "WHERE service='kinozal'")
    assert row is not None and row["last_login_at"]


@pytest.mark.asyncio
async def test_login_rate_limit_protects_tracker(system, transport):
    _, _, module, probe, _ = system
    transport.handler = lambda req: (
        _response("https://kinozal.me/userdetails.php")
        if "takelogin.php" in req.url else _login_form(req.url))

    # первый fetch: протухло → релогин → всё ещё страница логина → ошибка
    with pytest.raises(BusRequestError, match="разлогинены"):
        await probe.request("trackerauth.fetch", {
            "service": "kinozal", "url": KZ_URL}, timeout=5)

    # второй fetch сразу же: релогин запрещён rate-limit'ом
    with pytest.raises(BusRequestError, match="rate-limit"):
        await probe.request("trackerauth.fetch", {
            "service": "kinozal", "url": KZ_URL}, timeout=5)


@pytest.mark.asyncio
async def test_failed_login_does_not_consume_rate_limit(system, transport):
    """Неудачный логин (сетевая ошибка/таймаут одного зеркала) НЕ должен
    выставлять rate-limit и блокировать логин на другом доступном
    зеркале того же трекера — иначе перебор зеркал теряет смысл."""
    _, _, module, probe, _ = system
    posts = {"n": 0}

    def handler(req):
        if "takelogin.php" not in req.url:
            return _login_form(req.url)
        posts["n"] += 1
        if posts["n"] == 1:  # первое зеркало: таймаут на логине
            raise requests.exceptions.ConnectionError("Read timed out")
        return _response("https://kinozal.tv/userdetails.php")

    transport.handler = handler

    with pytest.raises(BusRequestError):
        await probe.request("trackerauth.fetch", {
            "service": "kinozal", "url": KZ_URL}, timeout=5)

    # второе зеркало сразу же: rate-limit НЕ блокирует — логин реально
    # вызывается (дальше «разлогинены», т.к. транспорт всё ещё отдаёт
    # форму входа; до бага здесь была бы ошибка rate-limit)
    with pytest.raises(BusRequestError, match="разлогинены"):
        await probe.request("trackerauth.fetch", {
            "service": "kinozal",
            "url": "https://kinozal.tv/details.php?id=1"}, timeout=5)
    assert posts["n"] == 2, "второй логин должен состояться, не отсечён лимитом"


# --- антибот-челлендж (Cloudflare) ------------------------------------------------

@pytest.mark.asyncio
async def test_challenge_passed_by_browser_then_request_retried(system,
                                                                transport):
    """Челлендж — не разлогин: сессию не трогаем, поднимаем браузер,
    забираем клирэнс и повторяем ИСХОДНЫЙ запрос уже с ним."""
    from core import crypto
    _, db, _, probe, monkeypatch = system
    calls = {"solves": 0}

    transport.handler = lambda req: (_challenge(req.url) if not calls["solves"]
                                     else _response(req.url,
                                                    text="<html>тема</html>"))
    monkeypatch.setattr(ta_module.cf_challenge, "solve", _solver(calls))

    reply = await probe.request("trackerauth.fetch", {
        "service": "rutracker", "url": RT_URL}, timeout=10)

    assert reply["status"] == 200 and "тема" in reply["text"]
    assert calls["solves"] == 1, "браузер поднимается ровно один раз"
    assert len(transport.sent) == 2, "исходный запрос должен повториться"
    assert transport.agents[1] == "ClearanceUA/1.0", \
        "повтор идёт с UA, под которым выдан клирэнс"

    row = await db.fetch_one(
        "SELECT * FROM tracker_sessions WHERE service='rutracker'")
    assert row["user_agent"] == "ClearanceUA/1.0"
    assert json.loads(crypto.decrypt(row["cookies_json"]))["cf_clearance"] == \
        "КЛИРЭНС", \
        "клирэнс персистится — после рестарта челлендж проходить заново не надо"


@pytest.mark.asyncio
async def test_challenge_detected_for_tracker_that_never_declared_it(
        system, transport):
    """Защита включается на стороне сайта и трекеру не принадлежит:
    челлендж на kinozal распознаётся так же, как на rutracker, без единой
    строки в провайдере."""
    _, _, _, probe, monkeypatch = system
    calls = {"solves": 0}

    transport.handler = lambda req: (_challenge(req.url) if not calls["solves"]
                                     else _response(req.url,
                                                    text="<html>раздача</html>"))
    monkeypatch.setattr(ta_module.cf_challenge, "solve", _solver(calls))

    reply = await probe.request("trackerauth.fetch", {
        "service": "kinozal", "url": KZ_URL}, timeout=10)

    assert reply["status"] == 200 and "раздача" in reply["text"]
    assert calls["solves"] == 1


@pytest.mark.asyncio
async def test_login_passes_challenge_and_retries(system, transport):
    """Челлендж на странице входа: логин обязан пройти его и повториться,
    иначе разлогиненная сессия не восстанавливается никогда."""
    _, _, _, probe, monkeypatch = system
    calls = {"solves": 0, "logins": 0}

    def handler(req):
        if "takelogin.php" in req.url:
            calls["logins"] += 1
            if calls["logins"] == 1:
                return _challenge(req.url)
            return _response("https://kinozal.me/userdetails.php")
        return (_login_form(req.url) if not calls["solves"]
                else _response(req.url, text="<html>раздача</html>"))

    transport.handler = handler
    monkeypatch.setattr(ta_module.cf_challenge, "solve", _solver(calls))

    reply = await probe.request("trackerauth.fetch", {
        "service": "kinozal", "url": KZ_URL}, timeout=10)

    assert reply["status"] == 200 and "раздача" in reply["text"]
    assert calls["logins"] == 2, "вход повторяется после прохождения"
    assert calls["solves"] == 1


@pytest.mark.asyncio
async def test_login_request_carries_clearance_user_agent(system, transport):
    """Клирэнс действителен только со «своим» UA, поэтому и вход обязан
    идти под ним: иначе защита разворачивает нас прямо на логине."""
    _, _, _, probe, monkeypatch = system
    calls = {"solves": 0}

    def handler(req):
        if "takelogin.php" in req.url:
            return _response("https://kinozal.me/userdetails.php")
        if not calls["solves"]:
            return _challenge(req.url)
        return (_login_form(req.url) if len(transport.sent) < 3
                else _response(req.url, text="<html>раздача</html>"))

    transport.handler = handler
    monkeypatch.setattr(ta_module.cf_challenge, "solve", _solver(calls))

    await probe.request("trackerauth.fetch", {
        "service": "kinozal", "url": KZ_URL}, timeout=10)

    login = [r for r in transport.sent if "takelogin.php" in r.url]
    assert login and all(r.headers["User-Agent"] == "ClearanceUA/1.0"
                         for r in login)


@pytest.mark.asyncio
async def test_challenge_rate_limit_protects_from_browser_loop(system,
                                                               transport):
    """Если клирэнс не помогает, браузер не должен запускаться по кругу."""
    _, _, _, probe, monkeypatch = system
    calls = {"solves": 0}

    transport.handler = lambda req: _challenge(req.url)
    monkeypatch.setattr(ta_module.cf_challenge, "solve", _solver(calls, "UA"))

    with pytest.raises(BusRequestError, match="не пускает"):
        await probe.request("trackerauth.fetch", {
            "service": "rutracker", "url": RT_URL}, timeout=10)

    with pytest.raises(BusRequestError, match="rate-limit"):
        await probe.request("trackerauth.fetch", {
            "service": "rutracker", "url": RT_URL}, timeout=10)
    assert calls["solves"] == 1, "второй заход отсечён лимитом, браузер не поднят"


@pytest.mark.asyncio
async def test_clearance_survives_relogin(system, transport):
    """Клирэнс — про клиента, а не про учётку: смена логина его не роняет
    (иначе каждый релогин тянул бы лишний прогон браузера)."""
    _, _, module, _, _ = system
    transport.handler = lambda req: _response(
        "https://kinozal.me/userdetails.php")
    session = requests.Session()
    session.cookies.set("cf_clearance", "КЛИРЭНС", domain="kinozal.me")
    session.cookies.set("uid", "42", domain="kinozal.me")

    await module._login("kinozal", "https://kinozal.me/x", session)

    assert session.cookies.get("cf_clearance") == "КЛИРЭНС"
    assert session.cookies.get("uid") is None, "прочие куки логин сбрасывает"


# --- личность сессии --------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_agent_restored_with_cookies(system):
    """UA прогретой сессии восстанавливается вместе с куками: врозь
    cf_clearance недействителен."""
    _, db, module, _, _ = system
    await module.repo.save_cookies(
        "rutracker", "rutracker.org", requests.Session(),
        logged_in=False, user_agent="ClearanceUA/1.0")

    module._sessions.clear()
    session = await module._get_session("rutracker", RT_URL)

    assert session.headers["User-Agent"] == "ClearanceUA/1.0"


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


@pytest.mark.asyncio
async def test_cookies_survive_restart(system, transport):
    bus, db, module, probe, _ = system
    await probe.request("trackerauth.fetch", {
        "service": "kinozal", "url": "https://kinozal.me/x"}, timeout=5)

    # «рестарт»: новый экземпляр модуля с той же БД. Отпечаток учётки —
    # текущий: вход переживает рестарт, пока учётные данные те же.
    module._sessions.clear()
    await db.execute(
        "UPDATE tracker_sessions SET cookies_json=?, account_fingerprint=?",
        (json.dumps({"uid": "42", "pass": "secret"}),
         account_fingerprint({"username": "user", "password": "pass"})))
    session = await module._get_session("kinozal", "https://kinozal.me/x")
    assert session.cookies.get("uid") == "42", \
        "куки должны восстановиться из БД без нового логина"


# --- вход привязан к учётным данным (находка 69) -----------------------------------

def test_account_fingerprint_follows_credentials():
    """Отпечаток меняется и от логина, и от пароля: при любой смене
    учётных данных прежний вход становится неприменимым."""
    base = {"username": "user", "password": "pass"}
    assert account_fingerprint(base) == account_fingerprint(dict(base))
    assert account_fingerprint(
        {"username": "other", "password": "pass"}) != account_fingerprint(base)
    assert account_fingerprint(
        {"username": "user", "password": "other"}) != account_fingerprint(base)
    assert account_fingerprint(None) is None


@pytest.mark.asyncio
async def test_username_trimmed_on_save(system):
    """Пробелы по краям логина уходили в форму входа как есть."""
    _, db, _, probe, _ = system
    await probe.request("trackerauth.credentials.set",
                        {"service": "rutracker", "username": "  vova  ",
                         "password": "p"}, timeout=5)
    row = await db.fetch_one(
        "SELECT username FROM auth WHERE auth_type='rutracker'")
    assert row["username"] == "vova"


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


@pytest.mark.asyncio
async def test_session_of_same_account_is_reused(system, transport):
    """Учётка не менялась — прежний вход применяется, лишнего входа нет
    (трекер не должен видеть входов на каждый запрос)."""
    _, _, module, probe, _ = system
    await _store_session(module, account_fingerprint(
        {"username": "user", "password": "pass"}))

    transport.handler = lambda req: _response(req.url,
                                              text="<html>раздача</html>")
    reply = await probe.request("trackerauth.fetch", {
        "service": "kinozal", "url": KZ_URL}, timeout=5)
    assert reply["status"] == 200

    first_cookies = transport.sent[0].headers.get("Cookie") or ""
    assert "uid=OLDACCOUNT" in first_cookies
    assert not [r for r in transport.sent if "takelogin.php" in r.url]


@pytest.mark.asyncio
async def test_session_without_fingerprint_is_kept(system, transport):
    """«Неизвестно, под какой учёткой» — не то же, что «под другой».
    Запись старше привязки обязана работать: форма входа RuTracker
    содержит капчу (находка 65), автоматически восстановиться нечем."""
    _, db, module, probe, _ = system
    await _store_session(module, None)      # отпечатка нет, как у старых записей
    assert (await db.fetch_one(
        "SELECT account_fingerprint AS f FROM tracker_sessions "
        "WHERE service='kinozal'"))["f"] is None

    transport.handler = lambda req: _response(req.url,
                                              text="<html>раздача</html>")
    reply = await probe.request("trackerauth.fetch", {
        "service": "kinozal", "url": KZ_URL}, timeout=5)
    assert reply["status"] == 200

    assert "uid=OLDACCOUNT" in (transport.sent[0].headers.get("Cookie") or ""), \
        "сессия без отпечатка должна применяться, а не выбрасываться"
    assert not [r for r in transport.sent if "takelogin.php" in r.url], \
        "лишнего входа быть не должно"


@pytest.mark.asyncio
async def test_credentials_change_drops_account_cookies_in_db(system):
    """Смена учётных данных отменяет прежний вход в ОБОИХ слоях: сброс
    только памяти оставлял куки в БД, откуда они и восстанавливались."""
    from core import crypto
    _, db, module, probe, _ = system
    await _store_session(module, account_fingerprint(
        {"username": "user", "password": "pass"}))

    await probe.request("trackerauth.credentials.set",
                        {"service": "kinozal", "username": "newuser",
                         "password": "newpass"}, timeout=5)

    row = await db.fetch_one("SELECT cookies_json FROM tracker_sessions "
                             "WHERE service='kinozal'")
    cookies = json.loads(crypto.decrypt(row["cookies_json"]))
    assert "uid" not in cookies, "куки авторизации обязаны уйти из БД"
    assert cookies.get("cf_clearance") == "CLEARANCE1", \
        "клирэнс про клиента — переживает смену учётки"
