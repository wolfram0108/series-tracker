"""Интеграционные тесты модуля torrents против СТЕНДОВОГО qBittorrent.

Гоняются только при заданном окружении (стенд — наш, прод не участвует):
    ST_QBIT_URL=http://series-tracker:8080 \
    ST_QBIT_USER=admin ST_QBIT_PASS=... pytest tests/test_torrents_integration.py

Сценарий: добавить реальный .torrent на паузе → hash совпал с локально
вычисленным кандидатом → файлы читаются → пауза/удаление с файлами.
Проверяется и гибрид v2: qBittorrent обязан ключевать его укороченным
SHA256 (открытие из revision.md Р-2).
"""
import json
import os

import pytest

from core import Bus, Runner
from modules.torrents import TorrentsModule, infohash_candidates

QBIT_URL = os.environ.get("ST_QBIT_URL")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "torrents")

pytestmark = pytest.mark.skipif(
    not QBIT_URL, reason="ST_QBIT_URL не задан — интеграция со стендом выключена")


@pytest.fixture
async def torrents_module(tmp_path):
    import subprocess
    import sys
    bus = Bus()
    db_file = tmp_path / "t.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{db_file}",
                        "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    from core.db import Database
    module = TorrentsModule(
        bus, Database(str(db_file)),
        qbt_url=QBIT_URL,
        qbt_username=os.environ.get("ST_QBIT_USER", "admin"),
        qbt_password=os.environ["ST_QBIT_PASS"],
    )

    # клиентский модуль для запросов через шину
    from core import BaseModule

    class Probe(BaseModule):
        name = "probe"

    probe = Probe(bus)
    runner = Runner(bus, [module, probe])
    await runner.start()
    yield probe
    await runner.stop()


def _fixtures() -> dict:
    expected = json.load(open(os.path.join(FIXTURES, "expected_hashes.json")))
    return {f: h.lower() for f, h in expected.items()}


@pytest.mark.asyncio
async def test_add_inspect_delete_roundtrip(torrents_module):
    probe = torrents_module
    save_path = "/downloads/it-test"

    for fname, expected_hash in _fixtures().items():
        content = open(os.path.join(FIXTURES, fname), "rb").read()
        assert expected_hash in infohash_candidates(content)

        import base64
        reply = await probe.request("torrents.add", {
            "content_b64": base64.b64encode(content).decode(),
            "save_path": save_path, "paused": True}, timeout=30)
        assert reply["hash"] == expected_hash, \
            f"{fname}: qBit ключует {reply['hash']}, ожидался {expected_hash}"

        files = await probe.request("torrents.files.get",
                                    {"hash": expected_hash}, timeout=15)
        assert files, f"{fname}: пустой список файлов"

        # повторное добавление: должно вернуть existed=True и тот же hash
        reply2 = await probe.request("torrents.add", {
            "content_b64": base64.b64encode(content).decode(),
            "save_path": save_path, "paused": True}, timeout=30)
        assert reply2["hash"] == expected_hash
        assert reply2["existed"] is True

    # уборка: всё добавленное удаляем вместе с файлами
    await probe.request("torrents.info.get", {}, timeout=15)
    probe.send_command("torrents.delete",
                       {"hashes": list(_fixtures().values()),
                        "delete_files": True})
    import asyncio
    await asyncio.sleep(1)
    info = await probe.request("torrents.info.get",
                               {"hashes": list(_fixtures().values())},
                               timeout=15)
    assert info == [], "тестовые торренты не удалились"
