"""Тесты входа администратора (Этап 1, docs/security.md): модуль auth +
HTTP-замок, сессия в куке, защита от перебора.

cookie_secure=False — иначе SessionMiddleware ставит Secure-куку, а httpx
по http её не сохранит (тестовый клиент ходит на http://test)."""
import subprocess
import sys

import httpx
import pytest

from core import BaseModule, Bus, Runner
from core.db import Database
from modules.auth import AuthModule
from modules.gateway import GatewayModule


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    return str(path)


class _Queue(BaseModule):
    """Фейк torrents.queue.get — защищённый бизнес-роут /api/agent/queue."""
    name = "q"

    def register(self):
        self.handle("torrents.queue.get", self._q)

    async def _q(self, env):
        return {"count": 0, "tasks": []}


@pytest.fixture
async def system(db_path, tmp_path, monkeypatch):
    # первичная учётка создаётся из окружения при старте (bootstrap)
    monkeypatch.setenv("ST_ADMIN_USER", "admin")
    monkeypatch.setenv("ST_ADMIN_PASSWORD", "s3cret")
    bus = Bus()
    db = Database(db_path)
    gateway = GatewayModule(bus, static_dir=str(tmp_path),
                            templates_dir=str(tmp_path), cookie_secure=False)
    runner = Runner(bus, [gateway, AuthModule(bus, db), _Queue(bus)])
    await runner.start()
    transport = httpx.ASGITransport(app=gateway.app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        yield bus, db, client
    await runner.stop()


@pytest.mark.asyncio
async def test_api_locked_without_session(system):
    """Замок: защищённый бизнес-роут без входа → 401."""
    _, _, client = system
    r = await client.get("/api/agent/queue")
    assert r.status_code == 401
    assert r.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_login_page_endpoint_is_public(system):
    """/api/login доступен без сессии (иначе войти было бы нельзя)."""
    _, _, client = system
    r = await client.post("/api/login",
                          json={"username": "x", "password": "y"})
    assert r.status_code == 401          # неверные креды, но эндпоинт доступен
    assert r.json()["success"] is False


@pytest.mark.asyncio
async def test_login_success_opens_api(system):
    """Верный вход ставит сессию → защищённый роут доступен, /api/me знает юзера."""
    _, _, client = system
    r = await client.post("/api/login",
                          json={"username": "admin", "password": "s3cret"})
    assert r.status_code == 200 and r.json()["success"] is True
    r2 = await client.get("/api/agent/queue")
    assert r2.status_code == 200
    me = await client.get("/api/me")
    assert me.status_code == 200
    assert me.json() == {"authenticated": True, "username": "admin"}
    # статус отдаёт имя вошедшего — фронт восстанавливает кнопку выхода
    st = await client.get("/api/auth/status")
    assert st.json() == {"configured": True, "authenticated": True,
                         "username": "admin"}


@pytest.mark.asyncio
async def test_logout_clears_session(system):
    _, _, client = system
    await client.post("/api/login",
                      json={"username": "admin", "password": "s3cret"})
    assert (await client.post("/api/logout")).status_code == 200
    assert (await client.get("/api/agent/queue")).status_code == 401


@pytest.mark.asyncio
async def test_wrong_password_rejected(system):
    _, _, client = system
    r = await client.post("/api/login",
                          json={"username": "admin", "password": "WRONG"})
    assert r.status_code == 401
    # сессия не выдана
    assert (await client.get("/api/agent/queue")).status_code == 401


@pytest.mark.asyncio
async def test_unknown_user_rejected(system):
    _, _, client = system
    r = await client.post("/api/login",
                          json={"username": "ghost", "password": "s3cret"})
    assert r.status_code == 401
    assert r.json()["success"] is False


@pytest.mark.asyncio
async def test_bruteforce_ban_after_5_fails(system):
    """5 неудач с одного адреса → временный бан (429) даже при верном пароле."""
    _, _, client = system
    for _ in range(5):
        r = await client.post("/api/login",
                              json={"username": "admin", "password": "WRONG"})
        assert r.status_code == 401
    r = await client.post("/api/login",
                          json={"username": "admin", "password": "s3cret"})
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_setup_first_run(db_path, tmp_path, monkeypatch):
    """Первый запуск (админа нет): /api/setup создаёт его и сразу логинит;
    короткий пароль отклоняется; повторный setup запрещён (403)."""
    monkeypatch.delenv("ST_ADMIN_USER", raising=False)
    monkeypatch.delenv("ST_ADMIN_PASSWORD", raising=False)
    bus = Bus()
    db = Database(db_path)
    gateway = GatewayModule(bus, static_dir=str(tmp_path),
                            templates_dir=str(tmp_path), cookie_secure=False)
    runner = Runner(bus, [gateway, AuthModule(bus, db), _Queue(bus)])
    await runner.start()
    transport = httpx.ASGITransport(app=gateway.app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        # админа нет
        assert (await client.get("/api/auth/status")).json() == {
            "configured": False, "authenticated": False, "username": ""}
        # короткий пароль отклоняется, админ не создан
        r = await client.post("/api/setup",
                              json={"username": "boss", "password": "short"})
        assert r.status_code == 400
        # нормальный setup создаёт админа и сразу логинит (сессия)
        r = await client.post("/api/setup",
                              json={"username": "boss", "password": "longpass123"})
        assert r.status_code == 200 and r.json()["success"] is True
        assert (await client.get("/api/agent/queue")).status_code == 200
        assert (await client.get("/api/auth/status")).json()["configured"] is True
        # повторный setup запрещён
        r2 = await client.post("/api/setup",
                               json={"username": "x", "password": "longpass123"})
        assert r2.status_code == 403
    await runner.stop()


@pytest.mark.asyncio
async def test_auth_module_queries(system):
    """Модуль auth: exists/password.set по шине (без HTTP)."""
    bus, _, _ = system

    class Probe(BaseModule):
        name = "probe"
    probe = Probe(bus)
    await probe.start()
    try:
        assert (await probe.request("auth.exists", {}))["exists"] is True
        # смена пароля → старый перестаёт подходить, новый подходит
        await probe.request("auth.password.set",
                            {"username": "admin", "password": "new-pass"})
        assert (await probe.request(
            "auth.login", {"username": "admin", "password": "s3cret"}))["ok"] is False
        assert (await probe.request(
            "auth.login", {"username": "admin", "password": "new-pass"}))["ok"] is True
    finally:
        await probe.stop()
