"""Тесты входа администратора (Этап 1, docs/security.md): модуль auth +
HTTP-замок, сессия в куке, защита от перебора.

cookie_secure=False — иначе SessionMiddleware ставит Secure-куку, а httpx
по http её не сохранит (тестовый клиент ходит на http://test)."""

import httpx
import pytest

from core import BaseModule, Bus, Runner
from core.db import Database
from modules.auth import AuthModule
from modules.gateway import GatewayModule



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
            "configured": False, "authenticated": False,
            "username": "", "admin": ""}
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
async def test_change_password_flow(system):
    """Вошедший меняет пароль без подтверждения старого (активная сессия —
    достаточное подтверждение): короткий новый — отказ; нормальный — успех,
    после чего работает только новый пароль."""
    _, _, client = system
    await client.post("/api/login",
                      json={"username": "admin", "password": "s3cret"})
    # слишком короткий новый → 400
    r = await client.post("/api/auth/password", json={"new": "short"})
    assert r.status_code == 400
    # нормальная смена → 200, сессия осталась валидной
    r = await client.post("/api/auth/password", json={"new": "longnewpass"})
    assert r.status_code == 200 and r.json()["success"] is True
    assert (await client.get("/api/agent/queue")).status_code == 200
    # после выхода старый пароль не подходит, новый — подходит
    await client.post("/api/logout")
    assert (await client.post(
        "/api/login",
        json={"username": "admin", "password": "s3cret"})).status_code == 401
    assert (await client.post(
        "/api/login",
        json={"username": "admin", "password": "longnewpass"})).status_code == 200
